import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.handlers.commands import _calculator_result_view


def test_shows_profit_for_a_real_arb():
    text, _ = _calculator_result_view(1000, 2.10, 2.05)
    assert "Прибыль" in text
    assert "не вилка" not in text


def test_shows_loss_warning_when_not_an_arb():
    text, _ = _calculator_result_view(1000, 1.5, 1.5)
    assert "не вилка" in text
    assert "Прибыль" not in text


def test_stakes_sum_to_roughly_the_bankroll():
    from bot.core.arbitrage import OutcomeOdds, calc_arbitrage, calc_stakes

    odds_by_outcome = {"1": [OutcomeOdds("1", "—", 2.10)], "2": [OutcomeOdds("2", "—", 2.05)]}
    arb = calc_arbitrage(odds_by_outcome)
    stakes = calc_stakes(1000, arb.best_odds)
    assert abs(sum(stakes.values()) - 1000) < 0.1
