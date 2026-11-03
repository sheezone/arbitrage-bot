"""One-off (owner 2026-11-03): the Mini App was down after a server reboot (stale tunnel
URL). Grant every user +1 free MAX day and tell them access is restored.
Run on the server: `venv/bin/python scripts/broadcast_restored1.py`.
"""
from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv

from bot.db.repository import Repository

load_dotenv()

TEXT = """✅ <b>Доступ восстановлен!</b>

Из-за технической неисправности приложение МАТЧ РАДАР какое-то время не открывалось. Всё уже работает.

🎁 В качестве извинения дарим <b>1 день MAX бесплатно</b> — он уже начислен на ваш аккаунт, ничего делать не нужно.

Спасибо, что вы с нами 👇"""


async def main() -> None:
    repo = Repository(os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"))
    bot = Bot(token=os.environ["BOT_TOKEN"])
    webapp_url = os.environ.get("WEBAPP_URL", "").rstrip("/")
    markup = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📡 Открыть МАТЧ РАДАР", web_app=WebAppInfo(url=webapp_url))]])
        if webapp_url else None
    )
    users = repo.get_all_users()
    for user in users:
        repo.grant_bonus_days(user.chat_id, 1)
    print(f"granted +1 day to {len(users)}")
    sent = failed = 0
    for user in users:
        try:
            await bot.send_message(user.chat_id, TEXT, parse_mode="HTML", reply_markup=markup,
                                   disable_notification=user.muted)
            sent += 1
        except Exception as e:
            failed += 1
            print("send failed", user.chat_id, type(e).__name__)
        await asyncio.sleep(0.05)
    print(f"sent={sent} failed={failed} total={len(users)}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
