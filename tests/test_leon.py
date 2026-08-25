import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.leon import parse_events


def _raw(events: list[dict]) -> dict:
    return {"events": {"events": events}}


def _event(family: str, team_a: str, team_b: str, markets: list[dict], kickoff: int = 1787770800000) -> dict:
    return {
        "kickoff": kickoff,
        "competitors": [{"name": team_a}, {"name": team_b}],
        "league": {"sport": {"family": family}},
        "markets": markets,
    }


def _total_market(name: str, line: str, over: float, under: float) -> dict:
    return {
        "name": name,
        "typeTag": "TOTAL",
        "handicap": line,
        "runners": [
            {"tags": ["OVER"], "price": over},
            {"tags": ["UNDER"], "price": under},
        ],
    }


def test_parses_total_market_for_football():
    event = _event("Soccer", "Arsenal", "Chelsea", [_total_market("Тотал", "2.5", 1.9, 1.85)])
    quotes = parse_events("football", _raw([event]))
    assert len(quotes) == 2
    assert {q.market for q in quotes} == {"total_2.5"}
    assert {q.outcome_name for q in quotes} == {"Тотал больше 2.5", "Тотал меньше 2.5"}
    assert all(q.bookmaker == "leon" for q in quotes)


def test_ignores_other_sports():
    event = _event("Basketball", "A", "B", [_total_market("Тотал", "150.5", 1.9, 1.85)])
    assert parse_events("football", _raw([event])) == []


def test_ignores_similarly_named_totals():
    event = _event(
        "Soccer",
        "Arsenal",
        "Chelsea",
        [
            _total_market("Тотал хозяев", "1.5", 1.9, 1.85),
            _total_market("1-й тайм: Тотал", "1", 1.9, 1.85),
        ],
    )
    assert parse_events("football", _raw([event])) == []


def test_rejects_implausible_line():
    event = _event("Soccer", "Arsenal", "Chelsea", [_total_market("Тотал", "23.5", 1.9, 1.85)])
    assert parse_events("football", _raw([event])) == []


def test_hockey_uses_ice_hockey_family():
    event = _event("IceHockey", "CSKA", "SKA", [_total_market("Тотал", "5.5", 1.9, 1.85)])
    quotes = parse_events("hockey", _raw([event]))
    assert len(quotes) == 2
    assert all(q.game == "hockey" for q in quotes)
