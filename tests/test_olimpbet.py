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


def _total_outcome(direction_suffix: str, line: str, price: float, group_position: int = 7, group_name: str = "Доп. Тотал") -> dict:
    return {
        "tableType": "TOTAL",
        "groupPosition": group_position,
        "groupName": group_name,
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


def test_uses_whole_total_ladder_and_dedupes_main_line():
    # Main line (singular "Доп. Тотал") + ladder (plural "Доп. тотал"); the main line also
    # appears in the ladder and must not be emitted twice.
    event = _event(
        "Lyon",
        "Fenerbahce",
        [
            _total_outcome("бол", "2.5", 1.79, group_position=7),
            _total_outcome("мен", "2.5", 2.03, group_position=7),
            _total_outcome("бол", "2.5", 1.79, group_position=8, group_name="Доп. тотал"),
            _total_outcome("мен", "2.5", 2.03, group_position=8, group_name="Доп. тотал"),
            _total_outcome("бол", "1.5", 1.24, group_position=8, group_name="Доп. тотал"),
            _total_outcome("мен", "1.5", 3.86, group_position=8, group_name="Доп. тотал"),
        ],
    )
    quotes = parse_sports_payload("football", _raw("1", "Футбол", [event]))
    assert sorted(q.market for q in quotes) == ["total_1.5", "total_1.5", "total_2.5", "total_2.5"]


def _winner_outcomes(p1: float, p2: float, table: str = "RESULT", draw: float | None = None) -> list[dict]:
    out = [
        {"groupPosition": 1, "tableType": table, "shortName": "П1", "probability": str(p1)},
        {"groupPosition": 1, "tableType": table, "shortName": "П2", "probability": str(p2)},
    ]
    if draw is not None:
        out.append({"groupPosition": 1, "tableType": table, "shortName": "Х", "probability": str(draw)})
    return out


def test_basketball_winner_from_other_table_type():
    event = _event("Детройт", "Бостон", _winner_outcomes(1.81, 2.05, table="OTHER"))
    quotes = parse_sports_payload("basketball", _raw("5", "Баскетбол", [event]))
    assert [(q.outcome_name, q.odds) for q in quotes] == [("Детройт", 1.81), ("Бостон", 2.05)]


def test_winner_skipped_when_draw_priced():
    event = _event("A", "B", _winner_outcomes(2.0, 2.0, draw=3.5))
    assert parse_sports_payload("tennis", _raw("3", "Теннис", [event])) == []


def test_esports_game_picked_by_competition_prefix():
    raw = [{"payload": {"sport": {"id": "112", "name": "Киберспорт"}, "competitionsWithEvents": [
        {"competition": {"name": "Dota 2. BLAST Slam"}, "events": [_event("Spirit", "Nemesis", _winner_outcomes(1.3, 3.2))]},
        {"competition": {"name": "Valorant. Champions"}, "events": [_event("FNC", "SEN", _winner_outcomes(1.8, 2.0))]},
        {"competition": {"name": "Dota 2. PGL. Противостояние игроков"}, "events": [_event("X", "Y", _winner_outcomes(1.9, 1.9))]},
    ]}}]
    assert {q.team_a for q in parse_sports_payload("dota2", raw)} == {"Spirit"}
    assert {q.team_a for q in parse_sports_payload("valorant", raw)} == {"FNC"}


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
