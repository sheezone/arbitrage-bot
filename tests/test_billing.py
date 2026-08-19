import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.billing import PLANS, TRIAL_DAYS, days_left, has_access, on_trial
from bot.db.repository import UserSettings

NOW = datetime.now(timezone.utc)


def _user(trial_started_ago_days: float, subscription_expires_in_days: float | None = None) -> UserSettings:
    sub = (NOW + timedelta(days=subscription_expires_in_days)).isoformat() if subscription_expires_in_days is not None else None
    return UserSettings(
        chat_id=1,
        bankroll=100.0,
        min_profit_pct=1.0,
        watched_games=["cs2"],
        is_active=True,
        menu_message_id=None,
        trial_started_at=(NOW - timedelta(days=trial_started_ago_days)).isoformat(),
        subscription_expires_at=sub,
    )


def test_trial_active_grants_access():
    user = _user(trial_started_ago_days=TRIAL_DAYS * 0.5)
    assert has_access(user, NOW)
    assert on_trial(user, NOW)
    assert 1 <= days_left(user, NOW) <= TRIAL_DAYS


def test_trial_expired_without_subscription_denies_access():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 1)
    assert not has_access(user, NOW)
    assert not on_trial(user, NOW)
    assert days_left(user, NOW) == 0


def test_active_subscription_after_trial_grants_access():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 1, subscription_expires_in_days=10)
    assert has_access(user, NOW)
    assert not on_trial(user, NOW)
    assert days_left(user, NOW) == 10


def test_expired_subscription_but_still_on_trial_reports_trial():
    # A plan bought during the trial that has since lapsed shouldn't shadow a trial
    # that's genuinely still running.
    user = _user(trial_started_ago_days=TRIAL_DAYS * 0.5, subscription_expires_in_days=-5)
    assert has_access(user, NOW)
    assert on_trial(user, NOW)


def test_expired_subscription_and_trial_denies_access():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 1, subscription_expires_in_days=-1)
    assert not has_access(user, NOW)
    assert days_left(user, NOW) == 0


def test_all_plans_have_positive_days_and_prices():
    for plan in PLANS:
        assert plan.days > 0
        assert plan.price_rub > 0
        assert plan.price_stars > 0
