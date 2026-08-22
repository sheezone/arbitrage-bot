"""Shared in-memory snapshot of the latest arbitrage scan, updated by the monitor loop
and read instantly by the "search now" button -- no fresh network calls, so the button
always answers immediately regardless of how long a full poll cycle takes."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from bot.core.arbitrage import ArbitrageResult


@dataclass(frozen=True)
class MatchSnapshot:
    game: str
    team_a: str
    team_b: str
    arb: ArbitrageResult
    start_time_utc: str = ""


@dataclass
class LatestState:
    updated_at: float = 0.0
    matches: list[MatchSnapshot] = field(default_factory=list)
