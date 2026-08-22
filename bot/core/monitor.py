from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot

from bot.core import billing
from bot.core.arbitrage import ArbitrageResult, OutcomeOdds, calc_arbitrage, calc_stakes
from bot.core.reconcile import group_quotes, split_by_market, to_arbitrage_input
from bot.core.state import LatestState, MatchSnapshot
from bot.db.repository import Repository
from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote
from bot.providers.surebet import SurebetFinder

logger = logging.getLogger(__name__)


GAME_EMOJI = {
    "cs2": "🔫",
    "dota2": "🎮",
    "lol": "🎮",
    "valorant": "🎮",
    "tennis": "🎾",
    "basketball": "🏀",
    "football": "⚽",
    "hockey": "🏒",
}

MOSCOW_TZ = timezone(timedelta(hours=3))
_MONTHS_RU = [
    "янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек",
]


def format_match_start(start_time_utc: str) -> str:
    """Match kickoff time in Moscow time, e.g. "21 авг, 18:00 МСК". Returns "" when
    unavailable -- Marathon never supplies a full timestamp (see its module docstring),
    and any source can hand back an unparseable/empty value."""
    if not start_time_utc:
        return ""
    try:
        dt = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return ""
    local = dt.astimezone(MOSCOW_TZ)
    return f"{local.day} {_MONTHS_RU[local.month - 1]}, {local.strftime('%H:%M')} МСК"


def within_time_horizon(start_time_utc: str, horizons_days: list[int], now: datetime) -> bool:
    """True if the match starts within the widest of `horizons_days` from `now` (the user
    can check up to 2 of the 1/3/30-day options at once -- narrower selections add nothing
    a wider one doesn't already cover, so only the max matters for filtering). Unknown/
    unparseable start times (Marathon never supplies one) are let through rather than
    hidden -- we can't evaluate what we don't have, so the safer default is to still show it."""
    if not start_time_utc:
        return True
    try:
        start = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return True
    horizon_days = max(horizons_days) if horizons_days else 0
    return start - now <= timedelta(days=horizon_days)


def _bookmakers_hash(best_odds: list[OutcomeOdds]) -> str:
    parts = sorted(f"{o.outcome_name}:{o.bookmaker}:{o.odds}" for o in best_odds)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _format_message(game: str, team_a: str, team_b: str, arb: ArbitrageResult, start_time_utc: str = "") -> str:
    emoji = GAME_EMOJI.get(game, "🏆")
    lines = [
        f"\U0001F4B0 {emoji} Найдена вилка ({game.upper()}): {team_a} vs {team_b}",
    ]
    match_time = format_match_start(start_time_utc)
    if match_time:
        lines.append(f"🕒 {match_time}")
    lines.append(f"Прибыль: {arb.profit_pct:.2f}%")
    lines.append("")
    for outcome in arb.best_odds:
        lines.append(f"  {outcome.outcome_name}: {outcome.odds} @ {outcome.bookmaker}")
    return "\n".join(lines)


def _format_showcase_message(
    game: str, team_a: str, team_b: str, arb: ArbitrageResult, bot_username: str, start_time_utc: str = ""
) -> str:
    emoji = GAME_EMOJI.get(game, "🏆")
    lines = [
        f"\U0001F4B0 {emoji} Вилка ({game.upper()}): {team_a} vs {team_b}",
    ]
    match_time = format_match_start(start_time_utc)
    if match_time:
        lines.append(f"🕒 {match_time}")
    lines.append(f"Прибыль: {arb.profit_pct:.2f}%")
    lines.append("")
    lines.append("Букмекеры и коэффициенты — в боте по подписке.")
    if bot_username:
        lines.append(f"https://t.me/{bot_username}")
    return "\n".join(lines)


# Consecutive empty (or failed) fetches from one source before we log a "probably broken"
# warning -- one bad cycle can just be a transient network hiccup, several in a row for a
# scraped, undocumented endpoint (Fonbet/PARI) most likely means the site changed shape.
STALE_SOURCE_WARNING_STREAK = 3


async def _fetch_all_quotes(
    sources: list[OddsProvider], games: list[str], empty_streaks: dict[str, int]
) -> list[SourceQuote]:
    all_quotes: list[SourceQuote] = []
    for source in sources:
        name = type(source).__name__
        try:
            quotes = await source.fetch_quotes(games)
        except Exception:
            logger.exception("Source %s failed to fetch quotes", name)
            quotes = []

        if quotes:
            empty_streaks[name] = 0
        else:
            empty_streaks[name] = empty_streaks.get(name, 0) + 1
            if empty_streaks[name] == STALE_SOURCE_WARNING_STREAK:
                logger.warning(
                    "Source %s returned no quotes for %d cycles in a row -- if this is a "
                    "scraped/undocumented source (Fonbet, PARI), its endpoint likely changed "
                    "shape and needs re-checking.",
                    name,
                    empty_streaks[name],
                )

        all_quotes.extend(quotes)
    return all_quotes


def _evaluate_group(group: list[SourceQuote]) -> list[tuple[str, str, str, str, ArbitrageResult]]:
    """Returns (game, team_a, team_b, market, arb) for every submarket in this match cluster
    that turns out to be a real arbitrage opportunity (usually zero or one, but a match can
    carry more than one independent market -- see bot/core/reconcile.py split_by_market)."""
    results: list[tuple[str, str, str, str, ArbitrageResult]] = []
    for market, subgroup in split_by_market(group).items():
        if len({q.bookmaker for q in subgroup}) < 2:
            continue

        team_a, team_b, odds_by_outcome = to_arbitrage_input(subgroup)
        if len(odds_by_outcome) < 2 or any(len(v) == 0 for v in odds_by_outcome.values()):
            continue

        arb = calc_arbitrage(odds_by_outcome)
        if not arb.is_arbitrage:
            continue

        results.append((subgroup[0].game, team_a, team_b, market, arb))
    return results


async def _notify_group(
    game: str,
    team_a: str,
    team_b: str,
    arb: ArbitrageResult,
    start_time_utc: str,
    repo: Repository,
    bot: Bot,
    admin_chat_ids: frozenset[int] = frozenset(),
    market: str = "winner",
) -> None:
    match_id = f"{game}:{team_a}:{team_b}:{start_time_utc}:{market}"
    bh = _bookmakers_hash(arb.best_odds)
    if repo.has_seen_opportunity(match_id, bh):
        return

    now = datetime.now(timezone.utc)
    for user in repo.get_active_users():
        if not billing.has_access(user, now, admin_chat_ids):
            continue
        if arb.profit_pct < user.min_profit_pct:
            continue
        if game not in user.watched_games:
            continue
        if not within_time_horizon(start_time_utc, user.time_horizons, now):
            continue

        stakes = calc_stakes(user.bankroll, arb.best_odds)
        message = _format_message(game, team_a, team_b, arb, start_time_utc)
        message += f"\n\nСтавки при банкролле {user.bankroll:.2f}:\n"
        for outcome_name, stake in stakes.items():
            message += f"  {outcome_name}: {stake:.2f}\n"

        try:
            await bot.send_message(user.chat_id, message, protect_content=True)
        except Exception:
            logger.exception("Failed to notify chat_id=%s", user.chat_id)

    repo.mark_opportunity_seen(match_id, bh)


async def _notify_showcase(
    found: list[MatchSnapshot],
    bot: Bot,
    showcase_chat_id: int,
    bot_username: str,
) -> None:
    if not found:
        return
    best = max(found, key=lambda m: m.arb.profit_pct)
    message = _format_showcase_message(best.game, best.team_a, best.team_b, best.arb, bot_username, best.start_time_utc)
    try:
        await bot.send_message(showcase_chat_id, message)
    except Exception:
        logger.exception("Failed to post showcase message to chat_id=%s", showcase_chat_id)


async def run_monitor_loop(
    sources: list[OddsProvider],
    repo: Repository,
    bot: Bot,
    games: list[str],
    poll_interval_seconds: int,
    default_min_profit_pct: float,
    state: LatestState,
    surebet_finder: SurebetFinder | None = None,
    admin_chat_ids: frozenset[int] = frozenset(),
    showcase_chat_id: int | None = None,
    showcase_interval_seconds: int = 600,
    bot_username: str = "",
) -> None:
    empty_streaks: dict[str, int] = {}
    last_showcase_post = 0.0
    while True:
        all_quotes = await _fetch_all_quotes(sources, games, empty_streaks)
        groups = group_quotes(all_quotes)

        found: list[MatchSnapshot] = []
        for group in groups:
            try:
                results = _evaluate_group(group)
            except Exception:
                logger.exception("Failed to evaluate match group")
                continue

            for game, team_a, team_b, market, arb in results:
                found.append(MatchSnapshot(game, team_a, team_b, arb, group[0].start_time_utc))
                try:
                    await _notify_group(
                        game, team_a, team_b, arb, group[0].start_time_utc, repo, bot, admin_chat_ids, market
                    )
                except Exception:
                    logger.exception("Failed to notify match group")

        if surebet_finder is not None:
            try:
                surebet_matches = await surebet_finder.find(games)
            except Exception:
                logger.exception("SureBet finder failed")
                surebet_matches = []

            for game, team_a, team_b, start_time_utc, arb in surebet_matches:
                found.append(MatchSnapshot(game, team_a, team_b, arb, start_time_utc))
                try:
                    await _notify_group(game, team_a, team_b, arb, start_time_utc, repo, bot, admin_chat_ids)
                except Exception:
                    logger.exception("Failed to notify SureBet match")

        if showcase_chat_id is not None and found and time.time() - last_showcase_post >= showcase_interval_seconds:
            await _notify_showcase(found, bot, showcase_chat_id, bot_username)
            last_showcase_post = time.time()

        state.matches = found
        state.updated_at = time.time()

        await asyncio.sleep(poll_interval_seconds)
