"""Pure arbitrage math: no I/O, no side effects. Easy to unit-test in isolation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OutcomeOdds:
    outcome_name: str
    bookmaker: str
    odds: float


@dataclass(frozen=True)
class ArbitrageResult:
    best_odds: list[OutcomeOdds]
    arb_ratio: float
    profit_pct: float

    @property
    def is_arbitrage(self) -> bool:
        return self.arb_ratio < 1.0


def find_best_odds(odds_by_outcome: dict[str, list[OutcomeOdds]]) -> list[OutcomeOdds]:
    """For each outcome, pick the bookmaker offering the highest odds."""
    best: list[OutcomeOdds] = []
    for outcome_name, quotes in odds_by_outcome.items():
        if not quotes:
            raise ValueError(f"No odds provided for outcome {outcome_name!r}")
        best.append(max(quotes, key=lambda q: q.odds))
    return best


def calc_arbitrage(odds_by_outcome: dict[str, list[OutcomeOdds]]) -> ArbitrageResult:
    """Compute the arbitrage ratio and profit % across the best odds for each outcome.

    arb_ratio = sum(1/odds_i). arb_ratio < 1 means a guaranteed profit regardless of outcome.
    profit_pct = (1/arb_ratio - 1) * 100.
    """
    best = find_best_odds(odds_by_outcome)
    arb_ratio = sum(1.0 / o.odds for o in best)
    profit_pct = (1.0 / arb_ratio - 1.0) * 100.0
    return ArbitrageResult(best_odds=best, arb_ratio=arb_ratio, profit_pct=profit_pct)


def calc_stakes(bankroll: float, best_odds: list[OutcomeOdds]) -> dict[str, float]:
    """Distribute `bankroll` across outcomes so the payout is equal regardless of outcome.

    stake_i = bankroll * (1/odds_i) / arb_ratio
    """
    arb_ratio = sum(1.0 / o.odds for o in best_odds)
    stakes = {}
    for o in best_odds:
        stake = bankroll * (1.0 / o.odds) / arb_ratio
        stakes[o.outcome_name] = round(stake, 2)
    return stakes
