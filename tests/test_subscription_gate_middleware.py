"""Exercises SubscriptionGateMiddleware directly against minimal-but-real aiogram
Message/CallbackQuery objects (not full dispatcher wiring -- register_handlers isn't
called anywhere else in the test suite either, see its own module docstring), since the
middleware's isinstance(event, Message/CallbackQuery) checks need the real classes."""
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from aiogram.types import CallbackQuery, Chat, Message, User

from bot.handlers.commands import NAV_CHECK_CHANNEL_SUB, SubscriptionGateMiddleware


def _run(coro):
    return asyncio.run(coro)


def _message(chat_id: int, text: str = "🔍 Поиск вилок") -> Message:
    chat = Chat(id=chat_id, type="private")
    user = User(id=chat_id, is_bot=False, first_name="Test")
    return Message(message_id=1, date=datetime.now(), chat=chat, text=text, from_user=user)


def _callback(chat_id: int, data: str) -> CallbackQuery:
    msg = _message(chat_id, text="dashboard")
    user = User(id=chat_id, is_bot=False, first_name="Test")
    return CallbackQuery(id="1", from_user=user, chat_instance="x", data=data, message=msg)


class _FakeMember:
    def __init__(self, status: str):
        self.status = status


class _FakeBot:
    def __init__(self, status: str = "member"):
        self.status = status

    async def get_chat_member(self, channel_id, user_chat_id):
        return _FakeMember(self.status)


async def _noop_handler(event, data):
    return "handler ran"


def test_lets_start_command_through_regardless_of_subscription():
    mw = SubscriptionGateMiddleware(frozenset(), -100, "chan")
    bot = _FakeBot(status="left")
    result = _run(mw(_noop_handler, _message(1, text="/start"), {"bot": bot}))
    assert result == "handler ran"


def test_lets_check_subscription_callback_through_regardless_of_subscription():
    mw = SubscriptionGateMiddleware(frozenset(), -100, "chan")
    bot = _FakeBot(status="left")
    result = _run(mw(_noop_handler, _callback(1, NAV_CHECK_CHANNEL_SUB), {"bot": bot}))
    assert result == "handler ran"


def test_lets_admin_through_even_when_not_subscribed():
    mw = SubscriptionGateMiddleware(frozenset({1}), -100, "chan")
    bot = _FakeBot(status="left")
    result = _run(mw(_noop_handler, _message(1), {"bot": bot}))
    assert result == "handler ran"


def test_lets_subscribed_user_through():
    mw = SubscriptionGateMiddleware(frozenset(), -100, "chan")
    bot = _FakeBot(status="member")
    result = _run(mw(_noop_handler, _message(1), {"bot": bot}))
    assert result == "handler ran"


def test_blocks_unsubscribed_message_and_sends_gate_instead():
    mw = SubscriptionGateMiddleware(frozenset(), -100, "chan")
    bot = _FakeBot(status="left")
    message = _message(1)

    sent = []

    async def fake_answer(text, reply_markup=None, parse_mode=None):
        sent.append(text)

    object.__setattr__(message, "answer", fake_answer)  # Message is a frozen pydantic model
    result = _run(mw(_noop_handler, message, {"bot": bot}))
    assert result is None
    assert len(sent) == 1
    assert "Подписаться" in sent[0] or "🔒" in sent[0]


def test_blocks_unsubscribed_callback_and_shows_alert_plus_gate():
    mw = SubscriptionGateMiddleware(frozenset(), -100, "chan")
    bot = _FakeBot(status="left")
    callback = _callback(1, "nav:search")

    answers = []
    message_sends = []

    async def fake_cq_answer(text=None, show_alert=False):
        answers.append((text, show_alert))

    async def fake_message_answer(text, reply_markup=None, parse_mode=None):
        message_sends.append(text)

    object.__setattr__(callback, "answer", fake_cq_answer)  # CallbackQuery is a frozen pydantic model
    object.__setattr__(callback.message, "answer", fake_message_answer)

    result = _run(mw(_noop_handler, callback, {"bot": bot}))
    assert result is None
    assert answers and answers[0][1] is True  # show_alert=True
    assert len(message_sends) == 1
