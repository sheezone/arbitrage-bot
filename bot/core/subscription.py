"""Mandatory-channel-subscription check, shared between the button bot UI
(handlers/commands.py) and the Mini App API (webapp/api.py) -- both must agree on the
same membership check, so it lives in one place rather than being reimplemented twice."""
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

logger = logging.getLogger(__name__)

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
