"""The Odds API (the-odds-api.com) -- a traditional-sports odds aggregator, wired up
here only for basketball_nba: a genuinely draw-free 2-way market (overtime always
resolves a winner), so it fits the existing 2-way-only pipeline without any changes.
Adds bookmakers like 1xBet and Pinnacle -- both otherwise unreachable directly (1xBet
sits behind Cloudflare, Pinnacle doesn't serve retail Russian traffic at all) -- as a
genuinely independent comparison point against Fonbet/PARI/Marathon/Baltbet.

Checked live on 2026-08-04 against the full /v4/sports catalog (including inactive
sports, ~175 entries): no esports coverage exists on this platform at all. Tennis is
also deliberately not used here -- coverage is per-tournament (e.g.
"tennis_atp_cincinnati_open") rather than a blanket ATP/WTA feed like OddsPapi's, so
at any moment only one or two Slam/Masters-level tournaments are queryable and the
long tail of Challenger/ITF events (where Fonbet/PARI actually find most of their
arbitrage) is missing entirely.

Free tier is 500 requests/month, TOTAL, shared across everything -- not per-minute like
OddsPapi's spacing concern, an absolute ceiling. Cached hard as a result: refreshed at
most once every MIN_INTERVAL_SECONDS, reusing the last fetch otherwise. Even at a
2-hour cache this is ~360 requests/month, leaving headroom for manual testing.
"""
from __future__ import annotations

import time

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://api.the-odds-api.com"

GAME_TO_SPORT_KEY = {"basketball": "basketball_nba"}
REGIONS = "eu"  # where 1xBet/Pinnacle/Betfair/Marathon Bet/etc live in this API's region split
MIN_INTERVAL_SECONDS = 7200  # 500 requests/month total -- refresh sparingly


class TheOddsApiProvider(OddsProvider):
    def __init__(self, api_key: str, base_url: str = BASE_URL):
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._cache: dict[str, list[SourceQuote]] = {}
        self._cache_at: dict[str, float] = {}

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        if not self._api_key:
            return []

        quotes: list[SourceQuote] = []
        for game in games:
            sport_key = GAME_TO_SPORT_KEY.get(game)
            if sport_key is None:
                continue

            if time.time() - self._cache_at.get(game, 0.0) < MIN_INTERVAL_SECONDS:
                quotes.extend(self._cache.get(game, []))
                continue

            fresh = await self._fetch_sport(game, sport_key)
            self._cache[game] = fresh
            self._cache_at[game] = time.time()
            quotes.extend(fresh)
        return quotes

    async def _fetch_sport(self, game: str, sport_key: str) -> list[SourceQuote]:
        resp = await self._client.get(
            f"/v4/sports/{sport_key}/odds",
            params={"apiKey": self._api_key, "regions": REGIONS, "markets": "h2h", "oddsFormat": "decimal"},
        )
        if resp.status_code in (401, 429):
            # 401: bad/expired key. 429: monthly quota exhausted. Either way, skip this
            # cycle rather than raise -- run_monitor_loop already treats a failed source
            # as "no quotes this cycle" and keeps the others going.
            return []
        resp.raise_for_status()
        return parse_events(game, resp.json())

    async def close(self) -> None:
        await self._client.aclose()


def parse_events(game: str, raw_events: list[dict]) -> list[SourceQuote]:
    quotes: list[SourceQuote] = []
    for event in raw_events:
        team_a, team_b = event.get("home_team"), event.get("away_team")
        if not team_a or not team_b:
            continue
        start_time_utc = event.get("commence_time", "")

        for bm in event.get("bookmakers", []):
            bookmaker = bm.get("key", "unknown")
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue  # unexpected 3rd outcome -- skip rather than mis-model as 2-way
                for outcome in outcomes:
                    name, price = outcome.get("name"), outcome.get("price")
                    if name not in (team_a, team_b) or not price:
                        continue
                    quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, name, float(price)))
    return quotes
