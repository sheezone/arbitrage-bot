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

Added 2026-08-26 (единоборства/волейбол request): boxing (sportId 12) and MMA (96) offer a
real "Ничья" (draw) outcome on their RESULT market -- confirmed live -- so, same reasoning
as football/hockey, they use a total-rounds TOTAL market instead. Unlike football/hockey
there's no fixed groupPosition for it (seen both 3 and 4 live) and usually just one line is
offered at all (only ~9/50 boxing events, ~12/53 MMA events had one live), so
`_extract_total_pair` scans every TOTAL outcome regardless of groupPosition and groups by
line instead of trusting a single known position. Volleyball (10) has no draw -- confirmed
live its RESULT market (groupPosition 1) only ever carries the two team outcomes, no "Ничья"
-- so it uses that match-winner market directly, same as basketball/tennis elsewhere in this
codebase.
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
    "boxing": "12",
    "mma": "96",
    "volleyball": "10",
    "tennis": "3",
    "basketball": "5",
    # All esports share sport 112; the game is told apart by competition name prefix.
    "cs2": "112",
    "dota2": "112",
    "lol": "112",
    "valorant": "112",
}
ESPORTS_PREFIXES = {
    "cs2": ("Counter-Strike", "CS2", "CS 2", "CS:GO"),
    "dota2": ("Dota",),
    "lol": ("League of Legends", "LoL"),
    "valorant": ("Valorant",),
}
# Outright/prop/stat competitions reuse the event shape but aren't real matches.
SKIP_COMPETITION_WORDS = ("Статистика", "Итоги", "Противостояние")

GOALS_TOTAL_GAMES = frozenset({"football", "hockey"})
ROUNDS_TOTAL_GAMES = frozenset({"boxing", "mma"})
RESULT_MARKET_GAMES = frozenset({"volleyball"})
# Plain two-way match winner, labelled П1/П2 at groupPosition 1 (tableType RESULT, or
# OTHER for basketball -- confirmed live 2026-09-25). Rejected if an "Х" is priced.
WINNER_GAMES = frozenset({"tennis", "basketball", "cs2", "dota2", "lol", "valorant"})
# "Доп. Тотал" is the single main line, "Доп. тотал" the full ladder of alternates. Both
# are used: other books each show only their own main line, and those often differ
# (2.5 vs 3), so the ladder is what lets Olimp line up with whatever line they chose.
GOALS_TOTAL_GROUP_NAMES = frozenset({"Доп. Тотал", "Доп. тотал"})
# Whole-match handicap (marketId 4): main line + ladder. Football and basketball only --
# see zenit.py's HANDICAP_GAMES for why not hockey/tennis.
HANDICAP_GAMES = frozenset({"football", "basketball"})
WHOLE_MATCH_HANDICAP_MARKET_ID = 4

MAIN_TOTAL_GROUP_POSITION = 7
STAT_PROP_TEAM_PREFIX = "УГЛ"  # corner-count (and similar) prop "matches", not real ones
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal/puck totals; guards against mismatched markets
PLAUSIBLE_ROUNDS_LINE_RANGE = (0.5, 14.5)  # real boxing/MMA total-rounds lines
RESULT_GROUP_POSITION = 1


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
        comp_name = (competition.get("competition") or {}).get("name") or ""
        if any(w in comp_name for w in SKIP_COMPETITION_WORDS):
            continue
        prefixes = ESPORTS_PREFIXES.get(game)
        if prefixes and not comp_name.startswith(prefixes):
            continue
        for event in competition.get("events") or []:
            quotes.extend(_parse_event(game, event))
    return quotes


def _parse_event(game: str, event: dict) -> list[SourceQuote]:
    team_a, team_b = event.get("team1Name"), event.get("team2Name")
    if not team_a or not team_b or team_a.startswith(STAT_PROP_TEAM_PREFIX) or team_b.startswith(STAT_PROP_TEAM_PREFIX):
        return []

    outcomes = event.get("outcomes") or []
    start_time_utc = _unix_to_iso(event.get("startDateTime"))

    if game in RESULT_MARKET_GAMES:
        return _parse_result_market(game, team_a, team_b, start_time_utc, outcomes)

    extra = _parse_handicaps(game, team_a, team_b, start_time_utc, outcomes) if game in HANDICAP_GAMES else []

    if game in WINNER_GAMES:
        return _parse_winner(game, team_a, team_b, start_time_utc, outcomes) + extra

    if game in GOALS_TOTAL_GAMES:
        return _parse_total_ladder(game, team_a, team_b, start_time_utc, outcomes) + extra
    else:
        # boxing/MMA: no fixed groupPosition for the (usually singular) total-rounds line
        # -- see module docstring -- so scan every TOTAL outcome and group by line instead.
        pair = _best_total_pair(outcomes, PLAUSIBLE_ROUNDS_LINE_RANGE)

    if pair is None:
        return []
    line, over_odds, under_odds = pair
    market = f"total_{line}"
    return [
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал больше {line}", over_odds, market),
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал меньше {line}", under_odds, market),
    ]


def _parse_handicaps(game: str, team_a: str, team_b: str, start_time_utc: str, outcomes: list[dict]) -> list[SourceQuote]:
    side1: dict[float, float] = {}
    side2: dict[float, float] = {}
    for o in outcomes:
        if o.get("tableType") != "HANDICAP" or o.get("marketId") != WHOLE_MATCH_HANDICAP_MARKET_ID:
            continue
        short = o.get("shortName") or ""
        try:
            line, price = float(o.get("param")), float(o.get("probability"))
        except (TypeError, ValueError):
            continue
        if "Ф1" in short or short == "Фора 1":
            side1.setdefault(line, price)
        elif "Ф2" in short or short == "Фора 2":
            side2.setdefault(line, price)
    quotes: list[SourceQuote] = []
    for h1, o1 in side1.items():
        o2 = side2.get(-h1)
        if o2 is None or (abs(h1) * 2) % 2 != 1 or o1 <= 1.0 or o2 <= 1.0:
            continue  # only half lines (no push) with both sides priced
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"H1:{h1}", o1, "hcp"))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"H2:{-h1}", o2, "hcp"))
    return quotes


def _parse_winner(game: str, team_a: str, team_b: str, start_time_utc: str, outcomes: list[dict]) -> list[SourceQuote]:
    main = [o for o in outcomes if o.get("groupPosition") == 1 and o.get("tableType") in ("RESULT", "OTHER")]
    by_short = {o.get("shortName"): o for o in main}
    if "Х" in by_short or "П1" not in by_short or "П2" not in by_short:
        return []  # draw priced (three-way) or no clean П1/П2 -- skip rather than guess
    try:
        odds_a = float(by_short["П1"].get("probability"))
        odds_b = float(by_short["П2"].get("probability"))
    except (TypeError, ValueError):
        return []
    if odds_a <= 1.0 or odds_b <= 1.0:
        return []
    return [
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", team_a, odds_a),
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", team_b, odds_b),
    ]


def _parse_total_ladder(game: str, team_a: str, team_b: str, start_time_utc: str, outcomes: list[dict]) -> list[SourceQuote]:
    by_line: dict[str, dict[str, dict]] = {}
    for o in outcomes:
        if o.get("tableType") != "TOTAL" or o.get("groupName") not in GOALS_TOTAL_GROUP_NAMES:
            continue
        # the main line also appears in the ladder -- dedupe by (line, side)
        by_line.setdefault(o.get("param"), {}).setdefault(o.get("unprocessedName") or "", o)
    quotes: list[SourceQuote] = []
    for group in by_line.values():
        pair = _total_pair_from_group(list(group.values()), PLAUSIBLE_TOTAL_LINE_RANGE)
        if pair is None:
            continue
        line, over_odds, under_odds = pair
        market = f"total_{line}"
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал больше {line}", over_odds, market))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", f"Тотал меньше {line}", under_odds, market))
    return quotes


def _parse_result_market(game: str, team_a: str, team_b: str, start_time_utc: str, outcomes: list[dict]) -> list[SourceQuote]:
    result_outcomes = [
        o for o in outcomes
        if o.get("tableType") == "RESULT" and o.get("groupPosition") == RESULT_GROUP_POSITION
    ]
    if len(result_outcomes) != 2:
        return []  # a real 3rd ("Ничья") outcome would land here too -- only a clean 2-way market is usable

    by_name = {o.get("unprocessedName"): o for o in result_outcomes}
    if team_a not in by_name or team_b not in by_name:
        return []  # names didn't line up 1:1 with team1Name/team2Name -- skip rather than guess which is which

    try:
        odds_a = float(by_name[team_a].get("probability"))
        odds_b = float(by_name[team_b].get("probability"))
    except (TypeError, ValueError):
        return []

    return [
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", team_a, odds_a),
        SourceQuote(game, team_a, team_b, start_time_utc, "olimpbet", team_b, odds_b),
    ]


def _total_pair_from_group(total_outcomes: list[dict], line_range: tuple[float, float]) -> tuple[float, float, float] | None:
    if len(total_outcomes) != 2:
        return None
    lines = {o.get("param") for o in total_outcomes}
    if len(lines) != 1:
        return None  # the two sides disagree on the line -- skip rather than guess
    try:
        line = float(next(iter(lines)))
    except (TypeError, ValueError):
        return None
    if not (line_range[0] <= line <= line_range[1]):
        return None

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
        return None
    return line, over_odds, under_odds


def _best_total_pair(outcomes: list[dict], line_range: tuple[float, float]) -> tuple[float, float, float] | None:
    by_line: dict[str, list[dict]] = {}
    for o in outcomes:
        if o.get("tableType") != "TOTAL":
            continue
        by_line.setdefault(o.get("param"), []).append(o)

    candidates = []
    for param, group in by_line.items():
        pair = _total_pair_from_group(group, line_range)
        if pair is not None:
            candidates.append(pair)
    if not candidates:
        return None
    return min(candidates, key=lambda p: p[0])


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
