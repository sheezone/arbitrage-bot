"""Shared parsing logic for bookmakers running on the same white-label betting
platform discovered live (2026-07-30) behind Fonbet and PARI: identical JSON shape,
identical internal event/sport IDs, identical factor-id scheme -- only the base URL,
path prefix and scopeMarket differ. Confirmed by diffing 14 live CS2 matches between
the two: bit-for-bit identical odds and event IDs, meaning they share one trading
engine. Kept as two separate sources anyway since they may diverge, and either can
still form a real arbitrage pair with a genuinely independent bookmaker (e.g. anything
from OddsPapi).

None of this is documented by either operator -- it was found by watching the site's
own network traffic in a browser (no CAPTCHA/anti-bot involved, no login required).
It is unauthenticated, undocumented, and can change or disappear without notice.

Basketball (added 2026-08-04) doesn't fit the sportCategoryId scheme above: unlike esports
(many titles sharing one "Киберспорт" parent, disambiguated by sportCategoryId) or tennis
(one title spanning several tournament-tier sportCategoryIds), real basketball leagues
mostly carry sportCategoryId=None -- the whole top-level "sport" node (parentId 3) *is*
the game. That parent also hosts a virtual/esports simulation mixed in under the same
parentId (NBA 2K -> sportCategoryId 119), which must be excluded explicitly or virtual
matches would pollute real fixtures.

Football and hockey deliberately have NO entry, for the same reason: checked live, both
sports' default win market always prices a draw (regulation time can tie) -- 161/200
sampled live hockey events carried DRAW_FACTOR, same as football. No distinct 2-way
"Draw No Bet"/"incl. overtime" market could be identified with confidence for either --
neither visually on fon.bet's own match page nor statistically (checked ~3000 live
football matches against every plausible factor-id pair for a match to the theoretical
no-vig DNB price; nothing landed close enough to trust with real money). Events carrying
DRAW_FACTOR are correctly skipped by the logic below rather than guessed at, so pointing
either sport at this parser just yields zero quotes -- safe, but not useful, which is why
neither is wired up from bot/config.py.

Response shape (GET .../events/listBase?lang=ru&scopeMarket=<n>):
- sports: flat list of {id, kind: "sport"|"segment", parentId, sportCategoryId, name}.
  Top-level category is a single "sport" node (e.g. alias "esports" or "tennis"); the
  actual game/tour is only distinguishable at the "segment" (tournament) level via
  sportCategoryId. Esports: 20=Counter-Strike, 19=Dota 2, 22=League of Legends. Tennis
  spans several category ids at once (ATP/WTA/challenger/qualifying/doubles/ITF etc:
  9, 10, 11, 17, 18, 32, 207, 210) -- unlike esports there is no single clean id, so
  a game key may map to more than one sportCategoryId.
- events: flat list of {id, sportId (-> a segment id above), team1, team2, startTime
  (unix seconds)}.
- customFactors: flat list of {e: <event id>, factors: [{f: <factor id>, v: <odds>}]}.
  Factor 921 = team1 win, factor 923 = team2 win -- this is a platform-wide factor
  numbering (same ids appear for football/tennis/etc in the sportBasicFactors catalog,
  not just esports), confirmed against the live "Победа" (Win) market definition.
- Factor 922 = draw. Present alongside 921/923 for Bo2-format series (a 1-1 series
  score is possible, unlike Bo1/Bo3/Bo5). Confirmed by finding 8 live "1w Essence Bo2"
  Dota fixtures where treating 921/923 as a plain 2-way market gave a mathematically
  impossible implied probability (sum of 1/odds < 1, i.e. a bookmaker apparently
  giving away free money on its own book) -- the missing draw probability was the
  cause, not a real arbitrage. Events carrying factor 922 are skipped entirely rather
  than modeled as a 3-way market, since bot/core/arbitrage.py and the OddsPapi side
  only handle 2-way outcomes for now.
"""
from __future__ import annotations

from bot.providers.models import SourceQuote

TEAM1_WIN_FACTOR = 921
DRAW_FACTOR = 922
TEAM2_WIN_FACTOR = 923


def parse_line_dump(
    raw: dict,
    game_to_categories: dict[str, list[int]],
    bookmaker: str,
    game_to_parent_sport: dict[str, int] | None = None,
    exclude_category_ids: frozenset[int] = frozenset(),
) -> list[SourceQuote]:
    category_to_game = {cat: game for game, cats in game_to_categories.items() for cat in cats}
    parent_id_to_game = {parent_id: game for game, parent_id in (game_to_parent_sport or {}).items()}

    segment_to_game: dict[int, str] = {}
    for s in raw.get("sports", []):
        if s.get("kind") != "segment":
            continue
        cat_id = s.get("sportCategoryId")
        if cat_id in category_to_game:
            segment_to_game[s["id"]] = category_to_game[cat_id]
        elif cat_id not in exclude_category_ids and s.get("parentId") in parent_id_to_game:
            segment_to_game[s["id"]] = parent_id_to_game[s["parentId"]]

    factors_by_event: dict[int, dict[int, float]] = {}
    for cf in raw.get("customFactors", []):
        event_id = cf.get("e")
        if event_id is None:
            continue
        factors_by_event[event_id] = {
            f["f"]: f["v"]
            for f in cf.get("factors", [])
            if f.get("f") in (TEAM1_WIN_FACTOR, DRAW_FACTOR, TEAM2_WIN_FACTOR)
        }

    quotes: list[SourceQuote] = []
    for event in raw.get("events", []):
        if event.get("place") != "line":
            continue  # pre-match only; live in-play odds move too fast for this loop

        game = segment_to_game.get(event.get("sportId"))
        if game is None:
            continue
        team_a, team_b = event.get("team1"), event.get("team2")
        if not team_a or not team_b:
            continue

        factors = factors_by_event.get(event["id"], {})
        if DRAW_FACTOR in factors:
            continue  # Bo2-style 3-way market, not supported yet

        odds_a = factors.get(TEAM1_WIN_FACTOR)
        odds_b = factors.get(TEAM2_WIN_FACTOR)
        if not odds_a or not odds_b:
            continue

        start_time_utc = _unix_to_iso(event.get("startTime"))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, team_a, odds_a))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, team_b, odds_b))

    return quotes


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
