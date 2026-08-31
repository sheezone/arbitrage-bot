"""Uses httpx.AsyncClient + ASGITransport (driven via asyncio.run per test) rather than
starlette's sync TestClient -- TestClient runs the ASGI app in a separate worker thread
via an anyio portal, which crashes here because Repository's sqlite3 connection can only
be used from the thread that created it (confirmed live, see api.py's register_api
docstring). Production only ever runs everything on one thread/event loop (uvicorn's
Server.serve() is awaited directly on the bot's existing loop, never uvicorn.run()), so an
async, single-thread test matches real execution -- TestClient's thread-hopping doesn't."""
import asyncio
import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BOT_TOKEN = "123456:test-token"


def _init_data(chat_id: int, bot_token: str = BOT_TOKEN) -> str:
    fields = {"user": json.dumps({"id": chat_id}), "auth_date": str(int(time.time()))}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


def _auth_header(chat_id: int) -> dict:
    return {"Authorization": "tma " + _init_data(chat_id)}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    from bot.core.state import LatestState
    from bot.db.repository import Repository
    from bot.webapp.api import register_api

    repo = Repository(str(tmp_path / "test.sqlite3"))
    state = LatestState()
    app = register_api(repo, state, admin_chat_ids=frozenset({99}))
    return app, repo, state


def _run(coro):
    return asyncio.run(coro)


async def _get(app, path, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path, headers=headers)


async def _post(app, path, headers=None, json_body=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, headers=headers, json=json_body)


def test_me_requires_auth(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/me"))
    assert resp.status_code == 401


def test_me_rejects_bad_init_data(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/me", headers={"Authorization": "tma garbage"}))
    assert resp.status_code == 401


def test_me_creates_and_returns_a_new_user(setup):
    app, repo, _ = setup
    resp = _run(_get(app, "/api/me", headers=_auth_header(555)))
    assert resp.status_code == 200
    body = resp.json()
    assert body["chat_id"] == 555
    assert body["on_trial"] is True
    assert repo.get_user(555) is not None


def test_me_reports_admin_flag(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/me", headers=_auth_header(99)))
    assert resp.json()["is_admin"] is True
    resp2 = _run(_get(app, "/api/me", headers=_auth_header(1)))
    assert resp2.json()["is_admin"] is False


def test_settings_updates_bankroll(setup):
    app, repo, _ = setup
    _run(_get(app, "/api/me", headers=_auth_header(1)))  # ensure the row exists
    resp = _run(_post(app, "/api/settings", headers=_auth_header(1), json_body={"bankroll": 2500}))
    assert resp.status_code == 200
    assert resp.json()["bankroll"] == 2500
    assert repo.get_user(1).bankroll == 2500


def test_settings_rejects_non_positive_bankroll(setup):
    app, _, _ = setup
    resp = _run(_post(app, "/api/settings", headers=_auth_header(1), json_body={"bankroll": 0}))
    assert resp.status_code == 400


def test_settings_rejects_emptying_all_time_horizons(setup):
    app, _, _ = setup
    resp = _run(_post(app, "/api/settings", headers=_auth_header(1), json_body={"time_horizons": [999]}))
    assert resp.status_code == 400


def test_settings_selecting_every_bookmaker_stores_as_no_restriction(setup):
    app, repo, _ = setup
    from bot.handlers.commands import _ALL_BOOKMAKER_KEYS

    resp = _run(
        _post(app, "/api/settings", headers=_auth_header(1), json_body={"allowed_bookmakers": list(_ALL_BOOKMAKER_KEYS)})
    )
    assert resp.status_code == 200
    assert repo.get_user(1).allowed_bookmakers == []


def test_bookmakers_endpoint_lists_categories(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/bookmakers", headers=_auth_header(1)))
    assert resp.status_code == 200
    body = resp.json()
    assert any(b["key"] == "fonbet" and b["category"] == "direct" for b in body["bookmakers"])
    assert any(b["key"] == "winline" and b["category"] == "aggregator" for b in body["bookmakers"])


def test_stats_endpoint_returns_zeroed_stats_when_empty(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/stats", headers=_auth_header(1)))
    assert resp.status_code == 200
    assert resp.json()["today_count"] == 0


def test_vilki_endpoint_empty_with_no_matches(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    assert resp.status_code == 200
    assert resp.json()["matches"] == []


def test_vilki_endpoint_filters_and_shapes_matches(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    _run(_get(app, "/api/me", headers=_auth_header(1)))  # ensure the row exists before set_bankroll
    repo.set_bankroll(1, 1000)
    best_odds = [OutcomeOdds("Team A", "fonbet", 2.1), OutcomeOdds("Team B", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Team A", "Team B", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    body = resp.json()
    assert len(body["matches"]) == 1
    m = body["matches"][0]
    assert m["team_a"] == "Team A"
    assert m["profit_amount"] == 50.0
    assert m["legs"][0]["bookmaker"] == "FONBET"
    assert m["legs"][0]["bookmaker_url"]


def test_news_endpoint_requires_auth(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/news"))
    assert resp.status_code == 401


def test_news_endpoint_returns_headlines_for_picked_matches(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot
    from email.utils import format_datetime
    from datetime import datetime, timezone

    best_odds = [OutcomeOdds("Реал Мадрид", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Реал Мадрид", "Барселона", arb, "2026-08-29T20:00:00+00:00")]

    async def fake_fetch_team_news(client, team_a, team_b):
        return [{"title": "Травма перед матчем", "link": "https://example.com/x", "source": "Example", "published_at": 0}]

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)

    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["matches"]) == 1
    assert body["matches"][0]["team_a"] == "Реал Мадрид"
    assert body["matches"][0]["headlines"][0]["title"] == "Травма перед матчем"


def test_news_endpoint_caches_across_calls(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Реал Мадрид", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Реал Мадрид", "Барселона", arb, "2026-08-29T20:00:00+00:00")]

    call_count = 0

    async def fake_fetch_team_news(client, team_a, team_b):
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)

    _run(_get(app, "/api/news", headers=_auth_header(1)))
    calls_after_first = call_count
    _run(_get(app, "/api/news", headers=_auth_header(1)))
    assert call_count == calls_after_first  # second call served from cache, no new requests
