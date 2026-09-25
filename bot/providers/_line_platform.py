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

Football and hockey (added 2026-08-20) are wired up via a completely different market:
Total goals/pucks in the match (factor 930 = Over, factor 931 = Under), NOT the 1X2 win
market -- a game's total score is exactly two-way (over or under the line), so a draw in
the underlying match (both allow one in regulation time) is irrelevant to it. Confirmed
live: factor pair 930/931 is present on 1442/1481 sampled football matches (97%) and
84/84 sampled hockey matches (100%), always carries matching `pt` (the line, e.g. "2.5")
on both sides, and the site's own selection key for the equivalent Marathon market
literally reads "Total_Goals0.Over_2.5"/"...Under_2.5" for both sports (see marathon.py).
A small fraction of matches carry implausible lines (e.g. 16.5, 23.5, 31.5) -- these
turned out to be non-match "outright"/specials entries reusing the same factor slot for
an unrelated total, not goals (confirmed: their `events` entries have team1/team2 ==
None). Filtered out via PLAUSIBLE_TOTAL_LINE_RANGE rather than trusted blindly, consistent
with this codebase's "skip rather than guess" policy elsewhere. Like basketball, both are
matched via game_to_parent_sport (football parentId=1, hockey parentId=2) rather than
sportCategoryId, and share one virtual/esports exclusion list with basketball's NBA 2K
(category 118 = "FC 26" virtual football, 165 = "NHL 26" virtual hockey).

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
TOTAL_OVER_FACTOR = 930
TOTAL_UNDER_FACTOR = 931
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal totals; guards against mismatched specials/outrights
# Full-match total ladder beyond the main 930/931 line: (over, under) factor pairs, each
# carrying its own `pt` line. Identified live 2026-09-25 by lining them up on real
# football matches -- prices move monotonically with the line (0.5 ... 4.5), so they're
# the whole-match goal total, not halves or team totals.
TOTAL_LADDER_PAIRS = (
    (TOTAL_OVER_FACTOR, TOTAL_UNDER_FACTOR),
    (1696, 1697), (1727, 1728), (1730, 1731), (1733, 1734),
    (1736, 1737), (1739, 1791), (1793, 1794), (1796, 1797),
)
# Whole-match handicap: (team 1, team 2) factor pairs, `pt` = that team's line. 927/928
# is the main line, the rest are the alternate ladder (same live identification).
HANDICAP_PAIRS = (
    (927, 928), (910, 912), (989, 991), (1569, 1572), (1672, 1675), (1677, 1678), (1680, 1681),
)
RELEVANT_FACTOR_IDS = frozenset(
    (TEAM1_WIN_FACTOR, DRAW_FACTOR, TEAM2_WIN_FACTOR)
    + tuple(f for pair in TOTAL_LADDER_PAIRS for f in pair)
    + tuple(f for pair in HANDICAP_PAIRS for f in pair)
)
# Handicaps only where every book means the same thing by them (see zenit.py).
DEFAULT_HANDICAP_GAMES = frozenset({"football", "basketball"})


def parse_line_dump(
    raw: dict,
    game_to_categories: dict[str, list[int]],
    bookmaker: str,
    game_to_parent_sport: dict[str, int] | None = None,
    exclude_category_ids: frozenset[int] = frozenset(),
    totals_games: frozenset[str] = frozenset(),
    handicap_games: frozenset[str] = DEFAULT_HANDICAP_GAMES,
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

    factors_by_event: dict[int, dict[int, dict]] = {}
    for cf in raw.get("customFactors", []):
        event_id = cf.get("e")
        if event_id is None:
            continue
        factors_by_event[event_id] = {f["f"]: f for f in cf.get("factors", []) if f.get("f") in RELEVANT_FACTOR_IDS}

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
        # Stat-prop "matches" (offsides, corners, cards, etc.) reuse the same 930/931
        # total factor slot as real goal/puck totals, but with the prop name appended in
        # parens on both team names -- e.g. "Крылья Советов (офсайды)" (confirmed live
        # 2026-09-04). A real plausible-looking line (unlike the implausible-line case
        # above) can slip through PLAUSIBLE_TOTAL_LINE_RANGE otherwise, showing a fake
        # "match" between two prop labels. No legitimate team name in this feed carries
        # parens, so skip rather than try to enumerate every prop type -- same "skip
        # rather than guess" policy as the implausible-line guard.
        if "(" in team_a or "(" in team_b:
            continue

        factors = factors_by_event.get(event["id"], {})
        start_time_utc = _unix_to_iso(event.get("startTime"))

        if game in handicap_games:
            quotes.extend(_handicap_quotes(game, team_a, team_b, start_time_utc, bookmaker, factors))

        if game in totals_games:
            quotes.extend(_total_ladder_quotes(game, team_a, team_b, start_time_utc, bookmaker, factors))
            continue

        if DRAW_FACTOR in factors:
            continue  # 3-way win market, not supported yet

        odds_a = factors.get(TEAM1_WIN_FACTOR, {}).get("v")
        odds_b = factors.get(TEAM2_WIN_FACTOR, {}).get("v")
        if not odds_a or not odds_b:
            continue

        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, team_a, odds_a))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, team_b, odds_b))

    return quotes


def _total_ladder_quotes(game, team_a, team_b, start_time_utc, bookmaker, factors) -> list[SourceQuote]:
    quotes: list[SourceQuote] = []
    seen: set[float] = set()
    for over_id, under_id in TOTAL_LADDER_PAIRS:
        over, under = factors.get(over_id), factors.get(under_id)
        if not over or not under:
            continue
        odds_over, odds_under = over.get("v"), under.get("v")
        if not odds_over or not odds_under or over.get("pt") is None or under.get("pt") is None:
            continue
        # Over and under must name the same line -- the feed has been seen handing back
        # two different `pt` values for one pair; taking one side's line would mislabel
        # the other's price and show a phantom arb. Skip rather than guess.
        try:
            line, line_under = float(over["pt"]), float(under["pt"])
        except ValueError:
            continue
        if line != line_under or line in seen:
            continue
        if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
            continue  # not a real match goal total (e.g. a special/outright reusing this slot)
        seen.add(line)
        market = f"total_{line}"
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, f"Тотал больше {line}", odds_over, market))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, f"Тотал меньше {line}", odds_under, market))
    return quotes


def _handicap_quotes(game, team_a, team_b, start_time_utc, bookmaker, factors) -> list[SourceQuote]:
    """Emitted as market="hcp" with "H1:<line>"/"H2:<line>" in this book's own team order;
    bot/core/reconcile.py lines them up across books. Half lines only (no push)."""
    quotes: list[SourceQuote] = []
    seen: set[float] = set()
    for t1_id, t2_id in HANDICAP_PAIRS:
        f1, f2 = factors.get(t1_id), factors.get(t2_id)
        if not f1 or not f2:
            continue
        try:
            h1, h2 = float(f1.get("pt")), float(f2.get("pt"))
            o1, o2 = float(f1.get("v")), float(f2.get("v"))
        except (TypeError, ValueError):
            continue
        if h1 != -h2 or (abs(h1) * 2) % 2 != 1 or h1 in seen or o1 <= 1.0 or o2 <= 1.0:
            continue
        seen.add(h1)
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, f"H1:{h1}", o1, "hcp"))
        quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, bookmaker, f"H2:{h2}", o2, "hcp"))
    return quotes


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
