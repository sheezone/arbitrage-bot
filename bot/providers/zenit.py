"""Zenit (zenit.win) -- like Melbet, this was previously assumed WebSocket-only/
unreachable (see bot/providers/baltbet.py's investigation notes), but that doesn't hold
up: confirmed live (2026-08-22) the whole prematch line is a single **unencrypted** REST
endpoint, `/ajax/line/printer/ranked?onlyview=0&lang_id=1&timezone=3`, gated by nothing
but an `imprintHash` request header whose value is never actually checked -- confirmed: a
random 32-hex-char string works with no cookies or session at all. Not real anti-bot, just
a header-presence check. So unlike melbet.py, no browser is needed here.

Response shape: `{"games": {<eventId>: {...}}, "dict": {"cmd": {<competitorId>: name}}}`.
Each game's `f_l` (factor list, the odds) and `hd` (header labels) arrays are positionally
paired -- `hd[i]` names what `f_l[i]` holds. The match Total market always appears as
three consecutive slots labelled "М" (Under odds), "Тотал" (the line itself, as a string
like "2.5"), "Б" (Over odds) -- confirmed against live football AND hockey matches. Only
ONE total line is exposed per match here (like Fonbet's single "closest to fair" line,
unlike melbet.py's several) -- found by searching `hd` for the "Тотал" label rather than
hardcoding its index, since the surrounding market columns (1X2/handicap) could plausibly
shift by sport.

Football and hockey use this Total market rather than the win market (`hd` "1"/"Х"/"2"),
for the same reason as everywhere else in this codebase: both allow a draw, but a
goals/pucks total doesn't. See bot/providers/_line_platform.py's module docstring for the
fuller rationale -- this mirrors that decision on a third independent bookmaker.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://zenit.win"
# Full prematch line (confirmed live 2026-09-25: ~6700 events in one ~10 MB response,
# ~11 s). The previous "/ajax/line/printer/ranked" endpoint is only the ~36 "top" events
# the homepage shows, which left Zenit with next to nothing to compare against. Same JSON
# shape (games / f_l / hd / dict.cmd) as ranked, so the parser below didn't change. The
# params mirror what zenit.win's own frontend sends for the line page (main.*.chunk.js,
# getLine); `sport` is a dash-joined list of Zenit sport ids.
LINE_PATH = "/ajax/line/printer/"
LINE_PARAMS = {
    "all": 0, "onlyview": 0, "timeline": 0, "tournaments_mode": 1, "ross": 0,
    "lang_id": 1, "timezone": 3, "offset": 0, "show_from_main": 0, "length": 10000,
    "sort_mode": 2, "popular": 0,
}

SPORT_IDS = {
    "football": 1,
    "hockey": 2,
    "basketball": 3,
    "tennis": 6,
}
# Football/hockey: the match-winner market has a draw, so only the Total is used (same
# rule as every other provider here). Basketball/tennis: match winner ("1"/"2"), taken
# only when the draw column ("Х") has no price, i.e. the market really is two-way.
TOTALS_GAMES = frozenset({"football", "hockey"})
WINNER_GAMES = frozenset({"basketball", "tennis"})

TOTAL_LABEL = "Тотал"
UNDER_LABEL = "М"
OVER_LABEL = "Б"
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal/puck totals; guards against mismatched columns


class ZenitProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=60.0,
            headers={"User-Agent": "Mozilla/5.0", "imprintHash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"},
        )

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = [g for g in games if g in SPORT_IDS]
        if not wanted:
            return []

        sport = "-".join(str(SPORT_IDS[g]) for g in wanted)
        resp = await self._client.get(LINE_PATH, params={**LINE_PARAMS, "sport": sport})
        resp.raise_for_status()
        raw = resp.json()

        quotes: list[SourceQuote] = []
        for game in wanted:
            quotes.extend(parse_line_dump(game, raw))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_line_dump(game: str, raw: dict) -> list[SourceQuote]:
    sport_id = SPORT_IDS.get(game)
    if sport_id is None:
        return []

    competitor_names = raw.get("dict", {}).get("cmd", {})
    quotes: list[SourceQuote] = []

    for event in raw.get("games", {}).values():
        if event.get("sid") != sport_id:
            continue

        team_a = competitor_names.get(str(event.get("c1_id")))
        team_b = competitor_names.get(str(event.get("c2_id")))
        if not team_a or not team_b:
            continue

        hd, f_l = event.get("hd") or [], event.get("f_l") or []
        start_time_utc = _unix_to_iso(event.get("time"))
        if game in WINNER_GAMES:
            quotes.extend(_winner_quotes(game, team_a, team_b, start_time_utc, hd, f_l))
            continue
        total_idx = next((i for i, h in enumerate(hd) if h.get("n") == TOTAL_LABEL), None)
        if total_idx is None or total_idx == 0 or total_idx + 1 >= len(f_l):
            continue
        if hd[total_idx - 1].get("n") != UNDER_LABEL or hd[total_idx + 1].get("n") != OVER_LABEL:
            continue  # unexpected column layout for this match -- skip rather than guess

        line_raw = f_l[total_idx].get("h")
        under_raw = f_l[total_idx - 1].get("h")
        over_raw = f_l[total_idx + 1].get("h")
        if line_raw is None or not under_raw or not over_raw:
            continue
        try:
            line = float(line_raw)
            under_odds = float(under_raw)
            over_odds = float(over_raw)
        except (TypeError, ValueError):
            continue
        if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
            continue

        market = f"total_{line}"
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "zenit", f"Тотал больше {line}", over_odds, market))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "zenit", f"Тотал меньше {line}", under_odds, market))

    return quotes


def _winner_quotes(game, team_a, team_b, start_time_utc, hd, f_l) -> list[SourceQuote]:
    labels = [h.get("n") for h in hd]
    try:
        i1 = labels.index("1")
    except ValueError:
        return []
    if i1 + 2 >= len(f_l) or labels[i1 + 1] != "Х" or labels[i1 + 2] != "2":
        return []  # unexpected layout -- skip rather than guess
    if f_l[i1 + 1].get("h") not in (None, "", 0):
        return []  # draw is priced -> three-way market, not an arb candidate here
    try:
        odds_a, odds_b = float(f_l[i1].get("h")), float(f_l[i1 + 2].get("h"))
    except (TypeError, ValueError):
        return []
    if odds_a <= 1.0 or odds_b <= 1.0:
        return []
    return [
        SourceQuote(game, team_a, team_b, start_time_utc, "zenit", team_a, odds_a),
        SourceQuote(game, team_a, team_b, start_time_utc, "zenit", team_b, odds_b),
    ]


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
