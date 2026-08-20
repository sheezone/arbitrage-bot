"""PARI scraper. Runs on the exact same underlying platform as Fonbet (see
bot/providers/_line_platform.py) -- confirmed by diffing 14 live CS2 matches: identical
event IDs and bit-for-bit identical odds. Kept as a separate source anyway (see
bot/main.py) since it may diverge from Fonbet later, and can still form a real
arbitrage pair against a genuinely independent bookmaker from OddsPapi.

Endpoint found live on 2026-07-30 by watching pari.ru's own network traffic --
unauthenticated, undocumented, no CAPTCHA involved. Note the path has no "/ma" prefix,
unlike Fonbet."""
from __future__ import annotations

import httpx

from bot.providers._line_platform import parse_line_dump
from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://line-lb01-w.pb06e2-resources.com"
EVENTS_PATH = "/events/listBase"
SCOPE_MARKET = 2300

GAME_TO_SPORT_CATEGORY: dict[str, list[int]] = {
    "cs2": [20],
    "dota2": [19],
    "lol": [22],
    "valorant": [21],
    "tennis": [9, 10, 11, 17, 18, 32, 207, 210],
}

# Basketball, football and hockey are whole top-level "sport" nodes rather than a
# sportCategoryId within a shared parent -- see the module docstring in _line_platform.py.
# Football/hockey are matched to the Total goals/pucks market (no draw possible), not the
# 1X2 win market.
GAME_TO_PARENT_SPORT: dict[str, int] = {
    "basketball": 3,
    "football": 1,
    "hockey": 2,
}
EXCLUDE_CATEGORY_IDS = frozenset({119, 118, 165})  # NBA 2K / FC 26 / NHL 26 virtual simulations
TOTALS_GAMES = frozenset({"football", "hockey"})


class PariProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        resp = await self._client.get(EVENTS_PATH, params={"lang": "ru", "scopeMarket": SCOPE_MARKET})
        resp.raise_for_status()
        raw = resp.json()

        wanted = {g: GAME_TO_SPORT_CATEGORY[g] for g in games if g in GAME_TO_SPORT_CATEGORY}
        wanted_parents = {g: GAME_TO_PARENT_SPORT[g] for g in games if g in GAME_TO_PARENT_SPORT}
        return parse_line_dump(
            raw,
            wanted,
            bookmaker="pari",
            game_to_parent_sport=wanted_parents,
            exclude_category_ids=EXCLUDE_CATEGORY_IDS,
            totals_games=TOTALS_GAMES,
        )

    async def close(self) -> None:
        await self._client.aclose()
