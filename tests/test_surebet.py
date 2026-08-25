import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.surebet import parse_records


def _record(sport_id: str, teams: list[str], prong_a: dict, prong_b: dict) -> dict:
    base_a = {"bk": "melbet", "sport_id": sport_id, "teams": teams, "time": 1786917600000}
    base_b = {"bk": "winline", "sport_id": sport_id, "teams": teams, "time": 1786917600000}
    base_a.update(prong_a)
    base_b.update(prong_b)
    return {"group_size": 2, "prongs": [base_a, base_b]}


def test_moneyline_market_uses_team_names():
    data = {
        "records": [
            _record(
                "Dota",
                ["Team Spirit", "Resilience"],
                {"value": 1.5, "type": {"type": "winOnly1"}},
                {"value": 3.5, "type": {"type": "winOnly2"}},
            )
        ]
    }
    results = parse_records(data)
    assert len(results) == 1
    game, team_a, team_b, start, arb = results[0]
    assert game == "dota2"
    assert team_a == "Team Spirit" and team_b == "Resilience"
    assert arb.is_arbitrage
    assert {o.outcome_name for o in arb.best_odds} == {"Team Spirit", "Resilience"}


def test_totals_market_gets_descriptive_label():
    data = {
        "records": [
            _record(
                "Tennis",
                ["Player A", "Player B"],
                {"value": 2.3, "type": {"type": "over", "condition": "10.5", "period": "regularTime"}},
                {"value": 1.8, "type": {"type": "under", "condition": "10.5", "period": "regularTime"}},
            )
        ]
    }
    results = parse_records(data)
    assert len(results) == 1
    labels = {o.outcome_name for o in results[0][4].best_odds}
    assert labels == {"Тотал больше 10.5", "Тотал меньше 10.5"}


def test_recomputed_negative_arbitrage_is_dropped():
    """SureBet said this is a surebet, but our own recompute disagrees -- must not trust
    blindly."""
    data = {
        "records": [
            _record(
                "Basketball",
                ["Home", "Away"],
                {"value": 1.5, "type": {"type": "winOnly1"}},
                {"value": 1.5, "type": {"type": "winOnly2"}},  # sum(1/1.5 + 1/1.5) > 1, not an arb
            )
        ]
    }
    assert parse_records(data) == []


def test_skips_records_with_more_than_two_prongs():
    data = {
        "records": [
            {
                "prongs": [
                    {"bk": "a", "sport_id": "Football", "teams": ["A", "B"], "value": 2.0, "type": {"type": "1"}},
                    {"bk": "b", "sport_id": "Football", "teams": ["A", "B"], "value": 3.5, "type": {"type": "X"}},
                    {"bk": "c", "sport_id": "Football", "teams": ["A", "B"], "value": 4.0, "type": {"type": "2"}},
                ]
            }
        ]
    }
    assert parse_records(data) == []


def test_skips_unknown_sport_id():
    data = {
        "records": [
            _record(
                "Rugby",
                ["A", "B"],
                {"value": 1.5, "type": {"type": "winOnly1"}},
                {"value": 3.0, "type": {"type": "winOnly2"}},
            )
        ]
    }
    assert parse_records(data) == []


def test_football_rejects_pure_win1_win2_pair_since_a_draw_would_lose_both():
    data = {
        "records": [
            _record(
                "Football",
                ["Arsenal", "Chelsea"],
                {"value": 1.8, "type": {"type": "win1"}},
                {"value": 4.5, "type": {"type": "win2"}},
            )
        ]
    }
    assert parse_records(data) == []


def test_football_accepts_win_plus_double_chance_since_it_covers_the_draw():
    data = {
        "records": [
            _record(
                "Football",
                ["Arsenal", "Chelsea"],
                {"value": 2.3, "type": {"type": "win1"}},
                {"value": 1.8, "type": {"type": "_x2"}},
            )
        ]
    }
    results = parse_records(data)
    assert len(results) == 1
    assert results[0][0] == "football"


def test_hockey_rejects_pure_win1_win2_pair_since_a_draw_would_lose_both():
    data = {
        "records": [
            _record(
                "Hockey",
                ["CSKA", "SKA"],
                {"value": 1.8, "type": {"type": "win1"}},
                {"value": 4.5, "type": {"type": "win2"}},
            )
        ]
    }
    assert parse_records(data) == []


def test_hockey_totals_market_works():
    data = {
        "records": [
            _record(
                "Hockey",
                ["CSKA", "SKA"],
                {"value": 2.1, "type": {"type": "over", "condition": "5.5", "period": "regularTime"}},
                {"value": 2.2, "type": {"type": "under", "condition": "5.5", "period": "regularTime"}},
            )
        ]
    }
    results = parse_records(data)
    assert len(results) == 1
    assert results[0][0] == "hockey"


def test_football_totals_market_still_works():
    data = {
        "records": [
            _record(
                "Football",
                ["Arsenal", "Chelsea"],
                {"value": 2.1, "type": {"type": "over", "condition": "2.5", "period": "regularTime"}},
                {"value": 2.2, "type": {"type": "under", "condition": "2.5", "period": "regularTime"}},
            )
        ]
    }
    results = parse_records(data)
    assert len(results) == 1
    assert results[0][0] == "football"
