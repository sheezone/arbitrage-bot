import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.arbitrage import OutcomeOdds, calc_arbitrage, calc_stakes, find_best_odds


def test_find_best_odds_picks_highest_per_outcome():
    odds = {
        "Team A": [
            OutcomeOdds("Team A", "BookieX", 1.90),
            OutcomeOdds("Team A", "BookieY", 2.10),
        ],
        "Team B": [
            OutcomeOdds("Team B", "BookieX", 2.05),
            OutcomeOdds("Team B", "BookieY", 1.80),
        ],
    }
    best = find_best_odds(odds)
    best_by_outcome = {o.outcome_name: o for o in best}
    assert best_by_outcome["Team A"].bookmaker == "BookieY"
    assert best_by_outcome["Team A"].odds == 2.10
    assert best_by_outcome["Team B"].bookmaker == "BookieX"
    assert best_by_outcome["Team B"].odds == 2.05


def test_calc_arbitrage_detects_classic_surebet():
    # Classic textbook surebet: 2.10 and 2.05 -> arb_ratio < 1
    odds = {
        "Team A": [OutcomeOdds("Team A", "BookieY", 2.10)],
        "Team B": [OutcomeOdds("Team B", "BookieX", 2.05)],
    }
    result = calc_arbitrage(odds)
    assert result.is_arbitrage
    assert result.arb_ratio < 1.0
    assert result.profit_pct > 0
    # 1/2.10 + 1/2.05 = 0.47619 + 0.48780 = 0.96399 -> profit ~3.73%
    assert round(result.profit_pct, 2) == 3.73


def test_calc_arbitrage_no_opportunity():
    odds = {
        "Team A": [OutcomeOdds("Team A", "BookieX", 1.80)],
        "Team B": [OutcomeOdds("Team B", "BookieY", 1.80)],
    }
    result = calc_arbitrage(odds)
    assert not result.is_arbitrage
    assert result.profit_pct < 0


def test_calc_stakes_equal_payout_regardless_of_outcome():
    best_odds = [
        OutcomeOdds("Team A", "BookieY", 2.10),
        OutcomeOdds("Team B", "BookieX", 2.05),
    ]
    stakes = calc_stakes(100.0, best_odds)
    assert sum(stakes.values()) == 100.0

    payout_a = stakes["Team A"] * 2.10
    payout_b = stakes["Team B"] * 2.05
    assert round(payout_a, 1) == round(payout_b, 1)
    assert payout_a > 100.0  # guaranteed profit above the bankroll


def test_find_best_odds_raises_on_empty_outcome():
    import pytest

    with pytest.raises(ValueError):
        find_best_odds({"Team A": []})
