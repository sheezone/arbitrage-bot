"""Football news channel autoposter.

Every ~50 minutes between NEWS_DAY_START and NEWS_DAY_END (Moscow time), up to
posts_per_day times a day, it:
  1. gathers fresh football items from RSS feeds (championat, sports.ru, sport-express,
     soccer.ru) and the public web previews of Telegram news channels (t.me/s/...);
  2. asks Claude to pick the single most interesting/hype item that isn't a repeat of
     anything posted recently;
  3. asks Claude to write an original post in the channel's own words, strictly from
     the item's text plus other outlets' takes on the same story. The article pages
     themselves are not fetched: championat, sport-express and soccer.ru sit behind
     "are you a robot" checks (checked 2026-09-29, not bypassed);
  4. publishes it as a photo post (the source article's photo, chosen by the owner
     2026-09-29) with a footer linking the channel and the bot.

Every source URL is recorded in news_posts so nothing is posted twice, and the last
titles are fed back to Claude so the same story from another outlet is skipped too.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import anthropic
import httpx
from aiogram import Bot
from aiogram.types import BufferedInputFile
from bs4 import BeautifulSoup

from bot.db.repository import Repository

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
# Posting window 08:00 -> 01:00 MSK (crosses midnight); the "news day" starts at 08:00.
NEWS_DAY_START, NEWS_DAY_END = 8, 1
WINDOW_HOURS = (NEWS_DAY_END - NEWS_DAY_START) % 24


def in_news_window(local: datetime) -> bool:
    return (local.hour - NEWS_DAY_START) % 24 < WINDOW_HOURS


def news_day_start(local: datetime) -> datetime:
    start = local.replace(hour=NEWS_DAY_START, minute=0, second=0, microsecond=0)
    return start if local >= start else start - timedelta(days=1)
MAX_ITEM_AGE = timedelta(hours=6)
CHECK_EVERY_S = 300
CAPTION_LIMIT = 1024
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"}

RSS_FEEDS = {
    "Чемпионат": "https://www.championat.com/rss/news/football/",
    "Sports.ru": "https://www.sports.ru/rss/rubric.xml?s=208",
    "Спорт-Экспресс": "https://www.sport-express.ru/services/materials/news/football/se/",
    "Soccer.ru": "https://www.soccer.ru/rss.xml",
}
# Public channels readable via the t.me/s/ web preview: full post text + photo.
TELEGRAM_CHANNELS = ("goalmasterlive", "sportsru", "championat", "sportexpress", "championsleague365")

PICK_SCHEMA = {
    "type": "object",
    "properties": {"index": {"type": "integer"}, "reason": {"type": "string"}},
    "required": ["index", "reason"],
    "additionalProperties": False,
}
POST_SCHEMA = {
    "type": "object",
    "properties": {
        "emoji": {"type": "string"},
        "headline": {"type": "string"},
        "paragraphs": {"type": "array", "items": {"type": "string"}},
        "question": {"type": "string"},
        "skip": {"type": "boolean"},
    },
    "required": ["emoji", "headline", "paragraphs", "question", "skip"],
    "additionalProperties": False,
}

PICK_SYSTEM = """Ты — главный редактор популярного русскоязычного Telegram-канала о футболе.
Из списка свежих новостей выбери ОДНУ, которая наберёт больше всего реакций: громкие
трансферы и слухи о топ-клубах и звёздах, скандалы, заявления тренеров и игроков,
яркие результаты и рекорды, сборная России и РПЛ, необычные истории. Не бери скучные
технические новости, анонсы трансляций, ставки и прогнозы, а также всё, что по сути
повторяет недавно опубликованное (список дан). Только футбол — другие виды спорта
не бери. Если достойных нет — верни index = -1."""

WRITE_SYSTEM = """Ты пишешь посты для русскоязычного Telegram-канала о футболе. Стиль —
живой, короткий, цепляющий, как у топовых футбольных каналов.

Правила:
- Только факты из присланного материала. Ничего не выдумывай: ни цифр, ни цитат, ни
  подробностей. Цитаты можно приводить только дословно из материала.
- Пиши своими словами, не копируй фразы исходника целиком (кроме цитат).
- headline — одна яркая фраза-заголовок (без эмодзи), до 120 символов.
- emoji — один подходящий эмодзи для начала поста (💥, 🔥, ⚡️, 😳, 🗣, ✍️, 🏆, 🚨 ...).
- paragraphs — 1–3 коротких абзаца, всего не больше 550 символов.
- Никогда не называй, откуда новость: ни СМИ, ни сайты, ни Telegram-каналы, ни
  инсайдеров, ни журналистов. Если это слух, а не подтверждённый факт, так и подай
  («по слухам», «сообщается», «по информации СМИ») — но без названий.
- question — короткий вопрос к подписчикам для вовлечения («Верим?», «Кто прав?») или
  пустая строка, если он неуместен.
- Без хэштегов, без ссылок, без призывов делать ставки.
- skip = true, если материал пустой, о ставках/прогнозах или не о футболе."""


@dataclass
class NewsItem:
    source: str
    url: str
    title: str
    summary: str
    published: datetime
    image: str | None = None


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", BeautifulSoup(text or "", "html.parser").get_text(" ")).strip()


def parse_rss(source: str, xml_text: str) -> list[NewsItem]:
    items: list[NewsItem] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items
    for it in root.iter("item"):
        title = _clean(it.findtext("title") or "")
        link = (it.findtext("link") or "").strip()
        if not title or not link:
            continue
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=MSK)
        image = None
        enclosure = it.find("enclosure")
        if enclosure is not None and (enclosure.get("type") or "").startswith("image"):
            image = enclosure.get("url")
        items.append(NewsItem(source, link, title, _clean(it.findtext("description") or "")[:1500], published, image))
    return items


def parse_telegram_preview(channel: str, page: str) -> list[NewsItem]:
    soup = BeautifulSoup(page, "html.parser")
    items: list[NewsItem] = []
    for msg in soup.select("div.tgme_widget_message"):
        post = msg.get("data-post")
        text_el = msg.select_one("div.tgme_widget_message_text")
        time_el = msg.select_one("time[datetime]")
        photo = msg.select_one("a.tgme_widget_message_photo_wrap")
        if not post or not text_el or not time_el or not photo:
            continue
        m = re.search(r"url\('([^']+)'\)", photo.get("style", ""))
        text = text_el.get_text("\n").strip()
        try:
            published = datetime.fromisoformat(time_el["datetime"])
        except ValueError:
            continue
        # first line with real words -- channels often open with a lone emoji/flag line
        lines = [ln.strip() for ln in text.split("\n") if len(ln.strip().split()) >= 3]
        items.append(NewsItem(f"t.me/{channel}", f"https://t.me/{post}", (lines[0] if lines else text)[:200],
                              text[:1500], published, m.group(1) if m else None))
    return items


async def gather_items(client: httpx.AsyncClient) -> list[NewsItem]:
    async def rss(source, url):
        try:
            r = await client.get(url)
            return parse_rss(source, r.text) if r.status_code == 200 else []
        except httpx.HTTPError:
            logger.warning("News feed %s unavailable", source)
            return []

    async def tg(channel):
        try:
            r = await client.get(f"https://t.me/s/{channel}")
            return parse_telegram_preview(channel, r.text) if r.status_code == 200 else []
        except httpx.HTTPError:
            logger.warning("Telegram preview %s unavailable", channel)
            return []

    results = await asyncio.gather(*[rss(s, u) for s, u in RSS_FEEDS.items()], *[tg(c) for c in TELEGRAM_CHANNELS])
    now = datetime.now(timezone.utc)
    return [i for batch in results for i in batch if now - i.published <= MAX_ITEM_AGE]


def related_texts(item: NewsItem, items: list[NewsItem]) -> list[str]:
    """Other outlets' takes on the same story (>= 2 shared capitalised words in the
    title): more facts for the writer, less temptation to pad."""
    def keys(text: str) -> set[str]:
        return {w.lower() for w in re.findall(r"[А-ЯЁA-Z][а-яёa-z]{3,}", text)}

    mine = keys(item.title)
    return [f"[{o.source}] {o.title}. {o.summary[:600]}" for o in items
            if o is not item and len(mine & keys(o.title)) >= 2][:4]


def _is_branded(item: NewsItem) -> bool:
    # Telegram channels post designed cards with their logo/text baked into the
    # picture (checked 2026-10-01: championat, sportsru, sportexpress all do); the
    # sites' RSS photos are clean. Watermarks are never edited out -- a branded item
    # instead borrows a clean RSS photo of the same story, or isn't used at all.
    return item.source.startswith("t.me/")


def with_clean_photos(items: list[NewsItem]) -> list[NewsItem]:
    clean = [i for i in items if i.image and not _is_branded(i)]
    out: list[NewsItem] = list(clean)
    for item in items:
        if not _is_branded(item):
            continue
        twin = next((c for c in clean if len(_title_keys(item.title) & _title_keys(c.title)) >= 2), None)
        if twin is not None:
            item.image = twin.image
            out.append(item)
    return out


def _title_keys(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[А-ЯЁA-Z][а-яёa-z]{3,}", text)}


def build_caption(post: dict, channel_username: str, bot_username: str) -> str:
    parts = [f"{html.escape(post['emoji'].strip())} <b>{html.escape(post['headline'].strip())}</b>"]
    parts += [html.escape(p.strip()) for p in post["paragraphs"] if p.strip()]
    if post["question"].strip():
        parts.append(f"<b>{html.escape(post['question'].strip())}</b>")
    footer = []
    if channel_username:
        footer.append(f'⚽️ <a href="https://t.me/{channel_username}">Подписаться</a>')
    if bot_username:
        footer.append(f'<a href="https://t.me/{bot_username}">Матч Радар</a>')
    text = "\n\n".join(parts)
    tail = ("\n\n" + " · ".join(footer)) if footer else ""
    visible_tail = len(re.sub(r"<[^>]+>", "", tail))
    while len(re.sub(r"<[^>]+>", "", html.unescape(text))) + visible_tail > CAPTION_LIMIT and len(parts) > 1:
        parts.pop(-1 if len(parts) > 2 else 1)
        text = "\n\n".join(parts)
    return text + tail


class NewsPoster:
    def __init__(self, bot: Bot, repo: Repository, chat_id: int, api_key: str, *, model: str,
                 posts_per_day: int, channel_username: str, bot_username: str,
                 admin_chat_ids: frozenset[int] = frozenset()):
        self.bot, self.repo, self.chat_id = bot, repo, chat_id
        self.admin_chat_ids = admin_chat_ids
        self.model = model
        self.posts_per_day = posts_per_day
        self.channel_username, self.bot_username = channel_username, bot_username
        self.claude = anthropic.AsyncAnthropic(api_key=api_key)
        self.http = httpx.AsyncClient(timeout=25, follow_redirects=True, headers=UA)
        # not right away: every deploy restarts the bot, which must not trigger a post
        self.next_post_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    async def _ask(self, system: str, content: str, schema: dict) -> dict | None:
        response = await self.claude.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        from bot.analysis.token_budget import record as _record_token_usage

        await _record_token_usage(self.repo, response, self.bot, self.admin_chat_ids)
        if response.stop_reason in ("refusal", "max_tokens"):
            logger.warning("News: Claude stopped with %s", response.stop_reason)
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)

    def _interval(self) -> timedelta:
        window_minutes = WINDOW_HOURS * 60
        base = window_minutes / max(1, self.posts_per_day)
        return timedelta(minutes=base * random.uniform(0.8, 1.15))

    async def post_one(self) -> bool:
        # photo posts only, and only clean (unbranded) photos -- see with_clean_photos
        items = [i for i in with_clean_photos(await gather_items(self.http)) if not self.repo.news_already_posted(i.url)]
        if not items:
            return False
        items.sort(key=lambda i: i.published, reverse=True)
        items = items[:60]
        recent = self.repo.recent_news_titles(40)
        listing = "\n".join(f"{n}. [{i.source}] {i.title} — {i.summary[:160]}" for n, i in enumerate(items))
        pick = await self._ask(
            PICK_SYSTEM,
            f"Недавно опубликовано у нас:\n" + "\n".join(f"- {t}" for t in recent) + f"\n\nСвежие новости:\n{listing}",
            PICK_SCHEMA,
        )
        if not pick or not (0 <= pick["index"] < len(items)):
            return False
        item = items[pick["index"]]
        material = f"Источник: {item.source}\nЗаголовок: {item.title}\n\n{item.summary}"
        related = related_texts(item, items)
        if related:
            material += "\n\nТа же история в других СМИ:\n" + "\n".join(related)
        post = await self._ask(WRITE_SYSTEM, material, POST_SCHEMA)
        self.repo.record_news_post(item.url, item.title)
        if not post or post["skip"]:
            return False
        try:
            resp = await self.http.get(item.image)
        except httpx.HTTPError:
            return False
        if resp.status_code != 200 or not resp.headers.get("content-type", "").startswith("image/"):
            logger.warning("News: image for %s not downloadable (%s)", item.url, resp.status_code)
            return False
        photo = resp.content
        caption = build_caption(post, self.channel_username, self.bot_username)
        await self.bot.send_photo(self.chat_id, BufferedInputFile(photo, "news.jpg"), caption=caption, parse_mode="HTML")
        self.repo.record_news_post(item.url, post["headline"], published=True)
        logger.info("News posted: %s (%s)", post["headline"], item.url)
        return True

    async def run(self) -> None:
        while True:
            try:
                now = datetime.now(timezone.utc)
                local = now.astimezone(MSK)
                day_start = news_day_start(local).astimezone(timezone.utc)
                in_window = in_news_window(local)
                if in_window and now >= self.next_post_at and self.repo.news_posts_since(day_start.isoformat()) < self.posts_per_day:
                    if await self.post_one():
                        self.next_post_at = now + self._interval()
                    else:
                        self.next_post_at = now + timedelta(minutes=10)
            except Exception:
                logger.exception("News poster iteration failed")
                self.next_post_at = datetime.now(timezone.utc) + timedelta(minutes=15)
            await asyncio.sleep(CHECK_EVERY_S)
