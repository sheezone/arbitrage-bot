"""JSON API backing the Telegram Mini App (bot/webapp/static/*) -- the same data/actions
as the button-based bot UI in bot/handlers/commands.py, reusing its business logic
(billing, monitor helpers, repository) rather than reimplementing any of it. Every
endpoint requires a valid Telegram `initData` (see auth.py) in the `Authorization: tma
<initData>` header -- there is no other auth, so a request with a missing/invalid/expired
one is rejected outright rather than falling back to some anonymous/demo mode."""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from bot.core import billing
from bot.core.arbitrage import calc_stakes
from bot.core.monitor import BOOKMAKER_URLS, GAME_EMOJI, format_match_start, user_allows_arb, within_time_horizon
from bot.core.state import LatestState
from bot.db.repository import Repository, UserSettings
from bot.handlers.commands import (
    BANKROLL_PRESETS,
    GAME_LABELS,
    TIME_HORIZONS,
    _ALL_BOOKMAKER_KEYS,
    _AGGREGATOR_BOOKMAKERS,
    _DIRECT_BOOKMAKERS,
)
from bot.webapp.auth import validate_init_data
from bot.webapp.football_stats import get_match_h2h, get_popular_upcoming_fixtures
from bot.webapp.news import fetch_team_news, pick_popular_matches

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


def _user_out(user: UserSettings, admin_chat_ids: frozenset[int]) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "chat_id": user.chat_id,
        "bankroll": user.bankroll,
        "min_profit_pct": user.min_profit_pct,
        "time_horizons": user.time_horizons,
        "allowed_bookmakers": user.allowed_bookmakers,
        "is_active": user.is_active,
        "muted": user.muted,
        "is_admin": billing.is_admin(user, admin_chat_ids),
        "has_access": billing.has_access(user, now, admin_chat_ids),
        "on_trial": billing.on_trial(user, now),
        "days_left": billing.days_left(user, now),
        # Admins bypass the 1/day analysis quota entirely -- always "available".
        "analysis_available": billing.is_admin(user, admin_chat_ids) or user.last_analysis_date != now.date().isoformat(),
    }


class SettingsIn(BaseModel):
    bankroll: float | None = None
    min_profit_pct: float | None = None
    time_horizons: list[int] | None = None
    allowed_bookmakers: list[str] | None = None
    muted: bool | None = None


def register_api(
    repo: Repository, state: LatestState, admin_chat_ids: frozenset[int], api_football_key: str = ""
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
        return _user_out(user, admin_chat_ids)

    @app.post("/api/settings")
    async def post_settings(body: SettingsIn, authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
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

        return _user_out(_get_user(repo, chat_id), admin_chat_ids)

    @app.get("/api/bookmakers")
    async def get_bookmakers(authorization: str | None = Header(default=None)):
        _auth(authorization)  # any authenticated user may read the static list
        def _row(key: str) -> dict:
            category = "direct" if key in _DIRECT_BOOKMAKERS else "aggregator" if key in _AGGREGATOR_BOOKMAKERS else "other"
            return {"key": key, "label": key.upper(), "url": BOOKMAKER_URLS.get(key), "category": category}
        return {"bookmakers": [_row(k) for k in _ALL_BOOKMAKER_KEYS], "presets": {"bankroll": BANKROLL_PRESETS}}

    @app.get("/api/stats")
    async def get_stats(authorization: str | None = Header(default=None)):
        _auth(authorization)
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
        _auth(authorization)

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
        if not in_fixtures:
            picked = pick_popular_matches(state.matches, limit=3)
            match = next((m for m in picked if m.team_a == team_a and m.team_b == team_b), None)
            if match is None:
                raise HTTPException(status_code=404, detail="Матч больше не в списке популярных")
            if match.game != "football":
                raise HTTPException(status_code=400, detail="Анализ пока доступен только для футбола")

        async with httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}) as client:
            h2h = await get_match_h2h(client, team_a, team_b, api_football_key)

        if not is_admin:
            repo.set_last_analysis_date(chat_id, today)
        return {"team_a": team_a, "team_b": team_b, "h2h": h2h}

    @app.get("/api/vilki")
    async def get_vilki(authorization: str | None = Header(default=None)):
        chat_id = _auth(authorization)
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

        def _match_out(m) -> dict:
            stakes = calc_stakes(user.bankroll, m.arb.best_odds)
            return {
                "game": m.game,
                "game_label": GAME_LABELS.get(m.game, m.game.upper()),
                "game_emoji": GAME_EMOJI.get(m.game, "🏆"),
                "team_a": m.team_a,
                "team_b": m.team_b,
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

        return {
            "updated_at": state.updated_at,
            "matches": [_match_out(m) for m in matches],
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
                }
                for u in repo.get_recent_users(limit=10)
            ],
        }

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
