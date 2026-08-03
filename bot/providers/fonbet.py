"""Fonbet scraper. See bot/providers/_line_platform.py for the shared, reverse-engineered
schema this and PariProvider both rely on. Endpoint found live on 2026-07-30 by watching
fon.bet's own network traffic -- unauthenticated, undocumented, no CAPTCHA involved."""
from __future__ import annotations

import httpx

from bot.providers._line_platform import parse_line_dump
from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://line-lb54-w.bk6bba-resources.com"
EVENTS_PATH = "/ma/events/listBase"
SCOPE_MARKET = 1600

GAME_TO_SPORT_CATEGORY: dict[str, list[int]] = {
    "cs2": [20],
    "dota2": [19],
    "lol": [22],
    "tennis": [9, 10, 11, 17, 18, 32, 207, 210],
}


class FonbetProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        resp = await self._client.get(EVENTS_PATH, params={"lang": "ru", "scopeMarket": SCOPE_MARKET})
        resp.raise_for_status()
        raw = resp.json()

        wanted = {g: GAME_TO_SPORT_CATEGORY[g] for g in games if g in GAME_TO_SPORT_CATEGORY}
        return parse_line_dump(raw, wanted, bookmaker="fonbet")

    async def close(self) -> None:
        await self._client.aclose()
