import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers._line_platform import parse_line_dump

GAME_TO_CATEGORIES = {"cs2": [20], "valorant": [21]}
GAME_TO_PARENT_SPORT = {"basketball": 3, "hockey": 2}
EXCLUDE_CATEGORY_IDS = frozenset({119, 165})


def _raw(sports, events, custom_factors):
    return {"sports": sports, "events": events, "customFactors": custom_factors}


def test_matches_by_sport_category_id_within_shared_parent():
    raw = _raw(
        sports=[{"id": 100, "kind": "segment", "parentId": 29086, "sportCategoryId": 21, "name": "Valorant. Bo3"}],
        events=[{"id": 1, "sportId": 100, "team1": "A", "team2": "B", "place": "line", "startTime": 0}],
        custom_factors=[{"e": 1, "factors": [{"f": 921, "v": 1.8}, {"f": 923, "v": 2.0}]}],
    )
    quotes = parse_line_dump(raw, GAME_TO_CATEGORIES, bookmaker="fonbet")
    assert {q.game for q in quotes} == {"valorant"}
    assert len(quotes) == 2


def test_matches_real_basketball_by_parent_id_when_category_unset():
    raw = _raw(
        sports=[{"id": 200, "kind": "segment", "parentId": 3, "sportCategoryId": None, "name": "NBA"}],
        events=[{"id": 2, "sportId": 200, "team1": "Lakers", "team2": "Celtics", "place": "line", "startTime": 0}],
        custom_factors=[{"e": 2, "factors": [{"f": 921, "v": 1.5}, {"f": 923, "v": 2.6}]}],
    )
    quotes = parse_line_dump(
        raw, {}, bookmaker="fonbet", game_to_parent_sport=GAME_TO_PARENT_SPORT, exclude_category_ids=EXCLUDE_CATEGORY_IDS
    )
    assert {q.game for q in quotes} == {"basketball"}
    assert len(quotes) == 2


def test_excludes_virtual_esports_simulation_under_same_parent():
    raw = _raw(
        sports=[
            {"id": 200, "kind": "segment", "parentId": 3, "sportCategoryId": None, "name": "NBA"},
            {"id": 201, "kind": "segment", "parentId": 3, "sportCategoryId": 119, "name": "NBA 2K26. H2H"},
        ],
        events=[
            {"id": 2, "sportId": 200, "team1": "Lakers", "team2": "Celtics", "place": "line", "startTime": 0},
            {"id": 3, "sportId": 201, "team1": "VirtualA", "team2": "VirtualB", "place": "line", "startTime": 0},
        ],
        custom_factors=[
            {"e": 2, "factors": [{"f": 921, "v": 1.5}, {"f": 923, "v": 2.6}]},
            {"e": 3, "factors": [{"f": 921, "v": 1.1}, {"f": 923, "v": 5.0}]},
        ],
    )
    quotes = parse_line_dump(
        raw, {}, bookmaker="fonbet", game_to_parent_sport=GAME_TO_PARENT_SPORT, exclude_category_ids=EXCLUDE_CATEGORY_IDS
    )
    assert {(q.team_a, q.team_b) for q in quotes} == {("Lakers", "Celtics")}


def test_draw_factor_still_skips_event_for_parent_matched_games():
    raw = _raw(
        sports=[{"id": 300, "kind": "segment", "parentId": 2, "sportCategoryId": None, "name": "KHL"}],
        events=[{"id": 4, "sportId": 300, "team1": "A", "team2": "B", "place": "line", "startTime": 0}],
        custom_factors=[{"e": 4, "factors": [{"f": 921, "v": 2.0}, {"f": 922, "v": 3.5}, {"f": 923, "v": 2.1}]}],
    )
    quotes = parse_line_dump(
        raw, {}, bookmaker="fonbet", game_to_parent_sport=GAME_TO_PARENT_SPORT, exclude_category_ids=EXCLUDE_CATEGORY_IDS
    )
    assert quotes == []
