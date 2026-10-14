"""One-off: tell every user about the +3 free MAX days (owner 2026-10-15). The days
themselves were already granted (grant_bonus_days(.., 3) for all users) -- this only
sends the message. Run on the server: `venv/bin/python scripts/broadcast_gift3.py`.
"""
from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv

from bot.db.repository import Repository

load_dotenv()

TEXT = """🎁 <b>Дарим 3 дня MAX бесплатно!</b>

Подарок уже начислен на ваш аккаунт — ничего делать не нужно.

Что нового в Матч Радар:
🧠 ИИ стал точнее: ищет форму, травмы, составы, погоду и новости клубов по каждому матчу
⏱ Финальный разбор за час до начала — с подтверждёнными составами
🟢 Сразу видно, где ИИ уверен, а где советует пропустить
📈 Экспрессы и готовые прогнозы дня

Заходите и пользуйтесь, пока подарок активен 👇"""


async def main() -> None:
    repo = Repository(os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"))
    bot = Bot(token=os.environ["BOT_TOKEN"])
    webapp_url = os.environ.get("WEBAPP_URL", "").rstrip("/")
    markup = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📡 Открыть Матч Радар", web_app=WebAppInfo(url=webapp_url))]])
        if webapp_url else None
    )
    users = repo.get_all_users()
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
