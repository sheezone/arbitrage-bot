"""One-off: grant +2 free MAX days to every user and broadcast the "мощное обновление"
announcement to their bot DM (owner 2026-10-10). Run once, on the server, from the repo
root: `venv/bin/python scripts/broadcast_update2.py`.
"""
from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from aiogram.types import BufferedInputFile, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from dotenv import load_dotenv

from bot.db.repository import Repository

load_dotenv()

CAPTION = """📡 <b>Матч Радар стал мощнее!</b>

Мы сильно обновили бота: больше видов спорта, точнее разборы, быстрее экспрессы.

🎁 В честь этого — <b>2 дня MAX всем пользователям бесплатно</b>, уже начислено на ваш аккаунт.

Внутри MAX:
🧠 Безлимитный разбор матчей по 9 видам спорта — футбол, хоккей, баскетбол, теннис, киберспорт, волейбол, настольный теннис, бокс, MMA
🎯 Готовые прогнозы и экспрессы дня
🏁 Итог ИИ — кто победит и с каким %

Открывайте приложение и пользуйтесь, пока подарок активен."""


async def main() -> None:
    repo = Repository(os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"))
    bot = Bot(token=os.environ["BOT_TOKEN"])
    webapp_url = os.environ.get("WEBAPP_URL", "").rstrip("/")
    markup = (
        InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📡 Открыть Матч Радар", web_app=WebAppInfo(url=webapp_url))]])
        if webapp_url else None
    )
    photo = open("bot/assets/update2_banner.png", "rb").read()

    users = repo.get_all_users()
    granted = sent = failed = 0
    for user in users:
        try:
            repo.grant_bonus_days(user.chat_id, 2)
            granted += 1
        except Exception as e:
            print("grant failed", user.chat_id, e)
        try:
            await bot.send_photo(
                user.chat_id, BufferedInputFile(photo, "update2.png"),
                caption=CAPTION, parse_mode="HTML", reply_markup=markup,
                disable_notification=user.muted,
            )
            sent += 1
        except Exception as e:
            failed += 1
            print("send failed", user.chat_id, type(e).__name__)
        await asyncio.sleep(0.05)  # ~20/s, under Telegram's broadcast rate limit

    print(f"granted={granted} sent={sent} failed={failed} total={len(users)}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
