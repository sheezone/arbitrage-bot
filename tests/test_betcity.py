import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.betcity import parse_line


def _ev(ht, at, main):
    return {"name_ht": ht, "name_at": at, "date_ev": 1800000000, "main": main}


def _win(p1, p2, x=None):
    b = {"P1": {"kf": p1}, "P2": {"kf": p2}}
    if x:
        b["X"] = {"kf": x}
    return {"69": {"name": "Фактический исход", "data": {"1": {"blocks": {"Wm": b}}}}}


def _raw(sport_id, champs):
    return {"reply": {"sports": {sport_id: {"chmps": champs}}}}


def test_football_total_and_half_handicap_stat_markets_ignored():
    main = {
        "72": {"name": "Тотал", "data": {"1": {"blocks": {"T1m": {"Tm": {"kf": 1.7}, "Tb": {"kf": 2.2}, "Tot": 2.5}}}}},
        "71": {"name": "Фора", "data": {"1": {"blocks": {"F1m": {"Kf_F1": {"kf": 1.9, "lv": -0.5}, "Kf_F2": {"kf": 1.95, "lv": 0.5}}}}}},
        **_win(2.0, 3.0, 3.3),
    }
    corners = {"72": {"name": "УГЛ. Тотал", "data": {"1": {"blocks": {"T1m": {"Tm": {"kf": 1.8}, "Tb": {"kf": 1.9}, "Tot": 9.5}}}}}}
    raw = _raw("1", {"10": {"name_ch": "РПЛ", "evts": {"1": _ev("Спартак", "ЦСКА", main), "2": _ev("A", "B", corners), "3": {"name_ht": "Победитель", "main": main}}}})
    got = [(q.outcome_name, q.odds, q.market) for q in parse_line("football", raw)]
    assert got == [("H1:-0.5", 1.9, "hcp"), ("H2:0.5", 1.95, "hcp"), ("Тотал больше 2.5", 2.2, "total_2.5"), ("Тотал меньше 2.5", 1.7, "total_2.5")]


def test_integer_handicap_skipped():
    main = {"71": {"name": "Фора", "data": {"1": {"blocks": {"F1m": {"Kf_F1": {"kf": 1.9, "lv": 0}, "Kf_F2": {"kf": 1.9, "lv": 0}}}}}}}
    assert parse_line("football", _raw("1", {"1": {"name_ch": "X", "evts": {"1": _ev("A", "B", main)}}})) == []


def test_esports_winner_by_champ_prefix_draw_rejected():
    raw = _raw("73", {
        "1": {"name_ch": "CS2. ESL (матчи из 3-х карт)", "evts": {"1": _ev("Spirit", "Navi", _win(1.5, 2.6))}},
        "2": {"name_ch": "Dota 2. BLAST", "evts": {"1": _ev("Tundra", "Liquid", _win(1.8, 2.0))}},
        "3": {"name_ch": "CS2. Draw cup", "evts": {"1": _ev("X", "Y", _win(1.8, 2.0, 5.0))}},
    })
    assert [(q.team_a, q.outcome_name, q.odds) for q in parse_line("cs2", raw)] == [("Spirit", "Spirit", 1.5), ("Spirit", "Navi", 2.6)]
    assert {q.team_a for q in parse_line("dota2", raw)} == {"Tundra"}
