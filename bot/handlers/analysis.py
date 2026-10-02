"""«🧠 ИИ-анализ» -- the bot's main section since 2026-10-02.

Bottom-keyboard button -> list of upcoming football matches (top leagues first) -> tap a
match -> Claude's analysis with one pick from the real line (bot/analysis/ai.py).
Daily limits: AI_FREE_PER_DAY distinct matches without a subscription, AI_PAID_PER_DAY
with one; re-opening a match already analysed today is free. Admins are unlimited.
"""
from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from bot.analysis.ai import Analyzer, hit_rate_line
from bot.analysis.catalog import FootballMatch, find_match, get_catalog
from bot.core import billing
from bot.core.emoji import button_texts, vi
from bot.core.flags import with_flag
from bot.db.repository import Repository
from bot.handlers.commands import AI_BUTTON_TEXT, _btn, _render

AI_BUTTON_TEXTS = tuple(AI_BUTTON_TEXT.values())
AI_FREE_PER_DAY = 1
AI_PAID_PER_DAY = 15
PAGE_SIZE = 10
MSK = timezone(timedelta(hours=3))
CB_LIST = "ai:list:"
CB_MATCH = "ai:m:"


def _when(match: FootballMatch) -> str:
    local = match.start_utc.astimezone(MSK)
    today = datetime.now(MSK).date()
    day = "сегодня" if local.date() == today else "завтра" if local.date() == today + timedelta(days=1) else local.strftime("%d.%m")
    return f"{day} {local:%H:%M}"


def list_view(matches: list[FootballMatch], page: int, hit_rate: str) -> tuple[str, InlineKeyboardMarkup]:
    pages = max(1, (len(matches) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(page, 0), pages - 1)
    chunk = matches[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    text = (
        f"🧠 <b>ИИ-АНАЛИЗ МАТЧЕЙ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Выберите матч — ИИ разберёт форму команд, личные встречи, новости и линию букмекеров "
        "и предложит одну ставку с объяснением.\n\n"
        f"{hit_rate}\n\n"
        f"{vi('warning')} Аналитика, а не гарантия. 18+."
    )
    if not matches:
        text += "\n\nСейчас нет ближайших матчей в линии — загляните позже."
    rows = [[_btn(f"⚽ {_when(m)} · {m.team_a} — {m.team_b}"[:60], f"{CB_MATCH}{m.id}")] for m in chunk]
    nav = []
    if page > 0:
        nav.append(_btn("◀️", f"{CB_LIST}{page - 1}"))
    if page < pages - 1:
        nav.append(_btn("▶️", f"{CB_LIST}{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([_btn("🔄 Обновить", f"{CB_LIST}{page}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def analysis_view(match: FootballMatch, result: dict, hit_rate: str, back_page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    pick = result["pick"]
    lines = [
        "🧠 <b>ИИ-АНАЛИЗ</b>",
        f"⚽ <b>{html.escape(with_flag(match.team_a))}</b> — <b>{html.escape(with_flag(match.team_b))}</b>",
        f"🏆 {html.escape(match.league)}",
        f"{vi('hourglass')} {_when(match)} МСК",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
        f"📋 {html.escape(result['summary'])}",
        "",
        "<b>🔎 Ключевые факторы</b>",
    ]
    for f in result["factors"][:5]:
        lines.append(f"• <b>{html.escape(f['title'])}:</b> {html.escape(f['text'])}")
    lines += [
        "",
        f"🎯 <b>Прогноз: {html.escape(pick['label'])} @ {pick['odds']:.2f}</b>",
        f"Уверенность: <b>{html.escape(result['confidence'])}</b>",
        f"<i>{html.escape(result['reasoning'])}</i>",
        "",
        f"{vi('warning')} <b>Риски:</b> {html.escape(result['risks'])}",
        "",
        hit_rate,
        "",
        "Коэффициенты на момент анализа — проверяйте у букмекера. Это аналитика, а не гарантия "
        "результата. 18+. Ставки — самостоятельно у лицензированных БК.",
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=[[_btn("◀️ К списку матчей", f"{CB_LIST}{back_page}")]])
    return "\n".join(lines), kb


def register_analysis_handlers(router: Router, repo: Repository, analyzer: Analyzer | None,
                               admin_chat_ids: frozenset[int]) -> None:
    async def show_list(bot: Bot, chat_id: int, message_id: int | None, page: int, fresh: bool) -> None:
        try:
            matches = await get_catalog()
        except Exception:
            matches = []
        text, kb = list_view(matches, page, hit_rate_line(repo))
        await _render(bot, repo, chat_id, message_id, text, kb, photo_path=None, fresh=fresh)

    @router.message(F.text.in_(button_texts(AI_BUTTON_TEXTS)))
    async def on_ai_button(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        try:
            await message.delete()
        except Exception:
            pass
        user = repo.get_user(message.chat.id)
        await show_list(bot, message.chat.id, user.menu_message_id if user else None, 0, fresh=True)

    @router.callback_query(F.data.startswith(CB_LIST))
    async def on_ai_list(callback: CallbackQuery, bot: Bot) -> None:
        page = int(callback.data[len(CB_LIST):] or 0)
        await show_list(bot, callback.message.chat.id, callback.message.message_id, page, fresh=False)
        await callback.answer()

    @router.callback_query(F.data.startswith(CB_MATCH))
    async def on_ai_match(callback: CallbackQuery, bot: Bot) -> None:
        chat_id = callback.message.chat.id
        match_id = callback.data[len(CB_MATCH):]
        if analyzer is None:
            await callback.answer("ИИ-анализ временно недоступен", show_alert=True)
            return
        match = await find_match(match_id)
        if match is None:
            await callback.answer("Матч уже начался или пропал из линии", show_alert=True)
            return
        user = repo.get_user(chat_id)
        now = datetime.now(timezone.utc)
        day = now.astimezone(MSK).date().isoformat()
        used = repo.ai_usage_today(chat_id, day)
        if not billing.is_admin(user, admin_chat_ids) and match_id not in used:
            limit = AI_PAID_PER_DAY if billing.has_access(user, now, admin_chat_ids) else AI_FREE_PER_DAY
            if len(used) >= limit:
                msg = (f"Лимит на сегодня: {limit} матч(ей). " +
                       ("Оформите подписку — до 15 анализов в день." if limit == AI_FREE_PER_DAY else "Возвращайтесь завтра."))
                await callback.answer(msg, show_alert=True)
                return
        await callback.answer()
        await _render(bot, repo, chat_id, callback.message.message_id,
                      f"🧠 Анализирую <b>{html.escape(match.team_a)} — {html.escape(match.team_b)}</b>…\n"
                      "Собираю форму, личные встречи, новости и линию. Это займёт до минуты.", None)
        try:
            result = await analyzer.analyze(match)
        except Exception:
            result = None
        if result is None:
            text, kb = list_view(await get_catalog(), 0, hit_rate_line(repo))
            text = "⚠️ Не удалось разобрать этот матч, попробуйте другой.\n\n" + text
            await _render(bot, repo, chat_id, callback.message.message_id, text, kb)
            return
        repo.record_ai_usage(chat_id, day, match_id)
        text, kb = analysis_view(match, result, hit_rate_line(repo))
        await _render(bot, repo, chat_id, callback.message.message_id, text, kb)
