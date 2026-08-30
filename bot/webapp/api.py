"""JSON API backing the Telegram Mini App (bot/webapp/static/*) -- the same data/actions
as the button-based bot UI in bot/handlers/commands.py, reusing its business logic
(billing, monitor helpers, repository) rather than reimplementing any of it. Every
endpoint requires a valid Telegram `initData` (see auth.py) in the `Authorization: tma
<initData>` header -- there is no other auth, so a request with a missing/invalid/expired
one is rejected outright rather than falling back to some anonymous/demo mode."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

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

STATIC_DIR = Path(__file__).resolve().parent / "static"


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
    }


class SettingsIn(BaseModel):
    bankroll: float | None = None
    min_profit_pct: float | None = None
    time_horizons: list[int] | None = None
    allowed_bookmakers: list[str] | None = None
    muted: bool | None = None


def register_api(repo: Repository, state: LatestState, admin_chat_ids: frozenset[int]) -> FastAPI:
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

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
