"""OlimpBet (olimp.bet / www.olimp.bet) -- confirmed live (2026-08-26): its whole line
is a single unencrypted REST GET, no auth, no browser needed:
`/api/v4/0/line/top/sports-with-competitions-with-events`. Despite the "top" in the path,
this returned 447 real football matches at check time (not a curated handful like
baltbet.py/melbet.py/leon.py's "top events" tier) -- genuinely broad coverage, across all
26 sports the site offers, in one ~9MB response.

Response shape: a list of `{operationId, payload: {sport, competitionsWithEvents}}`, one
entry per sport. Each event has a flat `outcomes` list covering every market at once, each
outcome tagged with a `groupName`/`groupPosition`/`tableType`. Some "events" are actually
corner-count (or other statistical) prop markets modelled with the same shape as a real
match -- confirmed live: their team names are prefixed "УГЛ " (i.e. "Corners ..."), so
those are filtered out by team name rather than trusted to only appear where expected.

Football and hockey use the TOTAL outcomes at `groupPosition == 7` ("Доп. Тотал", singular
-- exactly one line, the platform's own "closest to fair" pick) rather than
`groupPosition == 8` ("Доп. тотал", plural -- a whole ladder of alternate lines, not used
here) or the 1X2 win market, for the same reason as everywhere else in this codebase: both
allow a draw, but a goals/pucks total doesn't. See bot/providers/_line_platform.py's module
docstring for the fuller rationale.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://www.olimp.bet"
TOP_EVENTS_PATH = "/api/v4/0/line/top/sports-with-competitions-with-events"

SPORT_IDS = {
    "football": "1",
    "hockey": "2",
}

MAIN_TOTAL_GROUP_POSITION = 7
STAT_PROP_TEAM_PREFIX = "УГЛ"  # corner-count (and similar) prop "matches", not real ones
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal/puck totals; guards against mismatched markets


class OlimpBetProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=30.0, headers={"User-Agent": "Mozilla/5.0"})

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = [g for g in games if g in SPORT_IDS]
        if not wanted:
            return []

        resp = await self._client.get(TOP_EVENTS_PATH, params={"vids[]": ""})
        resp.raise_for_status()
        raw = resp.json()

        quotes: list[SourceQuote] = []
        for game in wanted:
            quotes.extend(parse_sports_payload(game, raw))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_sports_payload(game: str, raw: list[dict]) -> list[SourceQuote]:
    sport_id = SPORT_IDS.get(game)
    if sport_id is None:
        return []

    sport_entry = next((d for d in raw if (d.get("payload") or {}).get("sport", {}).get("id") == sport_id), None)
    if sport_entry is None:
        return []

    quotes: list[SourceQuote] = []
    for competition in (sport_entry["payload"].get("competitionsWithEvents") or []):
        for event in competition.get("events") or []:
            quotes.extend(_parse_event(game, event))
    return quotes


def _parse_event(game: str, event: dict) -> list[SourceQuote]:
    team_a, team_b = event.get("team1Name"), event.get("team2Name")
    if not team_a or not team_b or team_a.startswith(STAT_PROP_TEAM_PREFIX) or team_b.startswith(STAT_PROP_TEAM_PREFIX):
        return []

    total_outcomes = [
        o for o in event.get("outcomes") or []
        if o.get("tableType") == "TOTAL" and o.get("groupPosition") == MAIN_TOTAL_GROUP_POSITION
    ]
    if len(total_outcomes) != 2:
        return []

    lines = {o.get("param") for o in total_outcomes}
    if len(lines) != 1:
        return []  # the two sides disagree on the line -- skip rather than guess
    try:
        line = float(next(iter(lines)))
    except (TypeError, ValueError):
        return []
    if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
        return []

    over_odds = under_odds = None
    for o in total_outcomes:
        name = o.get("unprocessedName") or ""
        try:
            price = float(o.get("probability"))
        except (TypeError, ValueError):
            continue
        if name.endswith("бол"):
            over_odds = price
        elif name.endswith("мен"):
            under_odds = price
    if not over_odds or not under_odds:
        return []

    start_time_utc = _unix_to_iso(event.get("startDateTime"))
    market = f"total_{line}"
    return [
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал больше {line}", over_odds, market),
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал меньше {line}", under_odds, market),
    ]


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
