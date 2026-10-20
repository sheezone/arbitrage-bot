"""One-off: «Первая неделя 149 ₽» offer to every user WITHOUT access right now, with the
real top-match hit rate (owner 2026-10-20). Run on the server from the repo root:
`PYTHONPATH=. venv/bin/python scripts/broadcast_offer149.py`.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv

from bot.core import billing
from bot.db.repository import Repository

load_dotenv()


def text(wins: int, total: int) -> str:
    pct = round(wins / total * 100) if total else 0
    return f"""📊 <b>ИИ Матч Радар угадывает {pct}% победителей в топ-матчах</b>

{wins} из {total} — ЛЧ, топ-лиги Европы, КХЛ, НХЛ, НБА, топ-турниры CS2. Честная статистика, все прогнозы видны в приложении.

Ваш доступ к MAX закончился. Верните его:
💎 <b>Первая неделя — всего 149 ₽</b> <s>299 ₽</s>

В MAX:
🎯 Ставка ИИ и уверенность в каждом разборе
⏱ Подробный разбор за 60 минут до матча — составы, судья, движение линии
🔥 Главные матчи дня и готовые прогнозы

Окупается одной зашедшей ставкой 👇"""


async def main() -> None:
    repo = Repository(os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"))
    bot = Bot(token=os.environ["BOT_TOKEN"])
    admins = frozenset(int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip())
    webapp_url = os.environ.get("WEBAPP_URL", "").rstrip("/")
    markup = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💎 Открыть MAX за 149 ₽", web_app=WebAppInfo(url=webapp_url))]])
        if webapp_url else None
    )
    wins, total = repo.ai_hit_rate()
    msg = text(wins, total)
    now = datetime.now(timezone.utc)
    targets = [u for u in repo.get_all_users()
               if u.chat_id not in admins and not billing.has_access(u, now, admins)]
    sent = failed = 0
    for user in targets:
        try:
            await bot.send_message(user.chat_id, msg, parse_mode="HTML", reply_markup=markup,
                                   disable_notification=user.muted)
            sent += 1
        except Exception as e:
            failed += 1
            print("send failed", user.chat_id, type(e).__name__)
        await asyncio.sleep(0.05)
    print(f"stat={wins}/{total} targets={len(targets)} sent={sent} failed={failed}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
