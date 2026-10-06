"""The AI's own virtual bankroll (owner 2026-10-06): "у тебя есть виртуальные 10000 руб,
сам анализируй и ставь, задача — заработать как можно больше за неделю". Every real,
in-the-line pick the AI itself rates as "уверенная" (confidence >= SURE_CONFIDENCE, same
bar as the public "Уверенная ставка ИИ" label) gets a stake sized off the CURRENT
balance, so a losing streak shrinks future stakes automatically. One bet per match ever
(ai_bets.match_id is the primary key) -- re-analysing a cached match never bets twice.

Admin-only display (bot/webapp/api.py's /api/admin/ai_bankroll); nothing here touches
real money or any user-facing stake.
"""
from __future__ import annotations

from bot.analysis.catalog import FootballMatch, Option
from bot.analysis.ai import SURE_CONFIDENCE
from bot.db.repository import Repository

START_BANKROLL = 10_000.0
# % of the CURRENT balance staked per bet, by confidence -- deliberately small (bankroll
# management 101: survive a cold streak) rather than chasing the headline weekly target
# with oversized bets.
STAKE_PCT = {"средняя": 0.03, "высокая": 0.05, "очень высокая": 0.08}
MIN_STAKE, MAX_STAKE = 50.0, 1500.0


def place_bet(repo: Repository, match: FootballMatch, option: Option, confidence: str) -> None:
    if confidence not in SURE_CONFIDENCE or repo.ai_bet_exists(match.id):
        return
    balance = repo.ai_bankroll_balance()
    if balance < MIN_STAKE:
        return  # busted -- stop digging until(/unless) the owner tops it back up
    stake = round(min(MAX_STAKE, max(MIN_STAKE, balance * STAKE_PCT[confidence])), 2)
    repo.place_ai_bet(
        match.id, match.team_a, match.team_b, match.sport, match.league,
        option.label, option.odds, confidence, stake, match.start_utc.isoformat(),
    )
