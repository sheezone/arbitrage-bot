"""Tests for the periodic re-check in core/monitor.py -- catches someone who left the
required channel and never opens the bot again on their own (so the live per-request
gate check in bot/core/subscription.py never gets a chance to run for them)."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.monitor import _check_channel_subscriptions
from bot.db.repository import Repository


def _run(coro):
    return asyncio.run(coro)


def _repo(tmp_path) -> Repository:
    return Repository(str(tmp_path / "test.sqlite3"))


class _FakeMember:
    def __init__(self, status: str):
        self.status = status


class _FakeBot:
    """Membership keyed by chat_id -- "member" unless explicitly overridden."""

    def __init__(self):
        self.statuses: dict[int, str] = {}
        self.sent: list[int] = []

    async def get_chat_member(self, channel_id, user_chat_id):
        return _FakeMember(self.statuses.get(user_chat_id, "member"))

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append(chat_id)


def test_is_a_no_op_when_gate_is_not_configured(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    bot = _FakeBot()
    bot.statuses[1] = "left"

    _run(_check_channel_subscriptions(repo, bot, frozenset(), None, ""))
    assert bot.sent == []


def test_sends_reminder_only_to_users_who_left(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.upsert_user(2)
    bot = _FakeBot()
    bot.statuses[1] = "left"  # user 2 stays "member" (default)

    _run(_check_channel_subscriptions(repo, bot, frozenset(), -100, "testchan"))
    assert bot.sent == [1]


def test_skips_admins_regardless_of_membership(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    bot = _FakeBot()
    bot.statuses[1] = "left"

    _run(_check_channel_subscriptions(repo, bot, frozenset({1}), -100, "testchan"))
    assert bot.sent == []


def test_sends_nothing_when_everyone_is_still_subscribed(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.upsert_user(2)
    bot = _FakeBot()  # everyone "member"

    _run(_check_channel_subscriptions(repo, bot, frozenset(), -100, "testchan"))
    assert bot.sent == []


def test_one_users_send_failure_does_not_block_the_rest(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.upsert_user(2)

    class _FlakyBot(_FakeBot):
        async def send_message(self, chat_id, text, **kwargs):
            if chat_id == 1:
                raise RuntimeError("network exploded")
            self.sent.append(chat_id)

    bot = _FlakyBot()
    bot.statuses[1] = "left"
    bot.statuses[2] = "left"

    _run(_check_channel_subscriptions(repo, bot, frozenset(), -100, "testchan"))
    assert bot.sent == [2]
