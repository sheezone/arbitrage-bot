"""One-off: grant every existing user +BONUS_DAYS free subscription days and notify them
about it. Run manually on the server (`python -m scripts.grant_bonus_days`), separate from
the regular bot process -- NOT scheduled/repeated, since running it again just grants more
days on top rather than being a no-op.

Uses Repository.extend_subscription, which bases the new expiry on max(now, current
subscription_expires_at) -- works correctly regardless of a user's current state (still on
trial, subscription already expired, or an active paid subscription all just get a genuine
+BONUS_DAYS from wherever their access currently ends)."""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from dotenv import load_dotenv

from bot.core.monitor import _send_message_with_retries
from bot.db.repository import Repository

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BONUS_DAYS = 3
MESSAGE = (
    "🎁 <b>Подарок от нас!</b>\n\n"
    f"Всем пользователям начислено <b>+{BONUS_DAYS} бесплатных дня</b> доступа к боту — "
    "просто в благодарность за то, что вы с нами. Дни уже добавлены к вашему счёту, "
    "ничего делать не нужно."
)

# Telegram's broadcast rate limit is ~30 messages/second across all chats -- this stays
# comfortably under that without needing a token-bucket.
SEND_DELAY_SECONDS = 0.05


async def main() -> None:
    load_dotenv()
    bot_token = os.environ["BOT_TOKEN"]
    db_path = os.environ.get("DB_PATH", "arbitrage_bot.sqlite3")

    repo = Repository(db_path)

    # Same IPv6-DNS-stall workaround as bot/main.py -- confirmed live on this VPS.
    session = AiohttpSession()
    session._connector_init["family"] = socket.AF_INET
    bot = Bot(token=bot_token, session=session)

    users = repo.get_all_users()
    logger.info("Granting %d bonus day(s) to %d user(s)", BONUS_DAYS, len(users))

    granted = notified = failed = 0
    for user in users:
        repo.extend_subscription(user.chat_id, BONUS_DAYS)
        granted += 1
        try:
            await _send_message_with_retries(bot, user.chat_id, MESSAGE, parse_mode="HTML")
            notified += 1
        except Exception:
            # A blocked/deactivated chat shouldn't stop the rest of the broadcast -- the
            # bonus days are already granted regardless of whether the message lands.
            logger.exception("Failed to notify chat_id=%s", user.chat_id)
            failed += 1
        await asyncio.sleep(SEND_DELAY_SECONDS)

    logger.info("Done: granted=%d notified=%d failed=%d", granted, notified, failed)
    await bot.session.close()
    repo.close()


if __name__ == "__main__":
    asyncio.run(main())
