"""One-off: send a "привет" from the bot to every chat in ADMIN_CHAT_IDS. Run on the
server, from the repo root: `venv/bin/python scripts/hello_admins.py`.
"""
from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from dotenv import load_dotenv

load_dotenv()

TEXT = "👋 <b>Привет, админы!</b>\n\nЭто бот Матч Радар — я на связи и работаю."


async def main() -> None:
    admin_ids = [int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").split(",") if x.strip()]
    if not admin_ids:
        print("ADMIN_CHAT_IDS is empty -- nobody to greet")
        return
    bot = Bot(token=os.environ["BOT_TOKEN"])
    sent = failed = 0
    for chat_id in admin_ids:
        try:
            await bot.send_message(chat_id, TEXT, parse_mode="HTML")
            sent += 1
        except Exception as e:
            failed += 1
            print("send failed", chat_id, type(e).__name__)
    print(f"sent={sent} failed={failed} total={len(admin_ids)}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
