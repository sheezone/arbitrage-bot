import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.melbet import parse_events


def _event(stakes: list[dict], team_a: str = "Arsenal", team_b: str = "Chelsea") -> dict:
    return {
        "HT": team_a,
        "AT": team_b,
        "D": "2026-08-22T11:30:00Z",
        "StakeTypes": [{"Id": 3, "N": "Тотал", "Stakes": stakes}],
    }


def test_parses_total_market_over_under_pair():
    event = _event(
        [
            {"N": "Больше", "A": 2.5, "F": 1.9},
            {"N": "Меньше", "A": 2.5, "F": 1.95},
        ]
    )
    quotes = parse_events("football", [event])
    assert len(quotes) == 2
    assert {q.market for q in quotes} == {"total_2.5"}
    assert {q.outcome_name for q in quotes} == {"Тотал больше 2.5", "Тотал меньше 2.5"}
    assert all(q.game == "football" for q in quotes)
    assert all(q.bookmaker == "melbet" for q in quotes)
    assert all(q.start_time_utc == "2026-08-22T11:30:00Z" for q in quotes)


def test_emits_one_market_per_line_offered():
    event = _event(
        [
            {"N": "Больше", "A": 1.5, "F": 1.2},
            {"N": "Меньше", "A": 1.5, "F": 4.0},
            {"N": "Больше", "A": 2.5, "F": 1.9},
            {"N": "Меньше", "A": 2.5, "F": 1.95},
            {"N": "Больше", "A": 3.5, "F": 3.5},
            {"N": "Меньше", "A": 3.5, "F": 1.3},
        ]
    )
    quotes = parse_events("football", [event])
    assert {q.market for q in quotes} == {"total_1.5", "total_2.5", "total_3.5"}
    assert len(quotes) == 6


def test_skips_event_missing_total_market():
    event = {"HT": "A", "AT": "B", "D": "", "StakeTypes": [{"Id": 1, "N": "Исход", "Stakes": []}]}
    assert parse_events("football", [event]) == []


def test_rejects_implausible_line():
    event = _event(
        [
            {"N": "Больше", "A": 23.5, "F": 1.9},
            {"N": "Меньше", "A": 23.5, "F": 1.95},
        ]
    )
    assert parse_events("football", [event]) == []


def test_skips_event_missing_team_names():
    event = _event([{"N": "Больше", "A": 2.5, "F": 1.9}, {"N": "Меньше", "A": 2.5, "F": 1.95}], team_a="", team_b="Chelsea")
    assert parse_events("football", [event]) == []


def test_line_formatting_matches_across_int_and_float_valued_lines():
    """Melbet's JSON can hand back a whole-number line as a JS int (e.g. 2, not 2.0) --
    the market key must still come out identical to what Fonbet/Marathon produce for the
    same line (e.g. "total_2.0"), or an otherwise-real arbitrage would never be matched."""
    event = _event([{"N": "Больше", "A": 2, "F": 1.9}, {"N": "Меньше", "A": 2, "F": 1.95}])
    quotes = parse_events("football", [event])
    assert {q.market for q in quotes} == {"total_2.0"}
