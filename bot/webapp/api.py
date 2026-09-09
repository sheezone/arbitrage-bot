"""JSON API backing the Telegram Mini App (bot/webapp/static/*) -- the same data/actions
as the button-based bot UI in bot/handlers/commands.py, reusing its business logic
(billing, monitor helpers, repository) rather than reimplementing any of it. Every
endpoint requires a valid Telegram `initData` (see auth.py) in the `Authorization: tma
<initData>` header -- there is no other auth, so a request with a missing/invalid/expired
one is rejected outright rather than falling back to some anonymous/demo mode."""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

import httpx
from aiogram import Bot
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.core import billing
from bot.core.arbitrage import calc_stakes
from bot.core.monitor import BOOKMAKER_URLS, GAME_EMOJI, format_match_start, user_allows_arb, within_time_horizon
from bot.core.state import LatestState
from bot.core.subscription import is_subscribed
from bot.db.repository import Repository, UserSettings
from bot.handlers.commands import (
    BANKROLL_PRESETS,
    GAME_LABELS,
    TIME_HORIZONS,
    _ALL_BOOKMAKER_KEYS,
    _AGGREGATOR_BOOKMAKERS,
    _DIRECT_BOOKMAKERS,
)
from bot.providers import prodamus
from bot.webapp.auth import validate_init_data
from bot.webapp.football_stats import get_match_h2h, get_popular_upcoming_fixtures, search_team_logo
from bot.webapp.news import fetch_team_news, pick_popular_matches
from bot.webapp.football_data import get_team_form as fd_team_form
from bot.webapp.team_form import get_team_form, implied_probabilities
from bot.webapp.team_flags import get_team_flag
from bot.webapp.team_logos import get_nba_logo_url

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Google News RSS is a live web search, not a fast local lookup -- 3 matches x 1 request
# each on every /api/news call would be slow and needlessly hammer it. News doesn't
# change second-to-second, so a short server-side cache is the right trade here. Lives as
# a local inside register_api (see below), NOT a module-level global -- a global would be
# shared across every register_api() call (e.g. each test's own app instance), leaking
# cached state between them instead of each app owning its own.
NEWS_CACHE_TTL_SECONDS = 600

# Separate, much longer cache for the real-fixtures lookup (get_popular_upcoming_fixtures)
# -- it costs 1-2 API-Football requests against the free tier's 100/day cap, so refreshing
# it on the same 10-minute cadence as headlines (up to 144x/day) would blow through the
# quota fast. An hour is plenty fresh for "what's kicking off in the next 24h".
FIXTURES_CACHE_TTL_SECONDS = 3600


def _bot_token() -> str:
    return os.environ.get("BOT_TOKEN", "")


def _auth(authorization: str | None) -> int:
    """Returns the caller's chat_id, or raises 401. `Authorization: tma <initData>` is
    Telegram's own recommended header scheme for Mini App backend calls."""
    if not authorization or not authorization.startswith("tma "):
        raise HTTPException(status_code=401, detail="Missing Telegram init data")
    user = validate_init_data(authorization[4:], _bot_token())
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid or expired init data")
    return int(user["id"])


def _get_user(repo: Repository, chat_id: int) -> UserSettings:
    user = repo.get_user(chat_id)
    if user is None:
        # A real Telegram user who has never pressed /start in the bot itself -- the
        # Mini App is reached through the bot, so this shouldn't normally happen, but
        # upsert rather than 404 so it recovers gracefully if it ever does.
        repo.upsert_user(chat_id)
        user = repo.get_user(chat_id)
    return user


async def _user_out(
    user: UserSettings,
    admin_chat_ids: frozenset[int],
    bot: Bot | None,
    required_channel_id: int | None,
    required_channel_username: str,
) -> dict:
    now = datetime.now(timezone.utc)
    is_admin = billing.is_admin(user, admin_chat_ids)
    subscribed = (
        True
        if required_channel_id is None or is_admin or bot is None
        else await is_subscribed(bot, required_channel_id, user.chat_id)
    )
    return {
        "chat_id": user.chat_id,
        "bankroll": user.bankroll,
        "min_profit_pct": user.min_profit_pct,
        "time_horizons": user.time_horizons,
        "allowed_bookmakers": user.allowed_bookmakers,
        "is_active": user.is_active,
        "muted": user.muted,
        "is_admin": is_admin,
        "has_access": billing.has_access(user, now, admin_chat_ids),
        "on_trial": billing.on_trial(user, now),
        "days_left": billing.days_left(user, now),
        # Admins bypass the 1/day analysis quota entirely -- always "available".
        "analysis_available": is_admin or user.last_analysis_date != now.date().isoformat(),
        # Mandatory-subscription gate (bot/core/subscription.py) -- mirrors the button
        # bot UI's gate. channel_required tells the frontend whether to even show a gate
        # screen at all; channel_username is what it links "📢 Подписаться" to.
        "channel_required": required_channel_id is not None,
        "is_subscribed": subscribed,
        "channel_username": required_channel_username,
    }


class SettingsIn(BaseModel):
    bankroll: float | None = None
    min_profit_pct: float | None = None
    time_horizons: list[int] | None = None
    allowed_bookmakers: list[str] | None = None
    muted: bool | None = None


def register_api(
    repo: Repository,
    state: LatestState,
    admin_chat_ids: frozenset[int],
    api_football_key: str = "",
    bot: Bot | None = None,
    required_channel_id: int | None = None,
    required_channel_username: str = "",
    prodamus_secret_key: str = "",
    football_data_key: str = "",
) -> FastAPI:
    """Builds and returns a fresh FastAPI app wired to the given Repository/LatestState --
    NOT a module-level singleton mutated in place. Call this once from bot/main.py with
    the same instances the bot's aiogram handlers and monitor loop use (one shared source
    of truth, not a second copy of the data); tests call it once per Repository too, and a
    shared mutable app would leak routes closed over a previous test's Repository across
    tests (confirmed live -- that's exactly what happened before this was a factory).

    Every route below MUST stay `async def`, never plain `def` -- confirmed by a test
    failure: FastAPI runs sync route functions in a worker-thread pool, but Repository's
    sqlite3 connection was opened on the main thread and sqlite3 objects can only be used
    from the thread that created them (raises ProgrammingError otherwise). `async def`
    routes run directly on the event loop's own thread instead, where Repository already
    lives."""
    app = FastAPI(title="Arbitrage Bot Mini App API")
    # Telegram loads the Mini App inside a webview whose effective origin isn't something
    # to rely on for CORS -- the real access control here is the initData check on every
    # route, not Origin, so this stays permissive rather than fighting webview quirks for
    # no security benefit.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    news_cache: dict = {"at": 0.0, "payload": None}
    fixtures_cache: dict = {"at": 0.0, "payload": None}
    football_logo_cache: dict[str, str | None] = {}

    async def _require_subscribed(chat_id: int) -> None:
        """Mirrors the button bot UI's SubscriptionGateMiddleware (see
        handlers/commands.py) -- called right after _auth() on every endpoint except
        /api/me (which must always succeed so the frontend can read channel_required/
        is_subscribed and show its own gate screen instead of erroring out). A no-op
        when the gate isn't configured, bot wasn't passed in (e.g. most tests), or the
        caller is an admin."""
        if required_channel_id is None or bot is None:
            return
        # chat_id in admin_chat_ids is billing.is_admin's entire check -- doesn't need a
        # real UserSettings row, so this must NOT go through repo.get_user first (an
        # admin's very first request, before any row exists yet, would otherwise fall
        # through to the real subscription check and get wrongly 403'd).
        if chat_id in admin_chat_ids:
            return
        if not await is_subscribed(bot, required_channel_id, chat_id):
            raise HTTPException(status_code=403, detail="Требуется подписка на канал")

    @app.middleware("http")
    async def _no_cache(request, call_next):
        # Telegram's in-app WebView is known to cache static assets aggressively by URL
        # (confirmed live: a JS fix didn't take effect on a reopen of the same Mini App
        # until this was added) -- there's no build step/hashed filenames here to bust
        # that cache otherwise, so just refuse to let anything be cached at all.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return response

    @app.get("/api/me")
    async def get_me(authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
        user = _get_user(repo, chat_id)
        return await _user_out(user, admin_chat_ids, bot, required_channel_id, required_channel_username)

    @app.post("/api/settings")
    async def post_settings(body: SettingsIn, authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
        await _require_subscribed(chat_id)
        _get_user(repo, chat_id)  # ensures the row exists

        if body.bankroll is not None:
            if body.bankroll <= 0:
                raise HTTPException(status_code=400, detail="bankroll must be positive")
            repo.set_bankroll(chat_id, body.bankroll)
        if body.min_profit_pct is not None:
            if body.min_profit_pct < 0:
                raise HTTPException(status_code=400, detail="min_profit_pct must be >= 0")
            repo.set_min_profit_pct(chat_id, body.min_profit_pct)
        if body.time_horizons is not None:
            valid = [d for d in body.time_horizons if d in TIME_HORIZONS]
            if not valid:
                raise HTTPException(status_code=400, detail="need at least one valid time horizon")
            repo.set_time_horizons(chat_id, sorted(set(valid)))
        if body.allowed_bookmakers is not None:
            valid_bk = [b for b in body.allowed_bookmakers if b in _ALL_BOOKMAKER_KEYS]
            # Selecting everything is stored as empty (== "no restriction"), same
            # convention as the button UI -- see commands.py's on_bookmaker_toggle.
            to_store = [] if set(valid_bk) == set(_ALL_BOOKMAKER_KEYS) else sorted(set(valid_bk))
            repo.set_allowed_bookmakers(chat_id, to_store)
        if body.muted is not None:
            repo.set_muted(chat_id, body.muted)

        return await _user_out(_get_user(repo, chat_id), admin_chat_ids, bot, required_channel_id, required_channel_username)

    @app.get("/api/bookmakers")
    async def get_bookmakers(authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)  # any authenticated + subscribed user may read the static list
        await _require_subscribed(chat_id)
        def _row(key: str) -> dict:
            category = "direct" if key in _DIRECT_BOOKMAKERS else "aggregator" if key in _AGGREGATOR_BOOKMAKERS else "other"
            return {"key": key, "label": key.upper(), "url": BOOKMAKER_URLS.get(key), "category": category}
        return {"bookmakers": [_row(k) for k in _ALL_BOOKMAKER_KEYS], "presets": {"bankroll": BANKROLL_PRESETS}}

    @app.get("/api/stats")
    async def get_stats(authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
        await _require_subscribed(chat_id)
        return repo.get_opportunity_stats()

    @app.get("/api/news")
    async def get_news(authorization: str | None = Header(default=None)):
        """Real headlines for up to 3 "popular" matches -- deliberately NOT win-probability
        predictions/percentages. See bot/webapp/news.py's module docstring for why.

        Football matches come from real upcoming fixtures (next 24h, well-known leagues),
        NOT from bot/core/state.LatestState (which only ever holds matches an arb was
        actually found for) -- see get_popular_upcoming_fixtures's docstring. Other sports
        still fall back to the arb-derived pool, since there's no equivalent free
        fixture-calendar source for them."""
        chat_id = _auth(authorization)
        await _require_subscribed(chat_id)

        if news_cache["payload"] is not None and time.time() - news_cache["at"] < NEWS_CACHE_TTL_SECONDS:
            return news_cache["payload"]

        if fixtures_cache["payload"] is None or time.time() - fixtures_cache["at"] >= FIXTURES_CACHE_TTL_SECONDS:
            async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
                fixtures_cache["payload"] = await get_popular_upcoming_fixtures(client, api_football_key, limit=3)
            fixtures_cache["at"] = time.time()
        football_fixtures = fixtures_cache["payload"] or []

        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            entries = []
            for fx in football_fixtures:
                headlines = await fetch_team_news(client, fx["team_a"], fx["team_b"])
                entries.append({
                    "game": "football",
                    "game_label": "Футбол",
                    "game_emoji": GAME_EMOJI.get("football", "⚽"),
                    "team_a": fx["team_a"],
                    "team_b": fx["team_b"],
                    "start_time_label": format_match_start(fx["start_time_utc"]),
                    "headlines": headlines,
                    # H2H (the heavier part -- extra API-Football requests, rate-limited
                    # to 1/day/user) is deliberately NOT fetched here for all 3 matches on
                    # every page load -- see /api/analysis, fetched only on click.
                    "can_analyze": True,
                })

            existing_pairs = {(e["team_a"], e["team_b"]) for e in entries}
            remaining = 3 - len(entries)
            if remaining > 0:
                for m in pick_popular_matches(state.matches, limit=remaining + len(entries)):
                    if len(entries) >= 3:
                        break
                    if (m.team_a, m.team_b) in existing_pairs:
                        continue
                    headlines = await fetch_team_news(client, m.team_a, m.team_b)
                    entries.append({
                        "game": m.game,
                        "game_label": GAME_LABELS.get(m.game, m.game.upper()),
                        "game_emoji": GAME_EMOJI.get(m.game, "🏆"),
                        "team_a": m.team_a,
                        "team_b": m.team_b,
                        "start_time_label": format_match_start(m.start_time_utc),
                        "headlines": headlines,
                        "can_analyze": m.game == "football",
                    })

        payload = {"matches": entries}
        news_cache["payload"] = payload
        news_cache["at"] = time.time()
        return payload

    @app.get("/api/analysis")
    async def get_analysis(
        team_a: str, team_b: str, authorization: str | None = Header(default=None)
    ):
        """On-demand H2H analysis for one of the 3 currently-popular matches -- gated to
        once per user per UTC day (see last_analysis_date) so a click doesn't become an
        unlimited way to burn API-Football's 100-req/day free quota. Deliberately not part
        of /api/news's payload for that reason -- see the docstring there. Admins bypass
        the quota entirely (unlimited analyses), at the user's request."""
        chat_id = _auth(authorization)
        await _require_subscribed(chat_id)
        user = _get_user(repo, chat_id)
        is_admin = billing.is_admin(user, admin_chat_ids)

        today = datetime.now(timezone.utc).date().isoformat()
        if not is_admin and user.last_analysis_date == today:
            raise HTTPException(status_code=429, detail="Дневной лимит анализа исчерпан (1 в день)")

        # A valid pair is either one of the real upcoming fixtures currently cached (see
        # /api/news) or, for non-football, one from the arb-derived pool -- mirrors
        # exactly what /api/news is currently showing as analyzable.
        in_fixtures = any(
            fx["team_a"] == team_a and fx["team_b"] == team_b for fx in (fixtures_cache["payload"] or [])
        )
        match = None
        if not in_fixtures:
            picked = pick_popular_matches(state.matches, limit=3)
            match = next((m for m in picked if m.team_a == team_a and m.team_b == team_b), None)
            if match is None:
                raise HTTPException(status_code=404, detail="Матч больше не в списке популярных")
            if match.game != "football":
                raise HTTPException(status_code=400, detail="Анализ пока доступен только для футбола")

        # Market-implied win probabilities from the arb's own best odds -- only available
        # when this match came from the arb pool (a real upcoming fixture has no odds
        # attached). Not a model: just the bookmakers' prices with the margin removed,
        # the frontend labels it as such.
        implied = None
        if match is not None:
            probs = implied_probabilities(match.arb.best_odds)
            if probs:
                implied = {"source": "bookmaker_odds", "outcomes": probs}

        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            h2h = await get_match_h2h(client, team_a, team_b, api_football_key)
            # Form/standings: football-data.org first (authoritative table + form for
            # ~10 top leagues when a key is set), ESPN's hidden JSON as the fallback for
            # everything it doesn't cover. Neither touches the API-Football day budget.
            form_a = await fd_team_form(client, team_a, football_data_key) or await get_team_form(client, team_a)
            form_b = await fd_team_form(client, team_b, football_data_key) or await get_team_form(client, team_b)

        if not is_admin:
            repo.set_last_analysis_date(chat_id, today)
        return {
            "team_a": team_a,
            "team_b": team_b,
            "h2h": h2h,
            "form_a": form_a,
            "form_b": form_b,
            "implied": implied,
        }

    async def _get_football_logo(client: httpx.AsyncClient, team_name: str) -> str | None:
        # Cached forever (a logo URL doesn't change) in this process's own dict, shared
        # across every user's poll -- /api/vilki is auto-refreshed every 20s client-side,
        # so without this a popular team would re-hit API-Football's 100/day free quota
        # on nearly every request. First lookup ever for a given team name pays the real
        # request; everything after is free.
        if team_name in football_logo_cache:
            return football_logo_cache[team_name]
        logo = await search_team_logo(client, team_name, api_football_key) if api_football_key else None
        football_logo_cache[team_name] = logo
        return logo

    @app.get("/api/vilki")
    async def get_vilki(authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
        await _require_subscribed(chat_id)
        user = _get_user(repo, chat_id)
        now = datetime.now(timezone.utc)

        matches = [
            m
            for m in state.matches
            if m.arb.profit_pct >= user.min_profit_pct
            and within_time_horizon(m.start_time_utc, user.time_horizons, now)
            and user_allows_arb(user.allowed_bookmakers, m.arb.best_odds)
        ]
        matches.sort(key=lambda m: m.arb.profit_pct, reverse=True)

        async def _match_out(client: httpx.AsyncClient, m) -> dict:
            stakes = calc_stakes(user.bankroll, m.arb.best_odds)
            if m.game == "basketball":
                team_a_logo, team_b_logo = get_nba_logo_url(m.team_a), get_nba_logo_url(m.team_b)
            elif m.game == "football":
                team_a_logo = await _get_football_logo(client, m.team_a)
                team_b_logo = await _get_football_logo(client, m.team_b)
            else:
                team_a_logo = team_b_logo = None
            return {
                "game": m.game,
                "game_label": GAME_LABELS.get(m.game, m.game.upper()),
                "game_emoji": GAME_EMOJI.get(m.game, "🏆"),
                "team_a": m.team_a,
                "team_b": m.team_b,
                "team_a_flag": get_team_flag(m.team_a),
                "team_b_flag": get_team_flag(m.team_b),
                "team_a_logo": team_a_logo,
                "team_b_logo": team_b_logo,
                "start_time_label": format_match_start(m.start_time_utc),
                "profit_pct": m.arb.profit_pct,
                "profit_amount": user.bankroll * m.arb.profit_pct / 100,
                "legs": [
                    {
                        "outcome_name": o.outcome_name,
                        "bookmaker": o.bookmaker.upper(),
                        "bookmaker_url": BOOKMAKER_URLS.get(o.bookmaker.lower()),
                        "odds": o.odds,
                        "stake": stakes.get(o.outcome_name, 0.0),
                    }
                    for o in m.arb.best_odds
                ],
            }

        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            match_outs = [await _match_out(client, m) for m in matches]

        return {
            "updated_at": state.updated_at,
            "matches": match_outs,
        }

    @app.get("/api/admin/stats")
    async def get_admin_stats(authorization: str | None = Header(default=None)):
        """Admin-only aggregate view -- 403 for anyone not in admin_chat_ids, same check
        as _user_out's is_admin flag. Reuses existing Repository/billing helpers rather
        than any new aggregation logic, so this stays consistent with what those same
        numbers mean everywhere else in the bot."""
        chat_id = _auth(authorization)
        user = _get_user(repo, chat_id)
        if not billing.is_admin(user, admin_chat_ids):
            raise HTTPException(status_code=403, detail="Только для администраторов")

        now = datetime.now(timezone.utc)
        all_users = repo.get_all_users()
        has_access_count = sum(1 for u in all_users if billing.has_access(u, now, admin_chat_ids))

        return {
            "total_users": len(all_users),
            "on_trial": sum(1 for u in all_users if billing.on_trial(u, now)),
            "has_access": has_access_count,
            "expired": len(all_users) - has_access_count,
            "active_notifications": sum(1 for u in all_users if u.is_active),
            "referred_count": sum(1 for u in all_users if u.referred_by is not None),
            "payments": repo.get_payments_summary(),
            "acquisition_sources": repo.get_acquisition_source_counts(),
            "opportunities": repo.get_opportunity_stats(),
            "recent_users": [
                {
                    "chat_id": u.chat_id,
                    "trial_started_at": u.trial_started_at,
                    "has_access": billing.has_access(u, now, admin_chat_ids),
                    "acquisition_source": u.acquisition_source,
                    # "С какого по какое" -- access_start is when the trial (their only
                    # possible starting point) began; access_end is the later of trial
                    # end and any subscription_expires_at, same value the dashboard's
                    # own lock screen is driven by (billing.has_access).
                    "access_start": u.trial_started_at,
                    "access_end": billing.access_end(u, now).isoformat(),
                }
                for u in repo.get_recent_users(limit=10)
            ],
        }

    @app.post("/api/prodamus/webhook")
    async def prodamus_webhook(request: Request):
        """Prodamus payment notification (form-urlencoded + HMAC in the `Sign` header).
        Verifies the signature, then -- only for payment_status=success -- extends the
        buyer's subscription and credits referral bookkeeping. order_num is the order_id
        we set when building the link: "sbp-<chat_id>-<plan_id>-<ts>"."""
        if not prodamus_secret_key:
            raise HTTPException(status_code=404, detail="Not configured")

        raw = await request.body()
        # Prodamus posts application/x-www-form-urlencoded -- parsed directly (no
        # python-multipart dependency), then PHP-nested keys are reassembled.
        pairs = parse_qsl(raw.decode("utf-8", "replace"), keep_blank_values=True)
        data = prodamus.parse_php_form(pairs)
        sign = request.headers.get("Sign") or request.headers.get("sign") or data.get("signature", "")

        if not prodamus.verify_signature(data, prodamus_secret_key, sign):
            logger.warning(
                "Prodamus webhook signature mismatch: received=%r computed=%r body=%r",
                sign, prodamus.compute_signature(
                    {k: v for k, v in data.items() if k.lower() not in ("sign", "signature")},
                    prodamus_secret_key,
                ),
                raw[:2000],
            )
            raise HTTPException(status_code=403, detail="bad signature")

        if not prodamus.is_paid(data):
            return PlainTextResponse("OK")  # pending / test ping / other status -- ack, do nothing

        order_num = str(data.get("order_num") or data.get("order_id") or "")
        parts = order_num.split("-")
        if len(parts) < 4 or parts[0] != "sbp" or not parts[1].isdigit():
            logger.warning("Prodamus webhook: unrecognised order_num %r", order_num)
            return PlainTextResponse("OK")
        chat_id = int(parts[1])
        plan_id = parts[2]
        plan = billing.PLANS_BY_ID.get(plan_id)
        if plan is None:
            logger.warning("Prodamus webhook: unknown plan_id %r (order %r)", plan_id, order_num)
            return PlainTextResponse("OK")

        charge_key = f"prodamus:{order_num}"
        if repo.has_payment(charge_key):
            return PlainTextResponse("OK")  # already credited -- webhook can be re-delivered

        try:
            amount = float(str(data.get("sum") or data.get("amount") or plan.price_rub))
        except (TypeError, ValueError):
            amount = float(plan.price_rub)

        repo.extend_subscription(chat_id, plan.days)
        repo.record_payment(chat_id, plan.id, "prodamus", amount, "RUB", charge_key)

        discount_used = max(0.0, float(plan.price_rub) - amount)
        if discount_used > 0:
            repo.consume_referral_balance(chat_id, discount_used)
        buyer = repo.get_user(chat_id)
        if buyer is not None and buyer.referred_by is not None:
            repo.credit_referral_balance(
                buyer.referred_by, billing.referral_commission_rub(amount, "RUB")
            )

        if bot is not None:
            try:
                await bot.send_message(chat_id, f"✅ Подписка продлена на {plan.label}. Спасибо!")
            except Exception:
                logger.exception("Prodamus webhook: failed to notify chat_id=%s", chat_id)

        return PlainTextResponse("OK")

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
