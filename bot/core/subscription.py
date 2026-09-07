"""Mandatory-channel-subscription check, shared between the button bot UI
(handlers/commands.py), the periodic re-check in the monitor loop
(core/monitor.py's _check_channel_subscriptions), and the Mini App API
(webapp/api.py) -- all three must agree on the same membership check and the same
"please subscribe" screen, so both live in one place rather than being reimplemented.
Deliberately has no dependency on commands.py or monitor.py itself (both of those
import from here) to avoid a circular import."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

# Callback data for the "✅ Проверить подписку" button -- a shared constant so the
# button (built here) and the handler that reacts to it (commands.py) always agree.
CHECK_CHANNEL_SUB_CALLBACK = "nav:check_channel_sub"

# Telegram's chat member statuses that count as "still in the channel" -- "left" and
# "kicked" (banned) are the ones that mean not subscribed; there's no other status a
# real channel member can have.
_MEMBER_STATUSES = frozenset({"member", "administrator", "creator"})


async def is_subscribed(bot: Bot, channel_id: int, user_chat_id: int) -> bool:
    """True if user_chat_id is currently a member/admin/creator of channel_id.

    Fails CLOSED (returns False) on any error -- including a Telegram API outage or the
    bot not being an admin of the channel -- rather than silently letting everyone
    through. The whole point of this check is to be unbypassable, so a transient
    failure should re-show the gate (and get logged/noticed), not quietly grant access.
    If that trade-off ever needs revisiting for a specific deployment, this is the one
    place to change it."""
    try:
        member = await bot.get_chat_member(channel_id, user_chat_id)
    except (TelegramBadRequest, TelegramForbiddenError):
        # TelegramBadRequest covers "user not found" (never interacted with the
        # channel) -- a normal, expected case, not an error worth logging.
        return False
    except Exception:
        logger.exception("Subscription check failed for chat_id=%s against channel_id=%s", user_chat_id, channel_id)
        return False
    return member.status in _MEMBER_STATUSES


def gate_view(channel_username: str) -> tuple[str, InlineKeyboardMarkup]:
    """The (text, keyboard) shown wherever a user needs to be told to subscribe --
    the mandatory-gate screen in the bot UI, and the periodic reminder sent to anyone
    who's since left the channel (see core/monitor.py)."""
    text = (
        "🔒 <b>Доступ ограничен</b>\n\n"
        f"Чтобы пользоваться ботом (и мини-приложением), подпишитесь на канал "
        f"@{channel_username}, затем нажмите «Проверить подписку»."
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Подписаться", url=f"https://t.me/{channel_username}")],
            [InlineKeyboardButton(text="✅ Проверить подписку", callback_data=CHECK_CHANNEL_SUB_CALLBACK)],
        ]
    )
    return text, keyboard
