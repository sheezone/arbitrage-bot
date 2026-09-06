import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from bot.core.subscription import is_subscribed


def _run(coro):
    return asyncio.run(coro)


class _FakeMember:
    def __init__(self, status: str):
        self.status = status


class _FakeBot:
    def __init__(self, result=None, exc=None):
        self._result = result
        self._exc = exc
        self.calls = []

    async def get_chat_member(self, channel_id, user_chat_id):
        self.calls.append((channel_id, user_chat_id))
        if self._exc is not None:
            raise self._exc
        return self._result


def test_is_subscribed_true_for_member():
    bot = _FakeBot(result=_FakeMember("member"))
    assert _run(is_subscribed(bot, -100123, 42)) is True
    assert bot.calls == [(-100123, 42)]


def test_is_subscribed_true_for_admin_or_creator():
    assert _run(is_subscribed(_FakeBot(result=_FakeMember("administrator")), -1, 1)) is True
    assert _run(is_subscribed(_FakeBot(result=_FakeMember("creator")), -1, 1)) is True


def test_is_subscribed_false_for_left_or_kicked():
    assert _run(is_subscribed(_FakeBot(result=_FakeMember("left")), -1, 1)) is False
    assert _run(is_subscribed(_FakeBot(result=_FakeMember("kicked")), -1, 1)) is False


def test_is_subscribed_false_when_user_never_interacted_with_channel():
    # Telegram raises TelegramBadRequest ("user not found") for this, not a "left" status.
    bot = _FakeBot(exc=TelegramBadRequest(method=None, message="user not found"))
    assert _run(is_subscribed(bot, -1, 1)) is False


def test_is_subscribed_false_when_bot_lacks_permission():
    bot = _FakeBot(exc=TelegramForbiddenError(method=None, message="bot is not a member"))
    assert _run(is_subscribed(bot, -1, 1)) is False


def test_is_subscribed_fails_closed_on_unexpected_error():
    bot = _FakeBot(exc=RuntimeError("network exploded"))
    assert _run(is_subscribed(bot, -1, 1)) is False
