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
from datetime import datetime, timezone
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


async def _post_form(app, path, data, headers=None):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(path, headers=headers, data=data)


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
    assert m["team_a_flag"] is None  # "Team A"/"Team B" aren't in the curated flag list
    assert m["team_b_flag"] is None


def test_vilki_endpoint_includes_team_flags_for_recognized_teams(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    _run(_get(app, "/api/me", headers=_auth_header(1)))
    repo.set_bankroll(1, 1000)
    best_odds = [OutcomeOdds("Спартак", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Спартак", "Барселона", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    m = resp.json()["matches"][0]
    assert m["team_a_flag"] == "🇷🇺"
    assert m["team_b_flag"] == "🇪🇸"


def test_vilki_endpoint_includes_nba_logos_for_basketball(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    _run(_get(app, "/api/me", headers=_auth_header(1)))
    repo.set_bankroll(1, 1000)
    best_odds = [OutcomeOdds("Houston Rockets", "fonbet", 2.1), OutcomeOdds("Dallas Mavericks", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("basketball", "Houston Rockets", "Dallas Mavericks", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    m = resp.json()["matches"][0]
    assert m["team_a_logo"] == "https://a.espncdn.com/i/teamlogos/nba/500/hou.png"
    assert m["team_b_logo"] == "https://a.espncdn.com/i/teamlogos/nba/500/dal.png"


def test_vilki_endpoint_fetches_and_caches_football_logos(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    _run(_get(app, "/api/me", headers=_auth_header(1)))
    repo.set_bankroll(1, 1000)
    best_odds = [OutcomeOdds("Спартак", "fonbet", 2.1), OutcomeOdds("Зенит", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Спартак", "Зенит", arb, "2026-08-29T20:00:00+00:00")]

    call_count = 0

    async def fake_search_team_logo(client, team_name, api_key):
        nonlocal call_count
        call_count += 1
        return f"https://logo.example/{team_name}.png"

    monkeypatch.setattr("bot.webapp.api.search_team_logo", fake_search_team_logo)

    from bot.webapp.api import register_api

    app = register_api(repo, state, admin_chat_ids=frozenset({99}), api_football_key="test-key")
    resp1 = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    m1 = resp1.json()["matches"][0]
    assert m1["team_a_logo"] == "https://logo.example/Спартак.png"
    assert m1["team_b_logo"] == "https://logo.example/Зенит.png"
    assert call_count == 2

    # Second poll (client refreshes every 20s) must not re-fetch already-known logos.
    _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    assert call_count == 2


def test_vilki_endpoint_football_logo_is_none_without_api_key(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    _run(_get(app, "/api/me", headers=_auth_header(1)))
    repo.set_bankroll(1, 1000)
    best_odds = [OutcomeOdds("Спартак", "fonbet", 2.1), OutcomeOdds("Зенит", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Спартак", "Зенит", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))  # setup's app has no api_football_key
    m = resp.json()["matches"][0]
    assert m["team_a_logo"] is None
    assert m["team_b_logo"] is None


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


def test_news_endpoint_flags_football_matches_as_analyzable_without_fetching_h2h(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Реал Мадрид", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Реал Мадрид", "Барселона", arb, "2026-08-29T20:00:00+00:00")]

    async def fake_fetch_team_news(client, team_a, team_b):
        return []

    async def fail_get_match_h2h(client, team_a, team_b, api_key):
        raise AssertionError("/api/news must not fetch H2H -- that's gated behind /api/analysis")

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)
    monkeypatch.setattr("bot.webapp.api.get_match_h2h", fail_get_match_h2h)

    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    body = resp.json()
    assert body["matches"][0]["can_analyze"] is True
    assert "h2h" not in body["matches"][0]


def test_news_endpoint_flags_non_football_matches_as_not_analyzable(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Team A", "fonbet", 2.1), OutcomeOdds("Team B", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("tennis", "Team A", "Team B", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    assert resp.json()["matches"][0]["can_analyze"] is False


def _football_state(state):
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Реал Мадрид", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Реал Мадрид", "Барселона", arb, "2026-08-29T20:00:00+00:00")]


def test_news_endpoint_prefers_real_upcoming_fixtures_over_arb_pool(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    # An arb-derived match that would show under the old behavior -- should be pushed
    # out once a real upcoming fixture is available.
    best_odds = [OutcomeOdds("Old Arb Team A", "fonbet", 2.1), OutcomeOdds("Old Arb Team B", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("football", "Old Arb Team A", "Old Arb Team B", arb, "2026-08-29T20:00:00+00:00")]

    async def fake_fetch_team_news(client, team_a, team_b):
        return []

    async def fake_get_popular_upcoming_fixtures(client, api_key, limit=3):
        assert api_key == "test-football-key"
        return [{"team_a": "Реал Мадрид", "team_b": "Барселона", "start_time_utc": "2026-09-02T20:00:00+00:00", "league": "La Liga"}]

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)
    monkeypatch.setattr("bot.webapp.api.get_popular_upcoming_fixtures", fake_get_popular_upcoming_fixtures)

    from bot.webapp.api import register_api

    app = register_api(repo, state, admin_chat_ids=frozenset({99}), api_football_key="test-football-key")
    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    body = resp.json()
    assert body["matches"][0]["team_a"] == "Реал Мадрид"
    assert body["matches"][0]["can_analyze"] is True


def test_news_endpoint_pads_with_arb_pool_when_fewer_than_3_real_fixtures(setup, monkeypatch):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Team A", "fonbet", 2.1), OutcomeOdds("Team B", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("tennis", "Team A", "Team B", arb, "2026-08-29T20:00:00+00:00")]

    async def fake_fetch_team_news(client, team_a, team_b):
        return []

    async def fake_get_popular_upcoming_fixtures(client, api_key, limit=3):
        return [{"team_a": "Реал Мадрид", "team_b": "Барселона", "start_time_utc": "2026-09-02T20:00:00+00:00", "league": "La Liga"}]

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)
    monkeypatch.setattr("bot.webapp.api.get_popular_upcoming_fixtures", fake_get_popular_upcoming_fixtures)

    from bot.webapp.api import register_api

    app = register_api(repo, state, admin_chat_ids=frozenset({99}), api_football_key="test-football-key")
    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    teams = [(m["team_a"], m["team_b"]) for m in resp.json()["matches"]]
    assert ("Реал Мадрид", "Барселона") in teams
    assert ("Team A", "Team B") in teams


def test_analysis_endpoint_requires_auth(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/analysis?team_a=A&team_b=B"))
    assert resp.status_code == 401


def test_analysis_endpoint_returns_h2h_and_consumes_daily_quota(setup, monkeypatch):
    app, repo, state = setup
    _football_state(state)

    async def fake_get_match_h2h(client, team_a, team_b, api_key):
        assert api_key == "test-football-key"
        return {"total": 3, "team_a_wins": 1, "team_b_wins": 1, "draws": 1, "matches": []}

    monkeypatch.setattr("bot.webapp.api.get_match_h2h", fake_get_match_h2h)

    from bot.webapp.api import register_api

    app = register_api(repo, state, admin_chat_ids=frozenset({99}), api_football_key="test-football-key")
    resp = _run(
        _get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(1))
    )
    assert resp.status_code == 200
    assert resp.json()["h2h"]["total"] == 3
    today = datetime.now(timezone.utc).date().isoformat()
    assert repo.has_analyzed_today(1, "Реал Мадрид", "Барселона", today)


def test_analysis_endpoint_rejects_a_second_call_same_day(setup, monkeypatch):
    app, repo, state = setup
    _football_state(state)

    async def fake_get_match_h2h(client, team_a, team_b, api_key):
        return {"total": 1, "team_a_wins": 1, "team_b_wins": 0, "draws": 0, "matches": []}

    monkeypatch.setattr("bot.webapp.api.get_match_h2h", fake_get_match_h2h)

    resp1 = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(1)))
    assert resp1.status_code == 200
    resp2 = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(1)))
    assert resp2.status_code == 429


def test_analysis_quota_is_per_match_not_per_user(setup, monkeypatch):
    # Confirmed-live bug (2026-09-11): analysing one popular match made the button
    # vanish for the OTHER popular matches too, for the rest of the day. The quota must
    # only block re-analysing the SAME match.
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Реал Мадрид", "fonbet", 2.1), OutcomeOdds("Барселона", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    best_odds2 = [OutcomeOdds("Ливерпуль", "fonbet", 2.1), OutcomeOdds("Челси", "olimpbet", 2.05)]
    arb2 = ArbitrageResult(best_odds=best_odds2, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [
        MatchSnapshot("football", "Реал Мадрид", "Барселона", arb, "2026-08-29T20:00:00+00:00"),
        MatchSnapshot("football", "Ливерпуль", "Челси", arb2, "2026-08-29T20:00:00+00:00"),
    ]

    async def fake_get_match_h2h(client, team_a, team_b, api_key):
        return {"total": 1, "team_a_wins": 1, "team_b_wins": 0, "draws": 0, "matches": []}

    monkeypatch.setattr("bot.webapp.api.get_match_h2h", fake_get_match_h2h)

    resp1 = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(1)))
    assert resp1.status_code == 200
    # Same match again -> still blocked.
    resp_repeat = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(1)))
    assert resp_repeat.status_code == 429
    # A DIFFERENT match, same user, same day -> must go through.
    resp2 = _run(_get(app, "/api/analysis?team_a=Ливерпуль&team_b=Челси", headers=_auth_header(1)))
    assert resp2.status_code == 200


def test_news_endpoint_marks_already_analyzed_per_match(setup, monkeypatch):
    app, repo, state = setup
    _football_state(state)

    async def fake_fetch_team_news(client, team_a, team_b):
        return []

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)

    today = datetime.now(timezone.utc).date().isoformat()
    repo.record_analysis_use(1, "Реал Мадрид", "Барселона", today)

    resp = _run(_get(app, "/api/news", headers=_auth_header(1)))
    assert resp.json()["matches"][0]["already_analyzed"] is True

    # A different user hasn't used their quota on this match.
    resp2 = _run(_get(app, "/api/news", headers=_auth_header(2)))
    assert resp2.json()["matches"][0]["already_analyzed"] is False


def test_analysis_endpoint_lets_admins_bypass_the_daily_quota(setup, monkeypatch):
    app, repo, state = setup  # setup's admin_chat_ids = frozenset({99})
    _football_state(state)

    async def fake_get_match_h2h(client, team_a, team_b, api_key):
        return {"total": 1, "team_a_wins": 1, "team_b_wins": 0, "draws": 0, "matches": []}

    monkeypatch.setattr("bot.webapp.api.get_match_h2h", fake_get_match_h2h)

    resp1 = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(99)))
    assert resp1.status_code == 200
    resp2 = _run(_get(app, "/api/analysis?team_a=Реал Мадрид&team_b=Барселона", headers=_auth_header(99)))
    assert resp2.status_code == 200  # a normal user would get 429 here, see the test above
    today = datetime.now(timezone.utc).date().isoformat()
    assert not repo.has_analyzed_today(99, "Реал Мадрид", "Барселона", today)  # never recorded for an admin


def test_news_endpoint_never_marks_already_analyzed_for_admins(setup, monkeypatch):
    app, repo, state = setup
    repo.upsert_user(99)
    _football_state(state)

    async def fake_fetch_team_news(client, team_a, team_b):
        return []

    monkeypatch.setattr("bot.webapp.api.fetch_team_news", fake_fetch_team_news)

    today = datetime.now(timezone.utc).date().isoformat()
    repo.record_analysis_use(99, "Реал Мадрид", "Барселона", today)  # shouldn't happen, but even if it did:

    resp = _run(_get(app, "/api/news", headers=_auth_header(99)))
    assert resp.json()["matches"][0]["already_analyzed"] is False


def test_analysis_endpoint_rejects_a_match_not_currently_popular(setup):
    app, repo, state = setup
    _football_state(state)

    resp = _run(_get(app, "/api/analysis?team_a=Unknown&team_b=Nobody", headers=_auth_header(1)))
    assert resp.status_code == 404


def test_analysis_endpoint_rejects_non_football(setup):
    app, repo, state = setup
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import MatchSnapshot

    best_odds = [OutcomeOdds("Team A", "fonbet", 2.1), OutcomeOdds("Team B", "olimpbet", 2.05)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0)
    state.matches = [MatchSnapshot("tennis", "Team A", "Team B", arb, "2026-08-29T20:00:00+00:00")]

    resp = _run(_get(app, "/api/analysis?team_a=Team A&team_b=Team B", headers=_auth_header(1)))
    assert resp.status_code == 400


def test_admin_stats_requires_auth(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/admin/stats"))
    assert resp.status_code == 401


def test_admin_stats_rejects_non_admin(setup):
    app, _, _ = setup
    resp = _run(_get(app, "/api/admin/stats", headers=_auth_header(1)))
    assert resp.status_code == 403


def test_admin_stats_returns_aggregate_counts(setup):
    app, repo, state = setup  # setup's admin_chat_ids = frozenset({99})
    repo.upsert_user(1, acquisition_source="telega_ads1")
    repo.upsert_user(2)
    repo.upsert_user(3, referred_by=1)
    repo.set_active(2, False)

    resp = _run(_get(app, "/api/admin/stats", headers=_auth_header(99)))
    assert resp.status_code == 200
    body = resp.json()
    # +1 for the admin's own row, created by _get_user during _auth
    assert body["total_users"] == 4
    assert body["on_trial"] == 4
    assert body["active_notifications"] == 3
    assert body["referred_count"] == 1
    assert {"source": "telega_ads1", "count": 1} in body["acquisition_sources"]
    assert body["payments"] == []
    assert any(u["chat_id"] == 1 and u["acquisition_source"] == "telega_ads1" for u in body["recent_users"])

    user1 = next(u for u in body["recent_users"] if u["chat_id"] == 1)
    assert user1["access_start"] == repo.get_user(1).trial_started_at
    # A brand-new user's access_end is just their trial end (3 days out), well after now.
    from datetime import datetime, timezone

    assert datetime.fromisoformat(user1["access_end"]) > datetime.now(timezone.utc)


class _FakeMember:
    def __init__(self, status: str):
        self.status = status


class _FakeGateBot:
    """Membership is keyed by chat_id -- "member" unless explicitly overridden to
    "left", so tests can flip individual users without a real Bot/network call."""

    def __init__(self):
        self.statuses: dict[int, str] = {}

    async def get_chat_member(self, channel_id, user_chat_id):
        return _FakeMember(self.statuses.get(user_chat_id, "member"))


def _gated_app(tmp_path, monkeypatch, admin_chat_ids=frozenset({99}), bot=None):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    from bot.core.state import LatestState
    from bot.db.repository import Repository
    from bot.webapp.api import register_api

    repo = Repository(str(tmp_path / "gated.sqlite3"))
    state = LatestState()
    app = register_api(
        repo,
        state,
        admin_chat_ids=admin_chat_ids,
        bot=bot or _FakeGateBot(),
        required_channel_id=-1009999,
        required_channel_username="testchan",
    )
    return app, repo, state


def test_me_reports_channel_required_and_subscribed_when_gate_is_off(setup):
    app, _, _ = setup  # setup's app has no bot/required_channel_id -- gate off
    resp = _run(_get(app, "/api/me", headers=_auth_header(1)))
    body = resp.json()
    assert body["channel_required"] is False
    assert body["is_subscribed"] is True


def test_me_reports_not_subscribed_when_gate_is_on_and_user_left(tmp_path, monkeypatch):
    bot = _FakeGateBot()
    bot.statuses[1] = "left"
    app, _, _ = _gated_app(tmp_path, monkeypatch, bot=bot)

    resp = _run(_get(app, "/api/me", headers=_auth_header(1)))
    body = resp.json()
    assert body["channel_required"] is True
    assert body["is_subscribed"] is False
    assert body["channel_username"] == "testchan"


def test_me_reports_subscribed_when_gate_is_on_and_user_is_a_member(tmp_path, monkeypatch):
    app, _, _ = _gated_app(tmp_path, monkeypatch)  # default _FakeGateBot -> everyone "member"
    resp = _run(_get(app, "/api/me", headers=_auth_header(1)))
    assert resp.json()["is_subscribed"] is True


def test_vilki_endpoint_403s_when_not_subscribed(tmp_path, monkeypatch):
    bot = _FakeGateBot()
    bot.statuses[1] = "left"
    app, _, _ = _gated_app(tmp_path, monkeypatch, bot=bot)

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    assert resp.status_code == 403


def test_vilki_endpoint_works_when_subscribed(tmp_path, monkeypatch):
    app, _, _ = _gated_app(tmp_path, monkeypatch)
    resp = _run(_get(app, "/api/vilki", headers=_auth_header(1)))
    assert resp.status_code == 200


def test_admins_bypass_the_gate_even_when_not_subscribed(tmp_path, monkeypatch):
    bot = _FakeGateBot()
    bot.statuses[99] = "left"  # the admin themself hasn't subscribed
    app, _, _ = _gated_app(tmp_path, monkeypatch, admin_chat_ids=frozenset({99}), bot=bot)

    resp = _run(_get(app, "/api/vilki", headers=_auth_header(99)))
    assert resp.status_code == 200
    me = _run(_get(app, "/api/me", headers=_auth_header(99))).json()
    assert me["is_subscribed"] is True  # reported as subscribed too, not just bypassed


def test_settings_endpoint_403s_when_not_subscribed(tmp_path, monkeypatch):
    bot = _FakeGateBot()
    bot.statuses[1] = "left"
    app, _, _ = _gated_app(tmp_path, monkeypatch, bot=bot)

    resp = _run(_post(app, "/api/settings", headers=_auth_header(1), json_body={"bankroll": 500}))
    assert resp.status_code == 403


# ---- Prodamus СБП webhook ----

def _prodamus_app(tmp_path, monkeypatch, secret="pkey"):
    monkeypatch.setenv("BOT_TOKEN", BOT_TOKEN)
    from bot.core.state import LatestState
    from bot.db.repository import Repository
    from bot.webapp.api import register_api

    repo = Repository(str(tmp_path / "t.sqlite3"))
    app = register_api(
        repo, LatestState(), admin_chat_ids=frozenset({99}), prodamus_secret_key=secret
    )
    return app, repo


def _signed_form(secret, **fields):
    from bot.providers import prodamus

    pairs = list(fields.items())
    data = prodamus.parse_php_form([(k, str(v)) for k, v in pairs])
    sign = prodamus.compute_signature(data, secret)
    return fields, sign


def test_prodamus_webhook_extends_subscription_on_success(tmp_path, monkeypatch):
    app, repo = _prodamus_app(tmp_path, monkeypatch)
    repo.upsert_user(42)
    before = repo.get_user(42).subscription_expires_at

    fields, sign = _signed_form(
        "pkey",
        order_num="sbp-42-30d-1700000000",
        sum="999.00",
        payment_status="success",
    )
    resp = _run(_post_form(app, "/api/prodamus/webhook", fields, headers={"Sign": sign}))
    assert resp.status_code == 200
    after = repo.get_user(42).subscription_expires_at
    assert after != before and after is not None
    assert repo.has_payment("prodamus:sbp-42-30d-1700000000")


def test_prodamus_webhook_rejects_bad_signature(tmp_path, monkeypatch):
    app, repo = _prodamus_app(tmp_path, monkeypatch)
    repo.upsert_user(42)
    resp = _run(_post_form(
        app, "/api/prodamus/webhook",
        {"order_num": "sbp-42-30d-1", "sum": "999", "payment_status": "success"},
        headers={"Sign": "deadbeef"},
    ))
    assert resp.status_code == 403
    assert not repo.has_payment("prodamus:sbp-42-30d-1")


def test_prodamus_webhook_is_idempotent(tmp_path, monkeypatch):
    app, repo = _prodamus_app(tmp_path, monkeypatch)
    repo.upsert_user(7)
    fields, sign = _signed_form(
        "pkey", order_num="sbp-7-7d-1", sum="299.00", payment_status="success"
    )
    _run(_post_form(app, "/api/prodamus/webhook", fields, headers={"Sign": sign}))
    first = repo.get_user(7).subscription_expires_at
    _run(_post_form(app, "/api/prodamus/webhook", fields, headers={"Sign": sign}))
    assert repo.get_user(7).subscription_expires_at == first  # not extended twice


def test_prodamus_webhook_ignores_non_success_status(tmp_path, monkeypatch):
    app, repo = _prodamus_app(tmp_path, monkeypatch)
    repo.upsert_user(5)
    fields, sign = _signed_form(
        "pkey", order_num="sbp-5-7d-1", sum="299.00", payment_status="pending"
    )
    resp = _run(_post_form(app, "/api/prodamus/webhook", fields, headers={"Sign": sign}))
    assert resp.status_code == 200
    assert not repo.has_payment("prodamus:sbp-5-7d-1")
