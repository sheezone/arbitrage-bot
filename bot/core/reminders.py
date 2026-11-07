"""Bot reminders (owner 2026-11-07):

- trial ending: ~4 h before the free MAX day runs out, with the first-purchase price;
  a paid subscription gets the same nudge 24 h before it ends. (The old reminder lived
  in the vilki monitor loop, which no longer runs.)
- match in an hour: for every match a user analysed, ~60 min before kick-off, with a
  button to open the detailed pre-match analysis (confirmed lineups, referee).

Both are de-duplicated (users.expiry_reminder_sent_for / alerts table), so a restart
never sends anything twice.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

from bot.core import billing
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

CHECK_EVERY = 300  # s
TRIAL_WARN = timedelta(hours=4)
SUB_WARN = timedelta(hours=24)
MATCH_WARN_FROM, MATCH_WARN_TO = timedelta(minutes=45), timedelta(minutes=70)
MSK = timezone(timedelta(hours=3))


def _kb(webapp_url: str, text: str) -> InlineKeyboardMarkup | None:
    if not webapp_url:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=text, web_app=WebAppInfo(url=webapp_url))]])


async def _expiry(repo: Repository, bot: Bot, admins: frozenset[int], webapp_url: str) -> None:
    now = datetime.now(timezone.utc)
    for user in repo.get_all_users():
        if billing.is_admin(user, admins):
            continue
        end = billing.access_end(user, now)
        if end <= now:
            continue
        trial = billing.on_trial(user, now)
        if end - now > (TRIAL_WARN if trial else SUB_WARN):
            continue
        key = end.isoformat()
        if user.expiry_reminder_sent_for == key:
            continue
        left = max(1, round((end - now).total_seconds() / 3600))
        if trial:
            price = billing.FIRST_PLAN.price_rub if not repo.has_paid(user.chat_id) else billing.PLANS[0].price_rub
            text = (f"⏳ <b>Бесплатный MAX закончится через {left} ч</b>\n\n"
                    "После этого разборы матчей будут доступны только по подписке.\n"
                    f"Оформите MAX — первая неделя всего за <b>{price} ₽</b>: до 5 разборов в день, "
                    "ставка ИИ и подробный разбор за час до матча.")
        else:
            text = (f"⏳ <b>Подписка MAX закончится через {left} ч</b>\n\n"
                    "Продлите её, чтобы не потерять разборы матчей и ставки ИИ.")
        try:
            await bot.send_message(user.chat_id, text, parse_mode="HTML", disable_notification=user.muted,
                                   reply_markup=_kb(webapp_url, "💎 Оформить MAX"))
        except Exception as e:
            logger.info("expiry reminder to %s failed: %s", user.chat_id, type(e).__name__)
        repo.set_expiry_reminder_sent(user.chat_id, key)  # also on failure: a blocked bot stays blocked
        await asyncio.sleep(0.05)


async def _pre_match(repo: Repository, bot: Bot, admins: frozenset[int], webapp_url: str) -> None:
    now = datetime.now(timezone.utc)
    rows = repo._conn.execute(
        "SELECT DISTINCT u.chat_id, a.match_id, a.team_a, a.team_b, a.league, a.start_utc FROM ai_usage u "
        "JOIN ai_analyses a ON a.match_id = u.match_id WHERE a.start_utc > ? AND a.start_utc <= ?",
        ((now + MATCH_WARN_FROM).isoformat(), (now + MATCH_WARN_TO).isoformat()),
    ).fetchall()
    for r in rows:
        key = f"prematch_{r['chat_id']}_{r['match_id']}"
        if repo.alert_sent(key):
            continue
        repo.mark_alert_sent(key)
        user = repo.get_user(r["chat_id"])
        if user is None:
            continue
        start = datetime.fromisoformat(r["start_utc"])
        mins = max(1, round((start - now).total_seconds() / 60))
        access = billing.has_access(user, now, admins)
        text = (f"⏰ <b>Через {mins} мин:</b> {r['team_a']} — {r['team_b']}\n"
                f"{r['league'] or ''} · {start.astimezone(MSK):%H:%M} МСК\n\n"
                + ("Открыт <b>подробный разбор за час до матча</b>: подтверждённые составы, судья и "
                   "движение линии. Загляните перед ставкой 👇" if access else
                   "Подробный разбор за час до матча (составы, судья, движение линии) — в MAX 👇"))
        try:
            await bot.send_message(r["chat_id"], text, parse_mode="HTML", disable_notification=user.muted,
                                   reply_markup=_kb(webapp_url, "📡 Открыть разбор" if access else "💎 Оформить MAX"))
        except Exception as e:
            logger.info("pre-match reminder to %s failed: %s", r["chat_id"], type(e).__name__)
        await asyncio.sleep(0.05)


async def run_reminders(repo: Repository, bot: Bot, admins: frozenset[int], webapp_url: str) -> None:
    while True:
        for job in (_expiry, _pre_match):
            try:
                await job(repo, bot, admins, webapp_url)
            except Exception:
                logger.exception("reminder job %s failed", job.__name__)
        await asyncio.sleep(CHECK_EVERY)
