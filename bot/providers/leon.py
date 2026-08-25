"""Leon (leon.ru) -- confirmed live (2026-08-26): its "headline matches" endpoint is a
single unencrypted REST GET, no auth, no browser needed:
`/api-2/betline/headline-matches?ctag=ru-RU&flags=reg,urlv2,orn2,cn,mm2,rrc,cmg&merged=true`.
This is a curated "featured matches" list (~79 events across all sports at check time),
not the exhaustive per-league line other sources scrape here -- similar limitation to
baltbet.py's "hot events" widget and melbet.py's "top events" list. A full-line endpoint
may exist (the site's own football category page fetches via POST to a batched `/api-1`
RPC multiplexer, not a plain GET), but wasn't found in the time spent looking -- this is
the "top events" tier, not full coverage.

Football and hockey use the "Тотал" market (`typeTag: "TOTAL"`, exact name match -- there
are several similarly-named markets per match: "Тотал хозяев"/"Тотал гостей" (per-team),
"1-й тайм: Тотал" (half only) -- only the plain "Тотал" is the full-match total we want),
for the same reason as everywhere else in this codebase: both allow a draw, but a
goals/pucks total doesn't. See bot/providers/_line_platform.py's module docstring for the
fuller rationale.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://leon.ru"
HEADLINE_MATCHES_PATH = "/api-2/betline/headline-matches"

SPORT_FAMILIES = {
    "football": "Soccer",
    "hockey": "IceHockey",
}

TOTAL_MARKET_NAME = "Тотал"
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal/puck totals; guards against mismatched markets


class LeonProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=20.0, headers={"User-Agent": "Mozilla/5.0"})

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = [g for g in games if g in SPORT_FAMILIES]
        if not wanted:
            return []

        resp = await self._client.get(
            HEADLINE_MATCHES_PATH,
            params={"ctag": "ru-RU", "flags": "reg,urlv2,orn2,cn,mm2,rrc,cmg", "merged": "true"},
        )
        resp.raise_for_status()
        raw = resp.json()

        quotes: list[SourceQuote] = []
        for game in wanted:
            quotes.extend(parse_events(game, raw))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_events(game: str, raw: dict) -> list[SourceQuote]:
    family = SPORT_FAMILIES.get(game)
    if family is None:
        return []

    events = ((raw.get("events") or {}).get("events")) or []
    quotes: list[SourceQuote] = []

    for event in events:
        sport = ((event.get("league") or {}).get("sport")) or {}
        if sport.get("family") != family:
            continue

        competitors = event.get("competitors") or []
        if len(competitors) != 2:
            continue
        team_a, team_b = competitors[0].get("name"), competitors[1].get("name")
        if not team_a or not team_b:
            continue

        total_market = next(
            (m for m in event.get("markets") or [] if m.get("name") == TOTAL_MARKET_NAME and m.get("typeTag") == "TOTAL"),
            None,
        )
        if total_market is None:
            continue

        by_direction = {}
        for runner in total_market.get("runners") or []:
            tags = runner.get("tags") or []
            price = runner.get("price")
            if not price:
                continue
            if "OVER" in tags:
                by_direction["OVER"] = price
            elif "UNDER" in tags:
                by_direction["UNDER"] = price
        over_odds, under_odds = by_direction.get("OVER"), by_direction.get("UNDER")
        if not over_odds or not under_odds:
            continue

        try:
            line = float(total_market.get("handicap"))
        except (TypeError, ValueError):
            continue
        if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
            continue

        start_time_utc = _millis_to_iso(event.get("kickoff"))
        market = f"total_{line}"
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "leon", f"Тотал больше {line}", over_odds, market))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "leon", f"Тотал меньше {line}", under_odds, market))

    return quotes


def _millis_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
