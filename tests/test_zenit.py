import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.zenit import parse_line_dump


def _raw(games: dict, cmd: dict) -> dict:
    return {"games": games, "dict": {"cmd": cmd}}


def _hd():
    return [
        {"n": "1"}, {"n": "Х"}, {"n": "2"}, {"n": "1Х"}, {"n": "12"}, {"n": "Х2"},
        {"n": "Фора"}, {"n": "1"}, {"n": "Фора"}, {"n": "2"},
        {"n": "М"}, {"n": "Тотал"}, {"n": "Б"},
    ]


def _f_l(under: float, line: str, over: float):
    return [
        {"h": 1.5}, {"h": 3.5}, {"h": 4.0}, {}, {}, {},
        {"h": "-1.5"}, {"h": 1.9}, {"h": "1.5"}, {"h": 1.9},
        {"h": under}, {"h": line}, {"h": over},
    ]


def test_parses_total_market_for_football():
    raw = _raw(
        {
            "1001": {
                "sid": 1, "c1_id": 10, "c2_id": 20, "time": 1787388900,
                "hd": _hd(), "f_l": _f_l(1.95, "2.5", 1.85),
            }
        },
        {"10": "Arsenal", "20": "Chelsea"},
    )
    quotes = parse_line_dump("football", raw)
    assert len(quotes) == 2
    assert {q.market for q in quotes} == {"total_2.5"}
    assert {q.outcome_name for q in quotes} == {"Тотал больше 2.5", "Тотал меньше 2.5"}
    over = next(q for q in quotes if "больше" in q.outcome_name)
    under = next(q for q in quotes if "меньше" in q.outcome_name)
    assert over.odds == 1.85
    assert under.odds == 1.95
    assert over.team_a == "Arsenal" and over.team_b == "Chelsea"
    assert over.bookmaker == "zenit"


def test_filters_by_sport_id():
    raw = _raw(
        {
            "1001": {"sid": 1, "c1_id": 10, "c2_id": 20, "time": 0, "hd": _hd(), "f_l": _f_l(1.9, "2.5", 1.9)},
            "1002": {"sid": 2, "c1_id": 30, "c2_id": 40, "time": 0, "hd": _hd(), "f_l": _f_l(1.9, "5.5", 1.9)},
        },
        {"10": "A", "20": "B", "30": "C", "40": "D"},
    )
    quotes = parse_line_dump("hockey", raw)
    assert {q.game for q in quotes} == {"hockey"}
    assert {q.market for q in quotes} == {"total_5.5"}


def test_skips_event_missing_competitor_names():
    raw = _raw(
        {"1001": {"sid": 1, "c1_id": 10, "c2_id": 999, "time": 0, "hd": _hd(), "f_l": _f_l(1.9, "2.5", 1.9)}},
        {"10": "A"},
    )
    assert parse_line_dump("football", raw) == []


def test_skips_event_without_total_market():
    hd_no_total = [{"n": "1"}, {"n": "Х"}, {"n": "2"}]
    f_l_no_total = [{"h": 1.9}, {"h": 3.5}, {"h": 1.9}]
    raw = _raw(
        {"1001": {"sid": 1, "c1_id": 10, "c2_id": 20, "time": 0, "hd": hd_no_total, "f_l": f_l_no_total}},
        {"10": "A", "20": "B"},
    )
    assert parse_line_dump("football", raw) == []


def test_rejects_implausible_line():
    raw = _raw(
        {"1001": {"sid": 1, "c1_id": 10, "c2_id": 20, "time": 0, "hd": _hd(), "f_l": _f_l(1.9, "23.5", 1.9)}},
        {"10": "A", "20": "B"},
    )
    assert parse_line_dump("football", raw) == []
