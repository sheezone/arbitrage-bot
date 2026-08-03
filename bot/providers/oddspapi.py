"""OddsPapi (oddspapi.io) provider implementation.

Verified against the live API on 2026-07-30 with a real account/key (not just vendor
marketing docs):

- GET /v4/sports -> [{sportId, slug, sportName}]. Esports: 16=Dota, 17=Counter-Strike,
  18=League of Legends. Tennis: 12.
- GET /v4/fixtures?sportId=&from=&to=&apiKey= -- `from`/`to` (YYYY-MM-DD) are REQUIRED
  and must be within 10 days of each other. Returns fixtures with `fixtureId` (string),
  `tournamentId`, `participant1Name`, `participant2Name`, `startTime`, `hasOdds` (bool,
  must be filtered client-side -- the API does not filter on it server-side).
- GET /v4/odds-by-tournaments?tournamentIds=&bookmaker=&apiKey= -- returns odds for every
  fixture in up to 5 tournaments (hard API limit: "maximum of 5 tournament IDs"), but for
  exactly ONE bookmaker per call. Same fixture/bookmakerOdds shape as the (much more
  expensive) per-fixture /v4/odds endpoint. This is what fetch_quotes uses: one call per
  (bookmaker, <=5 tournaments) batch instead of one call per fixture, which matters
  because the free tier is credit-limited (250 total, not just rate-limited per minute) --
  calling /v4/odds per-fixture was burning ~200 credits per poll cycle for 3 games.
- GET /v4/markets?sportId=&apiKey= -> reference table confirming the 2-way "Winner"
  moneyline market follows one predictable formula across every sport checked (10-29):
  marketId == outcome1Id == sportId*10 + 1, outcome2Id == marketId + 1. e.g. sportId=17
  (CS) -> marketId 171, outcomes 171/172; sportId=12 (tennis) -> marketId 121, outcomes
  121/122. `_match_winner_market_id(sport_id)` below implements this.

One thing NOT covered by any written OddsPapi docs (inferred from observed live data,
not guaranteed): a price is only genuinely bettable right now when bookmaker.suspended is
False AND market.marketActive is True AND outcome.players["0"].active is True. Bookmakers
that fail any of those three showed obviously stale/placeholder prices in testing (e.g.
1.01 / 14.0 on an otherwise closely-matched fixture). If real-world notifications ever look
wrong (offer already gone, price stale), re-check this filter first.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import BookmakerQuote, Fixture, SourceQuote

GAME_TO_SPORT_ID: dict[str, int] = {
    "cs2": 17,
    "dota2": 16,
    "lol": 18,
    "tennis": 12,
}

FIXTURES_WINDOW_DAYS = 7  # must stay under the API's 10-day from/to limit
TOURNAMENT_BATCH_SIZE = 5  # hard API limit on /v4/odds-by-tournaments
REQUEST_SPACING_SECONDS = 0.7  # observed burst rate limit is separate from the 250-credit quota
MAX_RETRIES_ON_RATE_LIMIT = 3

# Curated shortlist of bookmakers genuinely independent of Fonbet/PARI, kept short on
# purpose: credit cost scales linearly with len(BOOKMAKERS) (one call per bookmaker per
# 5-tournament batch). Tennis alone roughly doubles the tournament count vs all three
# esports combined, so this list is intentionally short with tennis in the mix. Widen
# only if the 250-credit free tier is upgraded.
BOOKMAKERS = ["bet365", "1xbet"]

# Tennis alone has roughly as many concurrent tournaments as all three esports combined
# (ATP/WTA/challenger/qualifying/ITF running worldwide every day), so refreshing it every
# poll cycle would dominate the 250-credit quota. Throttled separately: reuse the last
# tennis fetch until this many seconds have passed, regardless of how often esports refreshes.
TENNIS_MIN_INTERVAL_SECONDS = 600


class OddsPapiProvider(OddsProvider):
    def __init__(self, api_key: str, base_url: str = "https://api.oddspapi.io"):
        self._api_key = api_key
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=15.0)
        self._tennis_cache: list[SourceQuote] = []
        self._tennis_cache_at: float = 0.0

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        if not self._api_key:
            raise RuntimeError("ODDS_API_KEY is not set — fill it in .env before fixtures can be fetched.")

        games_to_fetch = list(games)
        reuse_cached_tennis = False
        if "tennis" in games_to_fetch and time.time() - self._tennis_cache_at < TENNIS_MIN_INTERVAL_SECONDS:
            games_to_fetch.remove("tennis")
            reuse_cached_tennis = True

        fixtures_by_id: dict[str, Fixture] = {}
        for game in games_to_fetch:
            await asyncio.sleep(REQUEST_SPACING_SECONDS)
            for fixture in await self._get_fixtures(game):
                fixtures_by_id[fixture.fixture_id] = fixture

        tournament_ids = sorted({f.tournament_id for f in fixtures_by_id.values()})
        batches = [
            tournament_ids[i : i + TOURNAMENT_BATCH_SIZE]
            for i in range(0, len(tournament_ids), TOURNAMENT_BATCH_SIZE)
        ]

        quotes: list[SourceQuote] = []
        for bookmaker in BOOKMAKERS:
            for batch in batches:
                await asyncio.sleep(REQUEST_SPACING_SECONDS)
                for raw_fixture in await self._get_odds_by_tournaments(bookmaker, batch):
                    fixture = fixtures_by_id.get(raw_fixture.get("fixtureId"))
                    if fixture is None:
                        continue
                    for bq in _parse_odds_response(raw_fixture, fixture):
                        quotes.append(
                            SourceQuote(
                                game=fixture.game,
                                team_a=fixture.team_a,
                                team_b=fixture.team_b,
                                start_time_utc=fixture.start_time_utc,
                                bookmaker=bq.bookmaker,
                                outcome_name=bq.outcome_name,
                                odds=bq.odds,
                            )
                        )

        if "tennis" in games_to_fetch:
            self._tennis_cache = [q for q in quotes if q.game == "tennis"]
            self._tennis_cache_at = time.time()
        if reuse_cached_tennis:
            quotes.extend(self._tennis_cache)

        return quotes

    async def _get_fixtures(self, game: str) -> list[Fixture]:
        sport_id = GAME_TO_SPORT_ID.get(game)
        if sport_id is None:
            raise ValueError(f"Unknown game key {game!r}, expected one of {list(GAME_TO_SPORT_ID)}")

        now = datetime.now(timezone.utc)
        to = now + timedelta(days=FIXTURES_WINDOW_DAYS)
        params = {
            "sportId": sport_id,
            "from": now.strftime("%Y-%m-%d"),
            "to": to.strftime("%Y-%m-%d"),
            "apiKey": self._api_key,
        }

        raw = None
        for attempt in range(MAX_RETRIES_ON_RATE_LIMIT + 1):
            resp = await self._client.get("/v4/fixtures", params=params)
            if resp.status_code == 429 and attempt < MAX_RETRIES_ON_RATE_LIMIT:
                await asyncio.sleep(REQUEST_SPACING_SECONDS * (attempt + 2))
                continue
            resp.raise_for_status()
            raw = resp.json()
            break
        if raw is None:
            return []

        fixtures = []
        for item in raw:
            if not item.get("hasOdds"):
                continue
            fixtures.append(
                Fixture(
                    fixture_id=item["fixtureId"],
                    game=game,
                    team_a=item["participant1Name"],
                    team_b=item["participant2Name"],
                    start_time_utc=item.get("startTime", ""),
                    tournament_id=item["tournamentId"],
                    sport_id=sport_id,
                )
            )
        return fixtures

    async def _get_odds_by_tournaments(self, bookmaker: str, tournament_ids: list[int]) -> list[dict]:
        if not tournament_ids:
            return []

        for attempt in range(MAX_RETRIES_ON_RATE_LIMIT + 1):
            resp = await self._client.get(
                "/v4/odds-by-tournaments",
                params={
                    "tournamentIds": ",".join(str(t) for t in tournament_ids),
                    "bookmaker": bookmaker,
                    "apiKey": self._api_key,
                },
            )
            if resp.status_code == 404:
                # This bookmaker simply doesn't cover any fixture in this batch of
                # tournaments -- not an error.
                return []
            if resp.status_code == 429 and attempt < MAX_RETRIES_ON_RATE_LIMIT:
                await asyncio.sleep(REQUEST_SPACING_SECONDS * (attempt + 2))
                continue
            resp.raise_for_status()
            return resp.json()
        return []

    async def close(self) -> None:
        await self._client.aclose()


def _match_winner_market_id(sport_id: int) -> tuple[str, str, str]:
    """Returns (marketId, outcome1Id, outcome2Id) per the sportId*10+1 formula."""
    market_id = sport_id * 10 + 1
    return str(market_id), str(market_id), str(market_id + 1)


def _parse_odds_response(raw: dict, fixture: Fixture) -> list[BookmakerQuote]:
    """Isolated parsing so schema fixes stay in one place. See module docstring."""
    market_id, outcome1_id, outcome2_id = _match_winner_market_id(fixture.sport_id)

    quotes: list[BookmakerQuote] = []
    for bookmaker_name, bm in raw.get("bookmakerOdds", {}).items():
        if bm.get("suspended"):
            continue

        market = bm.get("markets", {}).get(market_id)
        if market is None or not market.get("marketActive"):
            continue

        for outcome_id, outcome in market.get("outcomes", {}).items():
            leaf = (outcome.get("players") or {}).get("0")
            if leaf is None or not leaf.get("active"):
                continue
            price = leaf.get("price")
            if not price:
                continue

            if outcome_id == outcome1_id:
                outcome_name = fixture.team_a
            elif outcome_id == outcome2_id:
                outcome_name = fixture.team_b
            else:
                continue

            quotes.append(
                BookmakerQuote(bookmaker=bookmaker_name, outcome_name=outcome_name, odds=float(price))
            )
    return quotes
