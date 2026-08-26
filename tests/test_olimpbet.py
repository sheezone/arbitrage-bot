import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.olimpbet import parse_sports_payload


def _raw(sport_id: str, sport_name: str, events: list[dict]) -> list[dict]:
    return [
        {
            "payload": {
                "sport": {"id": sport_id, "name": sport_name},
                "competitionsWithEvents": [{"events": events}],
            }
        }
    ]


def _total_outcome(direction_suffix: str, line: str, price: float, group_position: int = 7) -> dict:
    return {
        "tableType": "TOTAL",
        "groupPosition": group_position,
        "param": line,
        "unprocessedName": f"Тотал ({line}) {direction_suffix}",
        "probability": str(price),
    }


def _event(team_a: str, team_b: str, outcomes: list[dict], start: int = 1787770800) -> dict:
    return {"team1Name": team_a, "team2Name": team_b, "startDateTime": start, "outcomes": outcomes}


def test_parses_main_total_group_for_football():
    event = _event("Lyon", "Fenerbahce", [_total_outcome("бол", "2.5", 1.79), _total_outcome("мен", "2.5", 2.03)])
    quotes = parse_sports_payload("football", _raw("1", "Футбол", [event]))
    assert len(quotes) == 2
    assert {q.market for q in quotes} == {"total_2.5"}
    assert {q.outcome_name for q in quotes} == {"Тотал больше 2.5", "Тотал меньше 2.5"}
    assert all(q.bookmaker == "olimpbet" for q in quotes)


def test_ignores_alternate_line_ladder_group_8():
    event = _event(
        "Lyon",
        "Fenerbahce",
        [
            _total_outcome("бол", "2.5", 1.79, group_position=7),
            _total_outcome("мен", "2.5", 2.03, group_position=7),
            _total_outcome("бол", "1.5", 1.24, group_position=8),
            _total_outcome("мен", "1.5", 3.86, group_position=8),
        ],
    )
    quotes = parse_sports_payload("football", _raw("1", "Футбол", [event]))
    assert {q.market for q in quotes} == {"total_2.5"}


def test_skips_corner_prop_pseudo_events():
    event = _event("УГЛ Al Ahli", "УГЛ Auckland", [_total_outcome("бол", "9.5", 1.96), _total_outcome("мен", "9.5", 1.75)])
    assert parse_sports_payload("football", _raw("1", "Футбол", [event])) == []


def test_skips_event_missing_total_market():
    event = _event("Lyon", "Fenerbahce", [])
    assert parse_sports_payload("football", _raw("1", "Футбол", [event])) == []


def test_rejects_implausible_line():
    event = _event("Lyon", "Fenerbahce", [_total_outcome("бол", "23.5", 1.79), _total_outcome("мен", "23.5", 2.03)])
    assert parse_sports_payload("football", _raw("1", "Футбол", [event])) == []


def test_hockey_uses_sport_id_2():
    event = _event("CSKA", "SKA", [_total_outcome("бол", "5.5", 1.9), _total_outcome("мен", "5.5", 1.85)])
    quotes = parse_sports_payload("hockey", _raw("2", "Хоккей", [event]))
    assert len(quotes) == 2
    assert all(q.game == "hockey" for q in quotes)


def test_ignores_other_sports():
    event = _event("A", "B", [_total_outcome("бол", "150.5", 1.9), _total_outcome("мен", "150.5", 1.85)])
    assert parse_sports_payload("football", _raw("5", "Баскетбол", [event])) == []


def _result_outcome(name: str, price: float) -> dict:
    return {"tableType": "RESULT", "groupPosition": 1, "unprocessedName": name, "probability": str(price)}


def test_volleyball_uses_result_market():
    event = _event("Spain", "Portugal", [_result_outcome("Spain", 1.75), _result_outcome("Portugal", 2.01)])
    quotes = parse_sports_payload("volleyball", _raw("10", "Волейбол", [event]))
    assert len(quotes) == 2
    assert {q.outcome_name for q in quotes} == {"Spain", "Portugal"}
    assert {q.odds for q in quotes} == {1.75, 2.01}


def test_volleyball_skips_when_draw_outcome_present():
    event = _event(
        "Spain", "Portugal",
        [_result_outcome("Spain", 1.75), _result_outcome("Ничья", 25.0), _result_outcome("Portugal", 2.01)],
    )
    assert parse_sports_payload("volleyball", _raw("10", "Волейбол", [event])) == []


def test_boxing_uses_total_rounds_market_ignoring_group_position():
    event = _event(
        "Reeves", "Firth",
        [
            _result_outcome("Reeves", 1.13),
            _result_outcome("Ничья", 25.0),
            _result_outcome("Firth", 5.93),
            _total_outcome("бол", "2.5", 2.03, group_position=3),
            _total_outcome("мен", "2.5", 1.75, group_position=3),
        ],
    )
    quotes = parse_sports_payload("boxing", _raw("12", "Бокс", [event]))
    assert len(quotes) == 2
    assert {q.market for q in quotes} == {"total_2.5"}


def test_mma_picks_lowest_line_when_multiple_totals_present():
    event = _event(
        "A", "B",
        [
            _total_outcome("бол", "2.5", 2.03, group_position=4),
            _total_outcome("мен", "2.5", 1.75, group_position=4),
            _total_outcome("бол", "3.5", 1.5, group_position=3),
            _total_outcome("мен", "3.5", 2.5, group_position=3),
        ],
    )
    quotes = parse_sports_payload("mma", _raw("96", "MMA", [event]))
    assert {q.market for q in quotes} == {"total_2.5"}


def test_boxing_skips_event_with_no_total_market():
    event = _event("Reeves", "Firth", [_result_outcome("Reeves", 1.13), _result_outcome("Firth", 5.93)])
    assert parse_sports_payload("boxing", _raw("12", "Бокс", [event])) == []
