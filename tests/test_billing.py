import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.billing import (
    PLANS,
    REFERRED_TRIAL_DAYS,
    TRIAL_DAYS,
    days_left,
    from_rub_equivalent,
    has_access,
    is_admin,
    on_trial,
    referral_commission_rub,
    referral_discount,
    to_rub_equivalent,
)
from bot.db.repository import UserSettings

NOW = datetime.now(timezone.utc)


def _user(
    trial_started_ago_days: float, subscription_expires_in_days: float | None = None, referred_by: int | None = None
) -> UserSettings:
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
        time_horizons=[3],
        referred_by=referred_by,
        referral_balance_rub=0.0,
        expiry_reminder_sent_for=None,
        allowed_bookmakers=[],
        muted=False,
    )


def test_referred_user_gets_a_longer_trial():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 1, referred_by=42)
    assert has_access(user, NOW)  # would already be over for a non-referred user
    assert on_trial(user, NOW)
    assert 1 <= days_left(user, NOW) <= REFERRED_TRIAL_DAYS


def test_referred_users_extended_trial_still_ends():
    user = _user(trial_started_ago_days=REFERRED_TRIAL_DAYS + 1, referred_by=42)
    assert not has_access(user, NOW)


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


def test_admin_bypasses_expired_trial_and_subscription():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 100, subscription_expires_in_days=-100)
    admin_ids = frozenset({user.chat_id})
    assert not has_access(user, NOW)  # sanity check: genuinely expired without the bypass
    assert is_admin(user, admin_ids)
    assert has_access(user, NOW, admin_ids)


def test_admin_bypass_does_not_leak_to_other_chat_ids():
    user = _user(trial_started_ago_days=TRIAL_DAYS + 100)
    admin_ids = frozenset({user.chat_id + 1})  # some other chat_id is the admin, not this one
    assert not is_admin(user, admin_ids)
    assert not has_access(user, NOW, admin_ids)


def test_all_plans_have_positive_days_and_prices():
    for plan in PLANS:
        assert plan.days > 0
        assert plan.price_rub > 0
        assert plan.price_stars > 0


def test_referral_discount_reduces_rub_price():
    discounted, used = referral_discount(999, "RUB", balance_rub=200)
    assert discounted == 799
    assert used == 200


def test_referral_discount_never_reaches_zero():
    discounted, used = referral_discount(999, "RUB", balance_rub=5000)
    assert discounted == 1  # never fully free
    assert used == 998


def test_referral_discount_converts_for_stars_currency():
    # balance is stored in RUB-equivalent; spending it on a Stars invoice converts back
    discounted, used = referral_discount(700, "XTR", balance_rub=140)  # 140 RUB == 100 Stars
    assert discounted == 600
    assert used == 100


def test_referral_discount_caps_at_available_balance():
    discounted, used = referral_discount(999, "RUB", balance_rub=50)
    assert discounted == 949
    assert used == 50


def test_referral_discount_with_zero_balance_is_a_no_op():
    discounted, used = referral_discount(999, "RUB", balance_rub=0)
    assert discounted == 999
    assert used == 0


def test_referral_commission_for_rub_payment():
    assert referral_commission_rub(999, "RUB") == 199.8


def test_referral_commission_for_stars_payment_converts_to_rub():
    assert referral_commission_rub(700, "XTR") == 196.0


def test_usdt_to_rub_conversion_round_trips():
    assert to_rub_equivalent(10, "USDT") == 900.0
    assert from_rub_equivalent(900.0, "USDT") == 10.0


def test_referral_commission_for_usdt_payment_converts_to_rub():
    assert referral_commission_rub(10, "USDT") == 180.0  # 0.20 * (10 * 90)


def test_every_plan_has_a_usdt_price():
    for plan in PLANS:
        assert plan.price_usdt > 0
