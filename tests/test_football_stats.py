import asyncio

import httpx
import pytest

from bot.webapp import football_stats as fs


def _run(coro):
    return asyncio.run(coro)


def test_resolve_search_name_maps_known_cyrillic_team():
    assert fs.resolve_search_name("Спартак") == "Spartak Moscow"
    assert fs.resolve_search_name("Реал Мадрид") == "Real Madrid"


def test_resolve_search_name_passes_through_unknown_name():
    assert fs.resolve_search_name("FC Something Unmapped") == "FC Something Unmapped"


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_search_team_id_returns_first_result_id():
    def handler(request):
        assert request.headers["x-apisports-key"] == "key123"
        return httpx.Response(200, json={"response": [{"team": {"id": 541, "name": "Real Madrid"}}]})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.search_team_id(client, "Реал Мадрид", "key123")

    assert _run(go()) == 541


def test_search_team_id_returns_none_when_empty():
    def handler(request):
        return httpx.Response(200, json={"response": []})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.search_team_id(client, "Nobody FC", "key123")

    assert _run(go()) is None


def test_search_team_id_returns_none_on_http_error():
    def handler(request):
        return httpx.Response(403, json={"errors": {"plan": "nope"}})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.search_team_id(client, "Real Madrid", "key123")

    assert _run(go()) is None


def _fixture(home_id, away_id, home_name, away_name, home_score, away_score, ts, date):
    return {
        "fixture": {"status": {"short": "FT"}, "timestamp": ts, "date": date},
        "teams": {"home": {"id": home_id, "name": home_name}, "away": {"id": away_id, "name": away_name}},
        "goals": {"home": home_score, "away": away_score},
    }


def test_get_head_to_head_tallies_results_relative_to_team_a():
    fixtures = [
        _fixture(541, 529, "Real Madrid", "Barcelona", 2, 1, 300, "2024-01-01T20:00:00+00:00"),  # A win (A home)
        _fixture(529, 541, "Barcelona", "Real Madrid", 1, 1, 200, "2023-01-01T20:00:00+00:00"),  # draw
        _fixture(529, 541, "Barcelona", "Real Madrid", 3, 0, 100, "2022-01-01T20:00:00+00:00"),  # B win (B home)
    ]

    def handler(request):
        return httpx.Response(200, json={"response": fixtures})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.get_head_to_head(client, 541, 529, "key123")

    result = _run(go())
    assert result["total"] == 3
    assert result["team_a_wins"] == 1
    assert result["team_b_wins"] == 1
    assert result["draws"] == 1
    # newest first
    assert result["matches"][0]["date"] == "2024-01-01"


def test_get_head_to_head_ignores_non_finished_fixtures():
    fixtures = [
        {"fixture": {"status": {"short": "NS"}, "timestamp": 1, "date": "2026-01-01T00:00:00+00:00"},
         "teams": {"home": {"id": 541}, "away": {"id": 529}}, "goals": {"home": None, "away": None}},
    ]

    def handler(request):
        return httpx.Response(200, json={"response": fixtures})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.get_head_to_head(client, 541, 529, "key123")

    assert _run(go()) is None


def test_get_head_to_head_returns_none_when_no_shared_history():
    def handler(request):
        return httpx.Response(200, json={"response": []})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.get_head_to_head(client, 1, 2, "key123")

    assert _run(go()) is None


def test_get_match_h2h_returns_none_without_api_key():
    async def go():
        async with httpx.AsyncClient() as client:
            return await fs.get_match_h2h(client, "Спартак", "Зенит", "")

    assert _run(go()) is None


def test_get_match_h2h_returns_none_when_a_team_is_not_found():
    def handler(request):
        if "Real" in request.url.params.get("search", ""):
            return httpx.Response(200, json={"response": [{"team": {"id": 541}}]})
        return httpx.Response(200, json={"response": []})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fs.get_match_h2h(client, "Real Madrid", "Nobody FC", "key123")

    assert _run(go()) is None
