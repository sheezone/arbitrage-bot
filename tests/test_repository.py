import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.repository import Repository


def _repo(tmp_path) -> Repository:
    return Repository(str(tmp_path / "test.sqlite3"))


def test_new_user_starts_trial_immediately(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(42)
    user = repo.get_user(42)
    assert user.trial_started_at is not None
    assert user.subscription_expires_at is None
    started = datetime.fromisoformat(user.trial_started_at)
    assert (datetime.now(timezone.utc) - started).total_seconds() < 5


def test_extend_subscription_from_no_prior_subscription(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.extend_subscription(1, 7)
    user = repo.get_user(1)
    expires = datetime.fromisoformat(user.subscription_expires_at)
    expected = datetime.now(timezone.utc) + timedelta(days=7)
    assert abs((expires - expected).total_seconds()) < 5


def test_extend_subscription_stacks_on_top_of_existing(tmp_path):
    """A second purchase should add on top of remaining time, not reset it from now --
    otherwise buying early would waste whatever was left on the current plan."""
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.extend_subscription(1, 30)
    first_expiry = datetime.fromisoformat(repo.get_user(1).subscription_expires_at)

    repo.extend_subscription(1, 7)
    second_expiry = datetime.fromisoformat(repo.get_user(1).subscription_expires_at)

    assert abs((second_expiry - (first_expiry + timedelta(days=7))).total_seconds()) < 5


def test_extend_subscription_after_lapse_starts_from_now(tmp_path):
    """If the previous subscription already expired, the new one starts from now, not
    from the old (past) expiry date."""
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    lapsed = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    repo._conn.execute("UPDATE users SET subscription_expires_at = ? WHERE chat_id = ?", (lapsed, 1))
    repo._conn.commit()

    repo.extend_subscription(1, 7)
    expiry = datetime.fromisoformat(repo.get_user(1).subscription_expires_at)
    expected = datetime.now(timezone.utc) + timedelta(days=7)
    assert abs((expiry - expected).total_seconds()) < 5


def test_record_payment_is_idempotent_on_charge_id(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.record_payment(1, "7d", "stars", 200, "XTR", "charge-123")
    repo.record_payment(1, "7d", "stars", 200, "XTR", "charge-123")  # duplicate webhook/retry
    count = repo._conn.execute("SELECT COUNT(*) FROM payments WHERE telegram_charge_id = ?", ("charge-123",)).fetchone()[0]
    assert count == 1
