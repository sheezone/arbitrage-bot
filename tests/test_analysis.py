import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.analysis.catalog import Option, parse_catalog, settle
from bot.analysis.results import find_score, parse_results

NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)


def _f(fid, v, pt=None):
    d = {"f": fid, "v": v}
    if pt is not None:
        d["pt"] = pt
    return d


def test_catalog_keeps_real_matches_with_1x2_and_skips_outrights_props_and_aggregates():
    start = int((NOW + timedelta(hours=5)).timestamp())
    raw = {
        "sports": [
            {"id": 10, "kind": "segment", "parentId": 1, "name": "Англия. Премьер-Лига"},
            {"id": 11, "kind": "segment", "parentId": 1, "name": "Англия. Премьер-Лига. Итоги турнира"},
            {"id": 12, "kind": "segment", "parentId": 1, "name": "Венгрия. 3-я лига"},
        ],
        "events": [
            {"id": 1, "place": "line", "sportId": 12, "team1": "A", "team2": "B", "startTime": start},
            {"id": 2, "place": "line", "sportId": 10, "team1": "Арсенал", "team2": "Челси", "startTime": start},
            {"id": 3, "place": "line", "sportId": 11, "team1": "X", "team2": "Y", "startTime": start},
            {"id": 4, "place": "line", "sportId": 10, "team1": "Хозяева", "team2": "Гости", "startTime": start},
            {"id": 5, "place": "line", "sportId": 10, "team1": "Арсенал (угловые)", "team2": "Челси (угловые)", "startTime": start},
        ],
        "customFactors": [
            {"e": e, "factors": [_f(921, 2.1), _f(922, 3.4), _f(923, 3.5), _f(930, 1.9, "2.5"), _f(931, 1.9, "2.5"),
                                 _f(927, 1.8, "-0.5"), _f(928, 2.0, "+0.5")]}
            for e in (1, 2, 3, 4, 5)
        ],
    }
    ms = parse_catalog(raw, NOW)
    assert [(m.id, m.league) for m in ms] == [("2", "Англия. Премьер-Лига"), ("1", "Венгрия. 3-я лига")]
    ids = [o.id for o in ms[0].options]
    assert ids[:3] == ["1", "X", "2"] and "TO2.5" in ids and "TU2.5" in ids and "H1-0.5" in ids and "H2+0.5" in ids


def test_settle_all_kinds():
    assert settle(Option("1", "П1", 2.0, "1x2"), 2, 1) == "win"
    assert settle(Option("X", "Ничья", 3.0, "1x2"), 2, 1) == "lose"
    assert settle(Option("TO2.5", "", 1.9, "total_over", 2.5), 2, 1) == "win"
    assert settle(Option("TU2.5", "", 1.9, "total_under", 2.5), 2, 1) == "lose"
    assert settle(Option("H1-1.5", "", 1.9, "handicap1", -1.5), 2, 1) == "lose"
    assert settle(Option("H2+1.5", "", 1.9, "handicap2", 1.5), 2, 1) == "win"


def test_results_match_by_names_and_swap_orientation():
    raw = {"reply": {"sports": {"1": {"chmps": {"1": {"evts": {
        "9": {"name_ht": "Челси", "name_at": "Арсенал", "sc_ev": "3:1", "date_ev": "2026-10-02 20:00:00"},
    }}}}}}}
    res = parse_results(raw)
    start = datetime(2026, 10, 2, 17, tzinfo=timezone.utc)  # 20:00 MSK
    assert find_score("Арсенал", "Челси", start, res) == (1, 3)
    assert find_score("Ливерпуль", "Эвертон", start, res) is None


def test_build_expresses_uses_distinct_medium_high_picks_within_total_range():
    from bot.analysis.picks import build_expresses

    def p(i, odds, conf="средняя"):
        return {"match_id": f"m{i}", "odds": odds, "confidence": conf}

    picks = [p(1, 1.5), p(2, 1.6, "высокая"), p(3, 1.7), p(4, 1.9, "низкая"), p(5, 3.0)]
    ex = build_expresses(picks)
    assert ex and all(2.0 <= e["total_odds"] <= 6.0 for e in ex)
    legs = [leg["match_id"] for e in ex for leg in e["legs"]]
    assert len(legs) == len(set(legs)) and "m4" not in legs and "m5" not in legs


def test_match_by_teams_short_names_either_order_and_rejects_half_match():
    from bot.analysis.catalog import FootballMatch, match_by_teams

    t = datetime(2026, 10, 3, tzinfo=timezone.utc)
    line = [FootballMatch("1", "Реал Мадрид", "Барселона", t, "Испания"), FootballMatch("2", "Реал Сосьедад", "Бетис", t, "Испания")]
    assert match_by_teams(line, "Барселона", "Реал Мадрид").id == "1"
    assert match_by_teams(line, "реал сосьедад", "бетис").id == "2"
    assert match_by_teams(line, "Барселона", "Челси") is None


def test_market_probabilities_remove_margin():
    from bot.analysis.ai import _normalise, market_probabilities
    from bot.analysis.catalog import FootballMatch

    m = FootballMatch("1", "A", "B", NOW, "L", [Option("1", "", 2.0, "1x2"), Option("X", "", 3.5, "1x2"), Option("2", "", 3.8, "1x2")])
    p = market_probabilities(m)
    assert sum(p.values()) == 100 and p["p1"] > p["p2"] > 0
    assert _normalise({"p1": 50, "x": 30, "p2": 30}) == {"p1": 45, "x": 27, "p2": 28}


def test_express_message_full_and_teaser():
    from bot.analysis.picks import express_key, express_message

    legs = [{"match_id": "b", "team_a": "Зенит", "team_b": "Спартак", "start_utc": "2026-10-04T16:00:00+00:00",
             "label": "П1 (Зенит)", "odds": 1.8, "confidence": "средняя"},
            {"match_id": "a", "team_a": "Реал", "team_b": "Барселона", "start_utc": "2026-10-04T19:00:00+00:00",
             "label": "Тотал больше 2.5", "odds": 1.7, "confidence": "высокая"}]
    e = {"legs": legs, "total_odds": 3.06}
    assert express_key(e) == "a|b"
    full = express_message(e, True)
    assert "Ваш экспресс готов" in full and "Зенит — Спартак" in full and "@ <b>1.80</b>" in full and "3.06" in full
    teaser = express_message(e, False)
    assert "Экспресс дня готов" in teaser and "Зенит" not in teaser
