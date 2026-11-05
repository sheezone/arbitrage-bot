"""One-off (owner 2026-11-06): fill user_names for existing users via getChat, so the
admin panel shows @username / names right away. New names are saved on /start and
on every Mini App request. Run on the server: `venv/bin/python -m scripts.backfill_user_names`.
"""
from __future__ import annotations

import asyncio
import os

from aiogram import Bot
from dotenv import load_dotenv

from bot.db.repository import Repository

load_dotenv()


async def main() -> None:
    repo = Repository(os.environ.get("DB_PATH", "arbitrage_bot.sqlite3"))
    bot = Bot(token=os.environ["BOT_TOKEN"])
    ok = failed = 0
    for user in repo.get_all_users():
        try:
            chat = await bot.get_chat(user.chat_id)
            name = " ".join(p for p in (chat.first_name, chat.last_name) if p)
            repo.save_user_name(user.chat_id, chat.username, name)
            ok += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)
    print(f"names saved={ok} failed={failed}")
    await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
