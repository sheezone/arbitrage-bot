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
    quotes = [q for q in parse_line_dump("football", raw) if q.market != "hcp"]  # totals only here
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
    assert [q for q in parse_line_dump("football", raw) if q.market != "hcp"] == []


def test_skips_event_without_total_market():
    hd_no_total = [{"n": "1"}, {"n": "Х"}, {"n": "2"}]
    f_l_no_total = [{"h": 1.9}, {"h": 3.5}, {"h": 1.9}]
    raw = _raw(
        {"1001": {"sid": 1, "c1_id": 10, "c2_id": 20, "time": 0, "hd": hd_no_total, "f_l": f_l_no_total}},
        {"10": "A", "20": "B"},
    )
    assert [q for q in parse_line_dump("football", raw) if q.market != "hcp"] == []


def test_rejects_implausible_line():
    raw = _raw(
        {"1001": {"sid": 1, "c1_id": 10, "c2_id": 20, "time": 0, "hd": _hd(), "f_l": _f_l(1.9, "23.5", 1.9)}},
        {"10": "A", "20": "B"},
    )
    assert [q for q in parse_line_dump("football", raw) if q.market != "hcp"] == []


def test_basketball_winner_taken_only_when_draw_unpriced():
    from bot.providers.zenit import parse_line_dump

    hd = [{"n": n} for n in ["1", "Х", "2", "Фора", "1", "Фора", "2", "М", "Тотал", "Б"]]
    two_way = [{"h": 1.27}, {"h": None}, {"h": 3.76}, {"h": "-8.5"}, {"h": 1.9}, {"h": "8.5"}, {"h": 1.9}, {"h": 1.9}, {"h": "162.5"}, {"h": 1.9}]
    three_way = [{"h": 1.27}, {"h": 12.0}] + two_way[2:]
    raw = {
        "dict": {"cmd": {"1": "A", "2": "B", "3": "C", "4": "D"}},
        "games": {
            "10": {"sid": 3, "c1_id": 1, "c2_id": 2, "time": 1800000000, "hd": hd, "f_l": two_way},
            "11": {"sid": 3, "c1_id": 3, "c2_id": 4, "time": 1800000000, "hd": hd, "f_l": three_way},
        },
    }
    quotes = [q for q in parse_line_dump("basketball", raw) if q.market == "winner"]
    assert [(q.team_a, q.outcome_name, q.odds, q.market) for q in quotes] == [
        ("A", "A", 1.27, "winner"),
        ("A", "B", 3.76, "winner"),
    ]


def test_football_main_handicap_only_on_half_lines():
    from bot.providers.zenit import parse_line_dump

    hd = [{"n": n} for n in ["1", "Х", "2", "1Х", "12", "Х2", "Фора", "1", "Фора", "2", "М", "Тотал", "Б"]]
    half = [{"h": 2.2}, {"h": 3.6}, {"h": 3.2}, {"h": 1.3}, {"h": 1.3}, {"h": 1.7}, {"h": "-1.5"}, {"h": 3.9}, {"h": "1.5"}, {"h": 1.25}, {"h": 1.8}, {"h": "2.5"}, {"h": 2.0}]
    whole = half[:6] + [{"h": "0"}, {"h": 1.6}, {"h": "0"}, {"h": 2.3}] + half[10:]
    raw = {"dict": {"cmd": {"1": "A", "2": "B", "3": "C", "4": "D"}}, "games": {
        "1": {"sid": 1, "c1_id": 1, "c2_id": 2, "time": 1800000000, "hd": hd, "f_l": half},
        "2": {"sid": 1, "c1_id": 3, "c2_id": 4, "time": 1800000000, "hd": hd, "f_l": whole},
    }}
    hcp = [(q.team_a, q.outcome_name, q.odds) for q in parse_line_dump("football", raw) if q.market == "hcp"]
    assert hcp == [("A", "H1:-1.5", 3.9), ("A", "H2:1.5", 1.25)]


def test_esports_split_by_league_prefix_and_outrights_skipped():
    hd = [{"n": n} for n in ["1", "Х", "2"]]
    f_l = [{"h": 1.5}, {}, {"h": 2.5}]
    raw = {
        "dict": {
            "cmd": {"1": "Spirit", "2": "Navi", "3": "T1", "4": "G2", "5": "X", "6": "Y"},
            "league": {"100": "CS2. ESL Pro League", "200": "League of Legends. LCK", "300": "Итоги. CS2. Major. Победитель"},
        },
        "games": {
            "1": {"sid": 7, "lid": 100, "c1_id": 1, "c2_id": 2, "time": 1800000000, "hd": hd, "f_l": f_l},
            "2": {"sid": 7, "lid": 200, "c1_id": 3, "c2_id": 4, "time": 1800000000, "hd": hd, "f_l": f_l},
            "3": {"sid": 7, "lid": 300, "c1_id": 5, "c2_id": 6, "time": 1800000000, "hd": hd, "f_l": f_l},
        },
    }
    assert [(q.team_a, q.outcome_name, q.odds) for q in parse_line_dump("cs2", raw)] == [
        ("Spirit", "Spirit", 1.5), ("Spirit", "Navi", 2.5),
    ]
    assert {q.team_a for q in parse_line_dump("lol", raw)} == {"T1"}
    assert parse_line_dump("dota2", raw) == []
