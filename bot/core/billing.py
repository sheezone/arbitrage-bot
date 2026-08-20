"""Pure trial/subscription logic: no I/O, no side effects, easy to unit-test in isolation.
`now` must always be timezone-aware UTC (matches how trial_started_at/subscription_expires_at
are stored -- see bot/db/repository.py)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from bot.db.repository import UserSettings

TRIAL_DAYS = 3


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


def _trial_end(user: UserSettings) -> datetime:
    return datetime.fromisoformat(user.trial_started_at) + timedelta(days=TRIAL_DAYS)


def _access_end(user: UserSettings) -> datetime:
    """The later of trial end and subscription end -- a purchase during an active
    trial simply extends coverage, it never shortens it."""
    trial_end = _trial_end(user)
    if user.subscription_expires_at:
        return max(trial_end, datetime.fromisoformat(user.subscription_expires_at))
    return trial_end


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
