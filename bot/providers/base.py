"""Abstract odds source interface. Every source -- a multi-bookmaker aggregator like
OddsPapi or a single scraped bookmaker like Fonbet -- exposes the same shape so
bot/core/monitor.py and bot/core/reconcile.py don't need to know which kind they're
talking to."""
from __future__ import annotations

from abc import ABC, abstractmethod

from bot.providers.models import SourceQuote


class OddsProvider(ABC):
    @abstractmethod
    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        """Return every currently-known quote for the given game keys (e.g. 'cs2').

        A quote is one bookmaker's price on one outcome of one match. Multi-bookmaker
        sources return many quotes per match; single-bookmaker scrapers return exactly
        two (team_a win, team_b win) per match they carry.
        """

    @abstractmethod
    async def close(self) -> None:
        """Release any underlying HTTP resources."""
