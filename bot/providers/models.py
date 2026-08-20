"""Provider-agnostic data shapes returned by any OddsProvider implementation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Fixture:
    fixture_id: str
    game: str  # e.g. "cs2", "dota2", "lol"
    team_a: str
    team_b: str
    start_time_utc: str  # ISO 8601
    tournament_id: int
    sport_id: int


@dataclass(frozen=True)
class BookmakerQuote:
    bookmaker: str
    outcome_name: str  # matches Fixture.team_a / Fixture.team_b
    odds: float


@dataclass(frozen=True)
class SourceQuote:
    """A single bookmaker's price on one outcome of one match, as returned by any
    OddsProvider.fetch_quotes(). This is the common currency between otherwise
    unrelated sources (an aggregator like OddsPapi vs a single scraped bookmaker like
    Fonbet) -- everything downstream (bot/core/reconcile.py, bot/core/arbitrage.py)
    only ever sees this shape.
    """

    game: str
    team_a: str
    team_b: str
    start_time_utc: str  # ISO 8601
    bookmaker: str
    outcome_name: str  # matches team_a/team_b for market="winner", else a literal outcome label
    odds: float
    market: str = "winner"  # "winner" (team-name outcomes) or e.g. "total_2.5" (Over/Under, no draw possible)
