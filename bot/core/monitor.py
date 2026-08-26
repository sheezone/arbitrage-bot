from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

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
    "boxing": "🥊",
    "mma": "🥋",
    "volleyball": "🏐",
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


HORIZON_UNDER_24H = 1
HORIZON_OVER_24H = 2


def within_time_horizon(start_time_utc: str, selected_buckets: list[int], now: datetime) -> bool:
    """Two buckets: HORIZON_UNDER_24H (match starts within the next 24h) and
    HORIZON_OVER_24H (starts later than that, or already started/unknown timing is
    irrelevant here). True if the match's bucket is one of `selected_buckets`. Unknown/
    unparseable start times (Marathon never supplies one) are let through rather than
    hidden -- we can't evaluate what we don't have, so the safer default is to still show it."""
    if not start_time_utc:
        return True
    try:
        start = datetime.fromisoformat(start_time_utc.replace("Z", "+00:00"))
    except ValueError:
        return True
    bucket = HORIZON_UNDER_24H if start - now <= timedelta(hours=24) else HORIZON_OVER_24H
    return bucket in selected_buckets


def _bookmakers_hash(best_odds: list[OutcomeOdds]) -> str:
    parts = sorted(f"{o.outcome_name}:{o.bookmaker}:{o.odds}" for o in best_odds)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


SEND_RETRY_ATTEMPTS = 3
SEND_RETRY_DELAY_SECONDS = 3


async def _send_message_with_retries(bot: Bot, chat_id: int, text: str, **kwargs) -> None:
    """Confirmed live: this VPS's network path to api.telegram.org intermittently stalls
    for 100+ seconds (not a code bug -- a raw `curl` to api.telegram.org reproduced the
    same multi-minute hang). A single failed send used to just drop that notification
    silently; retrying a few times gives a transient stall a chance to clear before we
    give up on it."""
    last_error: Exception | None = None
    for attempt in range(1, SEND_RETRY_ATTEMPTS + 1):
        try:
            await bot.send_message(chat_id, text, **kwargs)
            return
        except TelegramNetworkError as e:
            last_error = e
            logger.warning(
                "send_message to chat_id=%s failed (attempt %d/%d): %s", chat_id, attempt, SEND_RETRY_ATTEMPTS, e
            )
            if attempt < SEND_RETRY_ATTEMPTS:
                await asyncio.sleep(SEND_RETRY_DELAY_SECONDS)
    raise last_error


# Alternating markers so each leg of the arb reads as its own row rather than blurring
# together -- confirmed live this matters most for two-line total markets ("Тотал больше
# X" / "Тотал меньше X"), which used to get squashed onto one comma-joined line.
_OUTCOME_MARKERS = ["📈", "📉", "🔹", "🔸"]


def format_odds_lines(best_odds: list[OutcomeOdds]) -> list[str]:
    lines = []
    for i, outcome in enumerate(best_odds):
        marker = _OUTCOME_MARKERS[i % len(_OUTCOME_MARKERS)]
        name = html.escape(outcome.outcome_name)
        bookmaker = html.escape(outcome.bookmaker.upper())
        lines.append(f"{marker} <b>{name}</b>: {outcome.odds} @ <b>{bookmaker}</b>")
    return lines


def format_stakes_lines(stakes: dict) -> list[str]:
    return [f"    ▫️ {html.escape(outcome_name)}: <b>{stake:.2f}</b>" for outcome_name, stake in stakes.items()]


def _format_message(
    game: str, team_a: str, team_b: str, arb: ArbitrageResult, start_time_utc: str = "", bankroll: float | None = None
) -> str:
    emoji = GAME_EMOJI.get(game, "🏆")
    lines = [
        f"💰 {emoji} <b>Найдена вилка</b> ({game.upper()})",
        f"⚔️ <b>{html.escape(team_a)}</b> vs <b>{html.escape(team_b)}</b>",
    ]
    match_time = format_match_start(start_time_utc)
    if match_time:
        lines.append(f"🕒 {match_time}")
    lines.append(f"🚀 Прибыль: <b>{arb.profit_pct:.2f}%</b>")
    if bankroll is not None:
        # The guaranteed profit is the same no matter which leg wins -- that's the whole
        # point of an arb -- so this is a single number, not a per-outcome range.
        profit_amount = bankroll * arb.profit_pct / 100
        lines.append(f"💸 Возможный выигрыш: <b>{profit_amount:.2f}</b>")
    lines.append("")
    lines.append("<blockquote>" + "\n".join(format_odds_lines(arb.best_odds)) + "</blockquote>")
    return "\n".join(lines)


def _format_showcase_message(
    game: str, team_a: str, team_b: str, arb: ArbitrageResult, bot_username: str, start_time_utc: str = ""
) -> str:
    emoji = GAME_EMOJI.get(game, "🏆")
    lines = [
        f"💰 {emoji} <b>Вилка</b> ({game.upper()})",
        f"⚔️ <b>{html.escape(team_a)}</b> vs <b>{html.escape(team_b)}</b>",
    ]
    match_time = format_match_start(start_time_utc)
    if match_time:
        lines.append(f"🕒 {match_time}")
    lines.append(f"🚀 Прибыль: <b>{arb.profit_pct:.2f}%</b>")
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
        if not within_time_horizon(start_time_utc, user.time_horizons, now):
            continue

        stakes = calc_stakes(user.bankroll, arb.best_odds)
        message = _format_message(game, team_a, team_b, arb, start_time_utc, user.bankroll)
        message += f"\n\n💵 Ставки при банкролле <b>{user.bankroll:.2f}</b>:\n"
        message += "<blockquote>" + "\n".join(format_stakes_lines(stakes)) + "</blockquote>"

        try:
            await _send_message_with_retries(bot, user.chat_id, message, parse_mode="HTML", protect_content=True)
        except Exception:
            logger.exception("Failed to notify chat_id=%s", user.chat_id)

    repo.mark_opportunity_seen(match_id, bh)


def _showcase_key(m: MatchSnapshot) -> str:
    return f"{m.game}:{m.team_a}:{m.team_b}:{m.start_time_utc}:{_bookmakers_hash(m.arb.best_odds)}"


async def _notify_showcase(
    best: MatchSnapshot,
    bot: Bot,
    showcase_chat_id: int,
    bot_username: str,
) -> None:
    message = _format_showcase_message(best.game, best.team_a, best.team_b, best.arb, bot_username, best.start_time_utc)
    try:
        await _send_message_with_retries(bot, showcase_chat_id, message, parse_mode="HTML")
    except Exception:
        logger.exception("Failed to post showcase message to chat_id=%s", showcase_chat_id)


async def run_monitor_loop(
    sources: list[OddsProvider],
    repo: Repository,
    bot: Bot,
    games: list[str],
    poll_interval_seconds: int,
    state: LatestState,
    surebet_finder: SurebetFinder | None = None,
    admin_chat_ids: frozenset[int] = frozenset(),
    showcase_chat_id: int | None = None,
    showcase_interval_seconds: int = 600,
    bot_username: str = "",
) -> None:
    empty_streaks: dict[str, int] = {}
    last_showcase_post = 0.0
    last_showcase_key: str | None = None
    daily_best: MatchSnapshot | None = None
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

        if showcase_chat_id is not None and found:
            best_now = max(found, key=lambda m: m.arb.profit_pct)
            if daily_best is None or best_now.arb.profit_pct > daily_best.arb.profit_pct:
                daily_best = best_now

        if (
            showcase_chat_id is not None
            and daily_best is not None
            and time.time() - last_showcase_post >= showcase_interval_seconds
        ):
            key = _showcase_key(daily_best)
            if key != last_showcase_key:
                await _notify_showcase(daily_best, bot, showcase_chat_id, bot_username)
                last_showcase_key = key
            last_showcase_post = time.time()
            daily_best = None

        state.matches = found
        state.updated_at = time.time()

        await asyncio.sleep(poll_interval_seconds)
