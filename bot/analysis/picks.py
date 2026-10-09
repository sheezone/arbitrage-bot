"""Ready-made picks and expresses, built only from real AI analyses (bot/analysis/ai.py).

A background job analyses the day's top matches on its own so the "Готовые прогнозы"
feed and the expresses fill up without anyone tapping a match first. Expresses combine
2-4 of those picks (different matches, confidence medium/high) into total odds 2-6; the
express is exactly as good as its legs -- no extra "AI selection" claim beyond that.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from itertools import combinations

from bot.analysis.ai import Analyzer
from bot.analysis.catalog import SPORTS, get_full_line
from bot.analysis.results import predicted_outcome
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
# Daily auto-analysis of the most popular matches in every sport (owner 2026-10-03):
# per-sport quota for matches in the next 24h; popularity = top league (football) and
# market depth (how many lines the bookmaker offers -- big matches get the most).
SPORT_QUOTA = {"football": 8, "hockey": 4, "basketball": 4, "tennis": 4, "esports": 3,
               "table_tennis": 2, "volleyball": 3, "combat": 4}
DAILY_TARGET = sum(SPORT_QUOTA.values())
PER_RUN = 6               # model calls per job run (spreads cost/latency over the day)
RUN_EVERY_S = 3600
ACTIVE_HOURS = range(8, 23)
EXPRESS_LEG_ODDS = (1.3, 2.4)
EXPRESS_TOTAL = (2.0, 6.0)
EXPRESS_COUNT = 3


def pick_from_row(row) -> dict:
    payload = json.loads(row["payload"])
    return {
        "match_id": row["match_id"],
        "sport": row["sport"] if "sport" in row.keys() else "football",
        "team_a": row["team_a"],
        "team_b": row["team_b"],
        "league": row["league"],
        "start_utc": row["start_utc"],
        "label": row["option_label"],
        "odds": row["odds"],
        "confidence": row["confidence"],
        "summary": payload.get("summary", ""),
        "reasoning": payload.get("reasoning", ""),
        "result": row["result"],
        "predicted": predicted_outcome(row["payload"]),
        "probabilities": payload.get("probabilities"),
        "winner_result": row["winner_result"] if "winner_result" in row.keys() else None,
        # What actually counts as a hit for THIS pick (owner 2026-10-09): a win/1X2
        # pick is judged by the predicted winner, a totals pick by the total itself --
        # matches Repository._STAT_HIT_SQL, so the app shows the same verdict it's
        # counted by in the public stats.
        "stat_result": (row["winner_result"] if row["option_kind"] in ("1x2", "winner") else row["result"])
                       if "winner_result" in row.keys() else row["result"],
        "score": row["score"],
    }


def upcoming_picks(repo: Repository, now: datetime | None = None, hours: int = 48) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = repo.ai_analyses_between(now.isoformat(), (now + timedelta(hours=hours)).isoformat())
    return [pick_from_row(r) for r in rows]


def build_expresses(picks: list[dict], count: int = EXPRESS_COUNT, exclude: frozenset[str] = frozenset()) -> list[dict]:
    legs = [p for p in picks if p["confidence"] in ("средняя", "высокая", "очень высокая")
            and EXPRESS_LEG_ODDS[0] <= p["odds"] <= EXPRESS_LEG_ODDS[1]]
    rank = {"очень высокая": 0, "высокая": 1, "средняя": 2}
    legs.sort(key=lambda p: (rank.get(p["confidence"], 3), p["odds"]))
    out: list[dict] = []
    used: set[str] = set()
    for size in (3, 2, 4):
        for combo in combinations(legs, size):
            ids = {p["match_id"] for p in combo}
            if ids & used or len(ids) < size or "|".join(sorted(ids)) in exclude:
                continue
            total = 1.0
            for p in combo:
                total *= p["odds"]
            if EXPRESS_TOTAL[0] <= total <= EXPRESS_TOTAL[1]:
                out.append({"legs": list(combo), "total_odds": round(total, 2)})
                used |= ids
            if len(out) >= count:
                return out
    return out


def express_key(express: dict) -> str:
    return "|".join(sorted(p["match_id"] for p in express["legs"]))


def express_message(express: dict, full: bool) -> str:
    """Bot chat text for a freshly built express (full for MAX, teaser otherwise)."""
    import html

    if not full:
        return (f"🔥 <b>Экспресс дня готов!</b>\n\n{len(express['legs'])} матча, общий кэф "
                f"<b>{express['total_odds']:.2f}</b>. Ставки и обоснование — в MAX-доступе.")
    lines = [f"🔥 <b>Ваш экспресс готов!</b> Общий кэф <b>{express['total_odds']:.2f}</b>", ""]
    for i, p in enumerate(express["legs"], 1):
        start = datetime.fromisoformat(p["start_utc"]).astimezone(MSK).strftime("%d.%m %H:%M")
        emoji = SPORTS.get(p.get("sport", "football"), {}).get("emoji", "⚽")
        lines.append(f"{i}. {emoji} <b>{html.escape(p['team_a'])} — {html.escape(p['team_b'])}</b> · {start} МСК")
        lines.append(f"   🎯 {html.escape(p['label'])} @ <b>{p['odds']:.2f}</b> · уверенность: {html.escape(p['confidence'])}")
    lines += ["", "Экспресс проигрывает, если не зашла хоть одна ставка. Аналитика, а не гарантия. 18+"]
    return "\n".join(lines)


async def push_new_expresses(repo: Repository, bot, webapp_url: str, admin_chat_ids: frozenset[int]) -> int:
    """Send every not-yet-sent express: full to users with access, a once-a-day teaser to
    the rest. Each express goes out once (sent_expresses)."""
    from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    from bot.core import billing

    now = datetime.now(timezone.utc)
    if now.astimezone(MSK).hour not in ACTIVE_HOURS:
        return 0  # nobody wants a push at 3 a.m.
    fresh = [e for e in build_expresses(upcoming_picks(repo, now)) if not repo.express_sent(express_key(e))]
    if not fresh:
        return 0
    day_start = now.astimezone(MSK).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    teaser_today = repo.expresses_sent_since(day_start.isoformat()) == 0
    button = [[InlineKeyboardButton(text="📡 Открыть в Матч Радаре", web_app=WebAppInfo(url=webapp_url))]] if webapp_url else []
    sent = 0
    for express in fresh:
        repo.mark_express_sent(express_key(express))
        for user in repo.get_all_users():
            full = billing.has_access(user, now, admin_chat_ids)
            if not full and not teaser_today:
                continue
            try:
                await bot.send_message(
                    user.chat_id, express_message(express, full), parse_mode="HTML",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=button) if button else None,
                    disable_notification=user.muted,
                )
                sent += 1
            except Exception:
                pass  # blocked the bot / deleted account
            await asyncio.sleep(0.05)
        teaser_today = False  # at most one teaser a day for non-subscribers
    return sent


def popular_to_analyse(matches, have: set[str], now: datetime) -> list:
    """Most popular not-yet-analysed matches of the next 24h, filling each sport's quota."""
    soon = [m for m in matches if now < m.start_utc <= now + timedelta(hours=24) and m.options]
    out = []
    for sport, quota in SPORT_QUOTA.items():
        pool = [m for m in soon if m.sport == sport]
        done = sum(m.id in have for m in pool)
        pool = [m for m in pool if m.id not in have]
        pool.sort(key=lambda m: (m.priority, -len(m.options), m.start_utc))
        out += pool[:max(0, quota - done)]
    # earliest kick-offs first, so nothing starts before it gets analysed
    return sorted(out, key=lambda m: m.start_utc)


FIGHT_HIGHLIGHT_SPORTS = ("combat",)
FIGHT_HIGHLIGHT_EVERY = timedelta(days=3)  # owner 2026-10-07: "редко пость самую интересную инфу"


def fight_highlight_message(pick: dict) -> str:
    import html

    emoji = SPORTS.get(pick["sport"], {}).get("emoji", "🥊")
    start = datetime.fromisoformat(pick["start_utc"]).astimezone(MSK).strftime("%d.%m %H:%M")
    lines = [
        f"{emoji} <b>Большой бой на подходе!</b>", "",
        f"<b>{html.escape(pick['team_a'])} — {html.escape(pick['team_b'])}</b>",
        f"🏆 {html.escape(pick['league'])} · {start} МСК",
    ]
    if pick.get("summary"):
        lines += ["", html.escape(pick["summary"])]
    lines += ["", f"🧠 Прогноз ИИ: {html.escape(pick['label'])} · уверенность {pick['confidence']}",
              "", "⚠️ Это только анализ ИИ, а не гарантия результата. 18+"]
    return "\n".join(lines)


async def post_fight_highlight(repo: Repository, bot, chat_id: int, now: datetime) -> bool:
    """Rarely (at most once every FIGHT_HIGHLIGHT_EVERY) posts the single most confident
    upcoming MMA/boxing pick -- among popular (is_popular) matches -- to the news
    channel, so a big UFC/boxing card gets a heads-up there without turning the channel
    into a combat-sports feed."""
    if repo.fight_highlights_since((now - FIGHT_HIGHLIGHT_EVERY).isoformat()):
        return False
    rank = {"очень высокая": 0, "высокая": 1, "средняя": 2}
    candidates = [p for p in upcoming_picks(repo, now, hours=72)
                 if p["sport"] in FIGHT_HIGHLIGHT_SPORTS and p["confidence"] in rank
                 and not repo.fight_highlight_posted(p["match_id"])]
    if not candidates:
        return False
    candidates.sort(key=lambda p: (rank[p["confidence"]], p["start_utc"]))
    pick = candidates[0]
    try:
        await bot.send_message(chat_id, fight_highlight_message(pick), parse_mode="HTML")
    except Exception:
        logger.exception("Fight highlight post failed")
        return False
    repo.mark_fight_highlight_posted(pick["match_id"])
    return True


async def run_daily_picks(analyzer: Analyzer, repo: Repository, bot=None, webapp_url: str = "",
                          admin_chat_ids: frozenset[int] = frozenset(), news_chat_id: int | None = None) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.astimezone(MSK).hour in ACTIVE_HOURS:
                have = {p["match_id"] for p in upcoming_picks(repo, now, hours=24)}
                for match in popular_to_analyse(await get_full_line(), have, now)[:PER_RUN]:
                    try:
                        await analyzer.analyze(match)
                    except Exception:
                        logger.exception("Daily analysis failed for %s", match.id)
            if bot is not None:
                n = await push_new_expresses(repo, bot, webapp_url, admin_chat_ids)
                if n:
                    logger.info("Express pushed to %s chats", n)
                if news_chat_id is not None and await post_fight_highlight(repo, bot, news_chat_id, now):
                    logger.info("Fight highlight posted to the channel")
        except Exception:
            logger.exception("Daily AI picks run failed")
        await asyncio.sleep(RUN_EVERY_S)
