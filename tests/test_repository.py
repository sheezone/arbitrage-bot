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


def test_acquisition_source_round_trips_on_new_user(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(42, acquisition_source="telega_ads1")
    assert repo.get_user(42).acquisition_source == "telega_ads1"


def test_acquisition_source_is_none_by_default(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(42)
    assert repo.get_user(42).acquisition_source is None


def test_acquisition_source_never_overwritten_on_existing_user(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(42, acquisition_source="telega_ads1")
    repo.upsert_user(42, acquisition_source="some_other_campaign")  # e.g. re-pressing /start
    assert repo.get_user(42).acquisition_source == "telega_ads1"


def test_acquisition_source_counts_groups_and_sorts_by_count(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1, acquisition_source="telega_ads1")
    repo.upsert_user(2, acquisition_source="telega_ads1")
    repo.upsert_user(3, acquisition_source="tgstat_post")
    repo.upsert_user(4)  # organic, no tag
    counts = repo.get_acquisition_source_counts()
    assert counts[0] == {"source": "telega_ads1", "count": 2}
    assert counts[1] == {"source": "tgstat_post", "count": 1}
    assert counts[-1] == {"source": None, "count": 1}  # untagged sorted last


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


def test_new_user_has_no_bookmaker_restriction_by_default(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).allowed_bookmakers == []


def test_set_and_read_allowed_bookmakers(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_allowed_bookmakers(1, ["fonbet", "olimpbet"])
    assert repo.get_user(1).allowed_bookmakers == ["fonbet", "olimpbet"]

    repo.set_allowed_bookmakers(1, [])
    assert repo.get_user(1).allowed_bookmakers == []


def test_get_all_users_includes_paused_ones(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.upsert_user(2)
    repo.set_active(2, False)
    assert {u.chat_id for u in repo.get_active_users()} == {1}
    assert {u.chat_id for u in repo.get_all_users()} == {1, 2}


def test_expiry_reminder_sent_for_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).expiry_reminder_sent_for is None
    repo.set_expiry_reminder_sent(1, "2026-09-01T00:00:00+00:00")
    assert repo.get_user(1).expiry_reminder_sent_for == "2026-09-01T00:00:00+00:00"


def test_opportunity_stats_empty_by_default(tmp_path):
    repo = _repo(tmp_path)
    stats = repo.get_opportunity_stats()
    assert stats["today_count"] == 0
    assert stats["today_avg_profit"] == 0.0
    assert stats["alltime_count"] == 0


def test_opportunity_stats_aggregate_logged_entries(tmp_path):
    repo = _repo(tmp_path)
    repo.log_opportunity(2.0)
    repo.log_opportunity(4.0)
    repo.log_opportunity(6.0)
    stats = repo.get_opportunity_stats()
    assert stats["today_count"] == 3
    assert abs(stats["today_avg_profit"] - 4.0) < 0.001
    assert stats["today_best_profit"] == 6.0
    assert stats["alltime_count"] == 3
    assert abs(stats["alltime_avg_profit"] - 4.0) < 0.001


def test_support_message_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.record_support_message(admin_chat_id=99, admin_message_id=555, user_chat_id=1)
    assert repo.get_support_message_user(99, 555) == 1


def test_support_message_lookup_misses_return_none(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_support_message_user(99, 12345) is None


def test_opportunity_stats_today_excludes_yesterdays_entries(tmp_path):
    repo = _repo(tmp_path)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    repo._conn.execute("INSERT INTO opportunity_log (found_at, profit_pct) VALUES (?, ?)", (yesterday, 99.0))
    repo._conn.commit()
    repo.log_opportunity(2.0)

    stats = repo.get_opportunity_stats()
    assert stats["today_count"] == 1
    assert stats["today_avg_profit"] == 2.0
    assert stats["alltime_count"] == 2  # yesterday's entry still counts toward all-time


def test_new_user_is_not_muted_by_default(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).muted is False


def test_set_muted_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_muted(1, True)
    assert repo.get_user(1).muted is True
    repo.set_muted(1, False)
    assert repo.get_user(1).muted is False


def test_has_payment_false_until_recorded(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.has_payment("cryptobot:12345") is False
    repo.record_payment(1, "7d", "cryptobot", 3.5, "USDT", "cryptobot:12345")
    assert repo.has_payment("cryptobot:12345") is True


def test_record_payment_ignores_duplicate_crypto_charge_id(tmp_path):
    """Guards the has_payment-before-extend idempotency in on_crypto_check (see
    bot/handlers/commands.py) -- a repeated "Проверить оплату" tap after it already
    succeeded must not extend the subscription twice."""
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.record_payment(1, "7d", "cryptobot", 3.5, "USDT", "cryptobot:99")
    repo.record_payment(1, "7d", "cryptobot", 3.5, "USDT", "cryptobot:99")
    count = repo._conn.execute(
        "SELECT COUNT(*) FROM payments WHERE telegram_charge_id = ?", ("cryptobot:99",)
    ).fetchone()[0]
    assert count == 1


def test_payments_summary_groups_by_provider_and_currency(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.record_payment(1, "7d", "stars", 200, "XTR", "a")
    repo.record_payment(1, "30d", "stars", 700, "XTR", "b")
    repo.record_payment(1, "7d", "cryptobot", 3.5, "USDT", "c")

    summary = repo.get_payments_summary()
    by_key = {(r["provider"], r["currency"]): r for r in summary}
    assert by_key[("stars", "XTR")]["count"] == 2
    assert by_key[("stars", "XTR")]["total"] == 900
    assert by_key[("cryptobot", "USDT")]["count"] == 1
    assert by_key[("cryptobot", "USDT")]["total"] == 3.5


def test_payments_summary_empty_with_no_payments(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_payments_summary() == []


def test_showcase_state_round_trips(tmp_path):
    repo = _repo(tmp_path)
    assert repo.get_showcase_state() == (None, None)
    repo.set_showcase_state("football:A:B:2026-08-29T20:00:00+00:00", "2026-08-29T21:00:00+00:00")
    assert repo.get_showcase_state() == ("football:A:B:2026-08-29T20:00:00+00:00", "2026-08-29T21:00:00+00:00")

    # A second call overwrites in place, not a second row (single persisted state, id=1).
    repo.set_showcase_state("football:C:D:2026-08-29T22:00:00+00:00", "2026-08-29T23:00:00+00:00")
    assert repo.get_showcase_state() == ("football:C:D:2026-08-29T22:00:00+00:00", "2026-08-29T23:00:00+00:00")


def test_find_duplicate_showcase_posts_keeps_the_first_of_each_key(tmp_path):
    repo = _repo(tmp_path)
    repo.record_showcase_post(100, 1, "match-a")
    repo.record_showcase_post(100, 2, "match-a")  # duplicate of message 1
    repo.record_showcase_post(100, 3, "match-b")  # different match, not a duplicate
    repo.record_showcase_post(100, 4, "match-a")  # another duplicate

    duplicates = repo.find_duplicate_showcase_posts()
    assert sorted(duplicates) == [(100, 2), (100, 4)]


def test_find_duplicate_showcase_posts_empty_when_no_dupes(tmp_path):
    repo = _repo(tmp_path)
    repo.record_showcase_post(100, 1, "match-a")
    repo.record_showcase_post(100, 2, "match-b")
    assert repo.find_duplicate_showcase_posts() == []


def test_delete_showcase_posts_removes_the_given_rows(tmp_path):
    repo = _repo(tmp_path)
    repo.record_showcase_post(100, 1, "match-a")
    repo.record_showcase_post(100, 2, "match-a")
    repo.delete_showcase_posts(100, [2])
    duplicates = repo.find_duplicate_showcase_posts()
    assert duplicates == []  # only one row left for "match-a" now


def test_get_recent_users_orders_newest_first(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.upsert_user(2)
    repo._conn.execute("UPDATE users SET trial_started_at = '2020-01-01T00:00:00+00:00' WHERE chat_id = 1")
    repo._conn.execute("UPDATE users SET trial_started_at = '2026-01-01T00:00:00+00:00' WHERE chat_id = 2")
    repo._conn.commit()

    recent = repo.get_recent_users(limit=10)
    assert [u.chat_id for u in recent] == [2, 1]
