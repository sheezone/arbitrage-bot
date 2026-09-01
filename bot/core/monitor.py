from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import time
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import LinkPreviewOptions

from bot.core import billing
from bot.core.arbitrage import ArbitrageResult, OutcomeOdds, calc_arbitrage, calc_stakes
from bot.core.reconcile import group_quotes, split_by_market, to_arbitrage_input
from bot.core.state import LatestState, MatchSnapshot
from bot.db.repository import Repository
from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote
from bot.providers.surebet import SurebetFinder

logger = logging.getLogger(__name__)


# Telegram Premium animated emoji -- IDs captured live (2026-08-28) from an actual
# Premium account via a one-off admin debug probe (since removed from commands.py). Each
# is (custom_emoji_id, fallback_text); the fallback is exactly what Telegram itself sent
# back as that emoji's non-animated representation, not guessed, so it's safe to trust
# verbatim. Rendering via <tg-emoji emoji-id="..."> needs no Premium on the bot's side --
# any bot can send these since Bot API 6.5 -- but ONLY works in message text/captions,
# not inline keyboard button labels (Telegram doesn't support custom emoji there).
EMOJI_ALERT = ("5440660757194744323", "🚨")
EMOJI_HIGH_PROFIT = ("5206607081334906820", "‼️")
EMOJI_WARNING = ("5210952531676504517", "❗️")
EMOJI_MONEY = ("5456140674028019486", "💵💸")
EMOJI_NEW = ("5395695537687123235", "🆕")
EMOJI_CANCEL = ("5244837092042750681", "❌")
EMOJI_LIGHTNING = ("5231200819986047254", "⚡️")
EMOJI_CHART = ("5447183459602669338", "🕯📈")
EMOJI_BELL = ("5246762912428603768", "🔔📊")
EMOJI_COMET = ("5458603043203327669", "☄️")


def tg_emoji(pair: tuple[str, str]) -> str:
    emoji_id, fallback = pair
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


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


async def _send_message_with_retries(bot: Bot, chat_id: int, text: str, **kwargs):
    """Confirmed live: this VPS's network path to api.telegram.org intermittently stalls
    for 100+ seconds (not a code bug -- a raw `curl` to api.telegram.org reproduced the
    same multi-minute hang). A single failed send used to just drop that notification
    silently; retrying a few times gives a transient stall a chance to clear before we
    give up on it. Returns the sent Message (some callers need its message_id)."""
    last_error: Exception | None = None
    for attempt in range(1, SEND_RETRY_ATTEMPTS + 1):
        try:
            return await bot.send_message(chat_id, text, **kwargs)
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

# Homepage for every bookmaker key this codebase's providers ever set on a quote (see each
# provider module's own SourceQuote(...) calls, plus surebet.py's BOOKMAKERS/display-name
# map) -- lets the bookmaker name in a result link straight to where to place the bet,
# instead of just naming it. A key with no entry here still renders, just not as a link.
BOOKMAKER_URLS = {
    "fonbet": "https://www.fonbet.ru",
    "pari": "https://pari.ru",
    "marathon": "https://www.marathonbet.ru",
    "baltbet": "https://baltbet.ru",
    "zenit": "https://zenit.win",
    "melbet": "https://melbet.ru",
    "leon": "https://leon.ru",
    "olimpbet": "https://www.olimp.bet",
    "winline": "https://winline.ru",
    "betcity": "https://betcity.ru",
    "betboom": "https://betboom.ru",
    "ligastavok": "https://www.ligastavok.ru",
    "bet365": "https://www.bet365.com",
    "1xbet": "https://1xbet.com",
    "pinnacle": "https://www.pinnacle.com",
}


def user_allows_arb(allowed_bookmakers: list[str], best_odds: list[OutcomeOdds]) -> bool:
    """Empty allowed_bookmakers means no restriction (the default -- a fresh/upgraded user
    sees everything). Otherwise every leg of the arb must be at an allowed bookmaker: a
    vilka the user can't actually place half of isn't useful to them at all."""
    if not allowed_bookmakers:
        return True
    allowed = {b.lower() for b in allowed_bookmakers}
    return all(o.bookmaker.lower() in allowed for o in best_odds)


def format_odds_lines(best_odds: list[OutcomeOdds]) -> list[str]:
    lines = []
    for i, outcome in enumerate(best_odds):
        marker = _OUTCOME_MARKERS[i % len(_OUTCOME_MARKERS)]
        name = html.escape(outcome.outcome_name)
        bookmaker_name = html.escape(outcome.bookmaker.upper())
        url = BOOKMAKER_URLS.get(outcome.bookmaker.lower())
        bookmaker = f'<a href="{url}">{bookmaker_name}</a>' if url else bookmaker_name
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
    lines.append("")
    lines.append("⚠️ Коэффициенты и % прибыли могут измениться у букмекера — проверяйте перед ставкой.")
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


async def _fetch_one_source(source: OddsProvider, games: list[str]) -> tuple[str, list[SourceQuote]]:
    name = type(source).__name__
    try:
        return name, await source.fetch_quotes(games)
    except Exception:
        logger.exception("Source %s failed to fetch quotes", name)
        return name, []


async def _fetch_all_quotes(
    sources: list[OddsProvider], games: list[str], empty_streaks: dict[str, int]
) -> list[SourceQuote]:
    # Fetched concurrently, not one after another: OlimpBet's response alone is ~10MB, and
    # sequentially awaiting every source in turn used to add each one's latency on top of
    # the last -- confirmed live this pushed a full cycle well past a minute, meaning the
    # odds shown could already be 1-2 poll intervals stale by the time someone checks them
    # against the bookmaker's own site. Concurrent fetching caps a cycle at roughly the
    # slowest single source instead of the sum of all of them.
    results = await asyncio.gather(*(_fetch_one_source(source, games) for source in sources))

    all_quotes: list[SourceQuote] = []
    for name, quotes in results:
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
        if not arb.is_arbitrage or arb.profit_pct < MIN_DISPLAYABLE_PROFIT_PCT:
            continue
        if arb.profit_pct > MAX_DISPLAYABLE_PROFIT_PCT:
            continue

        # The len(...)>=2 check above only guarantees the raw cluster mentions 2+
        # bookmakers somewhere -- find_best_odds still independently picks the single
        # best price per outcome, which CAN land on the same bookmaker for every leg.
        # Confirmed live (2026-08-31): a Greece-vs-Spain basketball "78% profit" vilka
        # with both legs tagged the same bookmaker (Marathon), one leg's price standing
        # far outside anything that bookmaker (or any other source) was actually quoting
        # moments later on repeated direct re-checks -- a data/parsing artifact, not a
        # real opportunity. A single real bookmaker pricing itself into an internal
        # arbitrage against its own book essentially never happens, so treat an all-
        # same-bookmaker result as a red flag and drop it rather than surface it.
        if len({o.bookmaker for o in arb.best_odds}) < 2:
            logger.warning(
                "Dropping %s %s vs %s (%s): every leg priced by the same bookmaker (%s) -- "
                "likely a data/matching artifact, not a real arb",
                subgroup[0].game, team_a, team_b, market, arb.best_odds[0].bookmaker,
            )
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
        if not within_time_horizon(start_time_utc, user.time_horizons, now):
            continue
        if not user_allows_arb(user.allowed_bookmakers, arb.best_odds):
            continue

        stakes = calc_stakes(user.bankroll, arb.best_odds)
        message = _format_message(game, team_a, team_b, arb, start_time_utc, user.bankroll)
        message += f"\n\n💵 Ставки при банкролле <b>{user.bankroll:.2f}</b>:\n"
        message += "<blockquote>" + "\n".join(format_stakes_lines(stakes)) + "</blockquote>"

        try:
            await _send_message_with_retries(
                bot, user.chat_id, message, parse_mode="HTML", protect_content=True,
                link_preview_options=LinkPreviewOptions(is_disabled=True), disable_notification=user.muted,
            )
        except Exception:
            logger.exception("Failed to notify chat_id=%s", user.chat_id)

    repo.mark_opportunity_seen(match_id, bh)
    repo.log_opportunity(arb.profit_pct)


# A profit this high is more often a stale/mispriced quote from a scraped source than a
# genuine opportunity that big -- confirmed live seeing implausible one-off percentages.
# Rather than trust it immediately, wait RECHECK_DELAY_SECONDS and re-fetch, then notify
# with whatever the fresh data says now -- even if the profit dropped some (that's the
# point of rechecking). Only when the match can't be found on the fresh data at all (fetch
# failure, or it just vanished) does it fall back to the original numbers rather than skip
# -- always notify, never silently drop one. Note this only ever fires for the (10%, 15%]
# band now -- anything above MAX_DISPLAYABLE_PROFIT_PCT (15%) is dropped outright before
# it ever reaches this, see that constant below.
HIGH_PROFIT_RECHECK_THRESHOLD = 10.0
RECHECK_DELAY_SECONDS = 30

# A global floor, independent of each user's own (possibly lower) min_profit_pct setting --
# anything below this is judged too small to be worth surfacing at all (rounding/spread
# noise more than a real edge), so it never becomes an "opportunity" in the first place:
# not notified, not shown in search, not logged to stats, not shown to the showcase channel.
MIN_DISPLAYABLE_PROFIT_PCT = 0.60

# A ceiling on the other end: real cross-bookmaker arbitrage margins this big essentially
# never happen (see the same-bookmaker guard in _evaluate_group for one confirmed-live
# example of exactly this -- a "78% profit" vilka that turned out to be a data artifact,
# not real). Rather than trust the recheck/same-bookmaker guard alone to catch every such
# case, anything above this is dropped outright, no exceptions -- at the user's explicit
# request after that incident.
MAX_DISPLAYABLE_PROFIT_PCT = 15.0


async def _recheck_and_notify_high_profit(
    suspicious: list[tuple[str, str, str, str, ArbitrageResult, str]],
    surebet_suspicious: list[tuple[str, str, str, str, ArbitrageResult]],
    sources: list[OddsProvider],
    games: list[str],
    empty_streaks: dict[str, int],
    surebet_finder: SurebetFinder | None,
    repo: Repository,
    bot: Bot,
    admin_chat_ids: frozenset[int],
) -> list[MatchSnapshot]:
    logger.info(
        "%d suspiciously high-profit match(es) found (>%.0f%%) -- rechecking in %ds before notifying",
        len(suspicious) + len(surebet_suspicious), HIGH_PROFIT_RECHECK_THRESHOLD, RECHECK_DELAY_SECONDS,
    )
    await asyncio.sleep(RECHECK_DELAY_SECONDS)

    found: list[MatchSnapshot] = []

    if suspicious:
        fresh_by_key: dict[tuple[str, str, str, str], ArbitrageResult] = {}
        try:
            fresh_quotes = await _fetch_all_quotes(sources, games, empty_streaks)
            for group in group_quotes(fresh_quotes):
                try:
                    for game, team_a, team_b, market, arb in _evaluate_group(group):
                        fresh_by_key[(game, team_a, team_b, market)] = arb
                except Exception:
                    logger.exception("Failed to evaluate match group during high-profit recheck")
        except Exception:
            logger.exception("Failed to re-fetch quotes for high-profit recheck")

        for game, team_a, team_b, market, stale_arb, start_time_utc in suspicious:
            fresh_arb = fresh_by_key.get((game, team_a, team_b, market))
            if fresh_arb is None:
                logger.warning(
                    "High-profit match %s vs %s (%s) not found on recheck -- notifying with the original odds anyway",
                    team_a, team_b, market,
                )
                fresh_arb = stale_arb
            found.append(MatchSnapshot(game, team_a, team_b, fresh_arb, start_time_utc))
            try:
                await _notify_group(game, team_a, team_b, fresh_arb, start_time_utc, repo, bot, admin_chat_ids, market)
            except Exception:
                logger.exception("Failed to notify rechecked match group")

    if surebet_suspicious and surebet_finder is not None:
        # SureBet's own MIN_INTERVAL_SECONDS (~65s) cache means a call this soon after the
        # first one often just returns the same cached response -- this still catches the
        # case where the opportunity has since dropped out of its own matching, just isn't
        # always a fully independent second look at the underlying odds.
        fresh_by_names: dict[tuple[str, str, str], ArbitrageResult] = {}
        try:
            fresh_surebet = await surebet_finder.find(games)
            fresh_by_names = {(g, ta, tb): arb for g, ta, tb, _, arb in fresh_surebet}
        except Exception:
            logger.exception("Failed to re-fetch SureBet matches for high-profit recheck")

        for game, team_a, team_b, start_time_utc, stale_arb in surebet_suspicious:
            fresh_arb = fresh_by_names.get((game, team_a, team_b))
            if fresh_arb is None:
                logger.warning(
                    "High-profit SureBet match %s vs %s not found on recheck -- notifying with the original odds anyway",
                    team_a, team_b,
                )
                fresh_arb = stale_arb
            found.append(MatchSnapshot(game, team_a, team_b, fresh_arb, start_time_utc))
            try:
                await _notify_group(game, team_a, team_b, fresh_arb, start_time_utc, repo, bot, admin_chat_ids)
            except Exception:
                logger.exception("Failed to notify rechecked SureBet match")

    return found


# How long before access actually runs out to warn a user, and how often to even bother
# checking -- checking every poll cycle (which can be as low as 15s) would just re-query
# every user for nothing since expiry_reminder_sent_for already dedups the actual sends;
# an hourly check is more than tight enough for a 24h warning window.
EXPIRY_REMINDER_WINDOW_HOURS = 24
EXPIRY_CHECK_INTERVAL_SECONDS = 3600


async def _send_expiry_reminders(repo: Repository, bot: Bot, admin_chat_ids: frozenset[int]) -> None:
    now = datetime.now(timezone.utc)
    for user in repo.get_all_users():
        if billing.is_admin(user, admin_chat_ids):
            continue  # unlimited access, nothing to warn about

        end = billing.access_end(user, now)
        if end <= now:
            continue  # already expired -- that's the dashboard's locked-screen job, not this
        hours_left = (end - now).total_seconds() / 3600
        if hours_left > EXPIRY_REMINDER_WINDOW_HOURS:
            continue

        end_key = end.isoformat()
        if user.expiry_reminder_sent_for == end_key:
            continue  # already reminded for this exact expiry point (a renewal changes
            # end_key, which naturally re-arms this for the new deadline)

        kind = "пробный период" if billing.on_trial(user, now) else "подписка"
        message = (
            f"⏳ Ваш {kind} заканчивается менее чем через 24 часа.\n\n"
            "Оформите подписку в разделе «👤 Мой профиль» → «💳 Подписка», чтобы не "
            "пропустить уведомления о новых вилках."
        )
        try:
            await _send_message_with_retries(bot, user.chat_id, message, parse_mode="HTML")
            repo.set_expiry_reminder_sent(user.chat_id, end_key)
        except Exception:
            logger.exception("Failed to send expiry reminder to chat_id=%s", user.chat_id)


def _showcase_key(m: MatchSnapshot) -> str:
    """Deliberately does NOT include the bookmakers/odds hash (unlike _bookmakers_hash-
    based dedup elsewhere in this file) -- the odds on the same match shift by a cent or
    two between poll cycles all the time, which used to make this look like a "new" find
    every time and repost the same match over and over. Identity here is just the match +
    market itself."""
    return f"{m.game}:{m.team_a}:{m.team_b}:{m.start_time_utc}"


async def _notify_showcase(
    best: MatchSnapshot,
    bot: Bot,
    showcase_chat_id: int,
    bot_username: str,
) -> int | None:
    """Returns the sent message_id (for repo.record_showcase_post), or None if it failed
    to send at all."""
    message = _format_showcase_message(best.game, best.team_a, best.team_b, best.arb, bot_username, best.start_time_utc)
    try:
        sent = await _send_message_with_retries(
            bot, showcase_chat_id, message, parse_mode="HTML", link_preview_options=LinkPreviewOptions(is_disabled=True)
        )
        return sent.message_id
    except Exception:
        logger.exception("Failed to post showcase message to chat_id=%s", showcase_chat_id)
        return None


# How often to run the duplicate-showcase-post cleanup sweep -- a safety net on top of
# set_showcase_state's in-the-loop dedup, in case something still slips through (e.g. two
# process instances briefly overlapping during a deploy).
SHOWCASE_CLEANUP_INTERVAL_SECONDS = 3 * 3600


async def _cleanup_duplicate_showcase_posts(repo: Repository, bot: Bot, showcase_chat_id: int) -> None:
    duplicates = repo.find_duplicate_showcase_posts()
    if not duplicates:
        return
    logger.info("Found %d duplicate showcase post(s) to delete", len(duplicates))
    deleted: list[int] = []
    for chat_id, message_id in duplicates:
        try:
            await bot.delete_message(chat_id, message_id)
            deleted.append(message_id)
        except Exception:
            logger.exception("Failed to delete duplicate showcase message_id=%s", message_id)
    if deleted:
        repo.delete_showcase_posts(showcase_chat_id, deleted)


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
    # Persisted (not just in-memory) so a process restart -- a deploy, which happens
    # often -- doesn't reset this and immediately repost whatever's currently the best
    # find. See Repository.get_showcase_state's docstring for what this fixed live.
    last_showcase_key, last_showcase_post_iso = repo.get_showcase_state()
    last_showcase_post = datetime.fromisoformat(last_showcase_post_iso).timestamp() if last_showcase_post_iso else 0.0
    daily_best: MatchSnapshot | None = None
    last_expiry_check = 0.0
    last_showcase_cleanup = 0.0
    while True:
        if (
            showcase_chat_id is not None
            and time.time() - last_showcase_cleanup >= SHOWCASE_CLEANUP_INTERVAL_SECONDS
        ):
            try:
                await _cleanup_duplicate_showcase_posts(repo, bot, showcase_chat_id)
            except Exception:
                logger.exception("Failed to clean up duplicate showcase posts")
            last_showcase_cleanup = time.time()

        if time.time() - last_expiry_check >= EXPIRY_CHECK_INTERVAL_SECONDS:
            try:
                await _send_expiry_reminders(repo, bot, admin_chat_ids)
            except Exception:
                logger.exception("Failed to send expiry reminders")
            last_expiry_check = time.time()

        all_quotes = await _fetch_all_quotes(sources, games, empty_streaks)
        groups = group_quotes(all_quotes)

        found: list[MatchSnapshot] = []
        suspicious: list[tuple[str, str, str, str, ArbitrageResult, str]] = []
        for group in groups:
            try:
                results = _evaluate_group(group)
            except Exception:
                logger.exception("Failed to evaluate match group")
                continue

            for game, team_a, team_b, market, arb in results:
                start_time_utc = group[0].start_time_utc
                if arb.profit_pct > HIGH_PROFIT_RECHECK_THRESHOLD:
                    suspicious.append((game, team_a, team_b, market, arb, start_time_utc))
                    continue
                found.append(MatchSnapshot(game, team_a, team_b, arb, start_time_utc))
                try:
                    await _notify_group(game, team_a, team_b, arb, start_time_utc, repo, bot, admin_chat_ids, market)
                except Exception:
                    logger.exception("Failed to notify match group")

        surebet_suspicious: list[tuple[str, str, str, str, ArbitrageResult]] = []
        if surebet_finder is not None:
            try:
                surebet_matches = await surebet_finder.find(games)
            except Exception:
                logger.exception("SureBet finder failed")
                surebet_matches = []

            for game, team_a, team_b, start_time_utc, arb in surebet_matches:
                if arb.profit_pct < MIN_DISPLAYABLE_PROFIT_PCT or arb.profit_pct > MAX_DISPLAYABLE_PROFIT_PCT:
                    continue
                if arb.profit_pct > HIGH_PROFIT_RECHECK_THRESHOLD:
                    surebet_suspicious.append((game, team_a, team_b, start_time_utc, arb))
                    continue
                found.append(MatchSnapshot(game, team_a, team_b, arb, start_time_utc))
                try:
                    await _notify_group(game, team_a, team_b, arb, start_time_utc, repo, bot, admin_chat_ids)
                except Exception:
                    logger.exception("Failed to notify SureBet match")

        if suspicious or surebet_suspicious:
            try:
                found.extend(
                    await _recheck_and_notify_high_profit(
                        suspicious, surebet_suspicious, sources, games, empty_streaks, surebet_finder,
                        repo, bot, admin_chat_ids,
                    )
                )
            except Exception:
                logger.exception("Failed to recheck high-profit matches")

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
                message_id = await _notify_showcase(daily_best, bot, showcase_chat_id, bot_username)
                if message_id is not None:
                    repo.record_showcase_post(showcase_chat_id, message_id, key)
                last_showcase_key = key
            last_showcase_post = time.time()
            repo.set_showcase_state(last_showcase_key, datetime.fromtimestamp(last_showcase_post, tz=timezone.utc).isoformat())
            daily_best = None

        state.matches = found
        state.updated_at = time.time()

        await asyncio.sleep(poll_interval_seconds)
