"""Pure trial/subscription logic: no I/O, no side effects, easy to unit-test in isolation.
`now` must always be timezone-aware UTC (matches how trial_started_at/subscription_expires_at
are stored -- see bot/db/repository.py)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.db.repository import UserSettings

TRIAL_DAYS = 3
# A user who signed up via someone's referral link gets a longer trial -- extra incentive
# to actually use a referral link instead of just starting the bot cold.
REFERRED_TRIAL_DAYS = 5


@dataclass(frozen=True)
class Plan:
    id: str
    days: int
    label: str
    price_rub: int
    price_stars: int


# Stars prices are a rough RUB equivalent at time of writing (~1 Star ~= 1.4 RUB) --
# Telegram's Stars exchange rate drifts over time, so treat these as a starting point
# to revisit, not a permanently fixed peg.
PLANS: list[Plan] = [
    Plan(id="7d", days=7, label="7 дней", price_rub=299, price_stars=200),
    Plan(id="30d", days=30, label="30 дней", price_rub=999, price_stars=700),
    Plan(id="360d", days=360, label="360 дней", price_rub=6990, price_stars=5000),
]

PLANS_BY_ID: dict[str, Plan] = {p.id: p for p in PLANS}

# Referral program: 1 tier only (no sub-referrals), credited as a discount balance the
# referrer can spend on their own future subscription purchases -- not a cash payout,
# since there's no integration here for sending real money out. STARS_TO_RUB_RATE matches
# the same rough peg used for PLANS above, needed to compare/convert between the two
# currencies a payment (and therefore a commission) can arrive in.
REFERRAL_COMMISSION_PCT = 0.20
STARS_TO_RUB_RATE = 1.4


def to_rub_equivalent(amount: float, currency: str) -> float:
    return amount if currency == "RUB" else amount * STARS_TO_RUB_RATE


def from_rub_equivalent(amount_rub: float, currency: str) -> float:
    return amount_rub if currency == "RUB" else amount_rub / STARS_TO_RUB_RATE


def referral_discount(original_amount: float, currency: str, balance_rub: float) -> tuple[float, float]:
    """(discounted_amount, discount_used_in_that_currency). Never discounts the full way
    to zero -- a real invoice needs a positive amount -- and never uses more balance than
    available."""
    available = from_rub_equivalent(max(0.0, balance_rub), currency)
    discount = max(0.0, min(available, original_amount - 1))
    return original_amount - discount, discount


def referral_commission_rub(payment_amount: float, currency: str) -> float:
    """Commission credited to the referrer, in RUB-equivalent, for one payment."""
    return REFERRAL_COMMISSION_PCT * to_rub_equivalent(payment_amount, currency)


def _trial_end(user: UserSettings) -> datetime:
    days = REFERRED_TRIAL_DAYS if user.referred_by is not None else TRIAL_DAYS
    return datetime.fromisoformat(user.trial_started_at) + timedelta(days=days)


def _access_end(user: UserSettings) -> datetime:
    """The later of trial end and subscription end -- a purchase during an active
    trial simply extends coverage, it never shortens it."""
    trial_end = _trial_end(user)
    if user.subscription_expires_at:
        return max(trial_end, datetime.fromisoformat(user.subscription_expires_at))
    return trial_end


def access_end(user: UserSettings, now: datetime) -> datetime:
    """Public wrapper around _access_end -- `now` is accepted but unused (access_end
    doesn't actually depend on it, only trial_started_at/subscription_expires_at do) and
    kept for symmetry with has_access/days_left so callers don't need to know which
    functions do and don't need a clock."""
    return _access_end(user)


def is_admin(user: UserSettings, admin_chat_ids: frozenset[int]) -> bool:
    return user.chat_id in admin_chat_ids


def has_access(user: UserSettings, now: datetime, admin_chat_ids: frozenset[int] = frozenset()) -> bool:
    if is_admin(user, admin_chat_ids):
        return True
    return now < _access_end(user)


def on_trial(user: UserSettings, now: datetime) -> bool:
    """True while access is still covered by the free trial specifically (as opposed
    to a paid subscription) -- used to label the dashboard's status line correctly even
    if the user has a since-expired subscription_expires_at sitting alongside an
    active trial."""
    return now < _trial_end(user)


def days_left(user: UserSettings, now: datetime) -> int:
    """Whole days remaining until access expires, rounded up (so a few hours left still
    shows as 1, not 0); 0 once access has actually expired."""
    remaining = (_access_end(user) - now).total_seconds()
    return max(0, math.ceil(remaining / 86400))
