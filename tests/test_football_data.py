import asyncio

import httpx

from bot.webapp import football_data as fd


def _run(coro):
    return asyncio.run(coro)


def _mock_transport(handler):
    return httpx.MockTransport(handler)


def test_build_team_index_returns_empty_without_api_key():
    async def go():
        async with httpx.AsyncClient() as client:
            return await fd.build_team_index(client, "")

    assert _run(go()) == {}


def test_build_team_index_merges_teams_across_competitions():
    def handler(request):
        code = request.url.path.split("/")[3]
        if code == "PL":
            return httpx.Response(200, json={"teams": [
                {"id": 65, "name": "Manchester City FC", "shortName": "Man City", "tla": "MCI"},
            ]})
        return httpx.Response(200, json={"teams": []})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.build_team_index(client, "key123")

    index = _run(go())
    assert index["manchester city fc"]["id"] == 65
    assert index["man city"]["competition_code"] == "PL"
    assert index["mci"]["display_name"] == "Manchester City FC"


def test_build_team_index_skips_a_failing_competition():
    def handler(request):
        code = request.url.path.split("/")[3]
        if code == "PL":
            return httpx.Response(500)
        return httpx.Response(200, json={"teams": [{"id": 1, "name": "Foo FC"}]})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.build_team_index(client, "key123")

    index = _run(go())
    assert "foo fc" in index


def test_find_team_matches_substring_case_insensitively():
    index = {"real madrid": {"id": 86, "competition_code": "PD", "competition_name": "La Liga", "display_name": "Real Madrid CF"}}
    assert fd.find_team(index, "Real Madrid CF")["id"] == 86
    assert fd.find_team(index, "unknown team") is None


def _match(team_id, opp_id, home_score, away_score, is_home, date):
    return {
        "utcDate": date,
        "homeTeam": {"id": team_id if is_home else opp_id, "name": "Home Team"},
        "awayTeam": {"id": opp_id if is_home else team_id, "name": "Away Team"},
        "score": {"fullTime": {"home": home_score, "away": away_score}},
    }


def test_get_recent_form_computes_results_relative_to_team():
    matches = [
        _match(65, 1, 2, 1, True, "2026-01-03T15:00:00Z"),   # win (home)
        _match(65, 2, 1, 1, False, "2026-01-02T15:00:00Z"),  # draw (away, score is home:1 away:1 -> team is away side w/ goals_for=1,against=1)
        _match(65, 3, 3, 0, False, "2026-01-01T15:00:00Z"),  # loss (away side, goals_for=0, against=3)
    ]

    def handler(request):
        return httpx.Response(200, json={"matches": matches})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_recent_form(client, 65, "key123", limit=5)

    result = _run(go())
    assert result["matches"][0]["result"] == "W"
    assert result["matches"][0]["date"] == "2026-01-03"
    # oldest-to-newest reading order
    assert result["form_string"] == "LDW"


def test_get_recent_form_returns_none_on_http_error():
    def handler(request):
        return httpx.Response(500)

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_recent_form(client, 65, "key123")

    assert _run(go()) is None


def test_get_recent_form_returns_none_when_no_finished_matches():
    def handler(request):
        return httpx.Response(200, json={"matches": []})

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_recent_form(client, 65, "key123")

    assert _run(go()) is None


def test_get_standings_position_finds_the_matching_team():
    payload = {"standings": [{"type": "TOTAL", "table": [
        {"position": 1, "team": {"id": 65}, "playedGames": 20, "points": 50, "won": 16, "draw": 2, "lost": 2, "goalDifference": 30},
        {"position": 2, "team": {"id": 66}, "playedGames": 20, "points": 45, "won": 14, "draw": 3, "lost": 3, "goalDifference": 20},
    ]}]}

    def handler(request):
        return httpx.Response(200, json=payload)

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_standings_position(client, 65, "PL", "key123")

    result = _run(go())
    assert result["position"] == 1
    assert result["points"] == 50
    assert result["total_teams"] == 2


def test_get_standings_position_returns_none_when_team_not_in_table():
    payload = {"standings": [{"type": "TOTAL", "table": [{"position": 1, "team": {"id": 999}, "playedGames": 1, "points": 1, "won": 0, "draw": 1, "lost": 0, "goalDifference": 0}]}]}

    def handler(request):
        return httpx.Response(200, json=payload)

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_standings_position(client, 65, "PL", "key123")

    assert _run(go()) is None


def test_get_team_progress_returns_none_when_team_not_in_index():
    async def go():
        async with httpx.AsyncClient() as client:
            return await fd.get_team_progress(client, "Спартак", "key123", {})

    assert _run(go()) is None


def test_get_team_progress_returns_none_without_api_key():
    index = {"real madrid": {"id": 86, "competition_code": "PD", "competition_name": "La Liga", "display_name": "Real Madrid CF"}}

    async def go():
        async with httpx.AsyncClient() as client:
            return await fd.get_team_progress(client, "Real Madrid", "", index)

    assert _run(go()) is None


def test_get_team_progress_combines_form_and_standings():
    index = {"real madrid": {"id": 86, "competition_code": "PD", "competition_name": "La Liga", "display_name": "Real Madrid CF"}}
    matches = [_match(86, 1, 2, 0, True, "2026-01-01T15:00:00Z")]
    standings_payload = {"standings": [{"type": "TOTAL", "table": [
        {"position": 1, "team": {"id": 86}, "playedGames": 1, "points": 3, "won": 1, "draw": 0, "lost": 0, "goalDifference": 2},
    ]}]}

    def handler(request):
        if "matches" in request.url.path:
            return httpx.Response(200, json={"matches": matches})
        return httpx.Response(200, json=standings_payload)

    async def go():
        async with httpx.AsyncClient(transport=_mock_transport(handler)) as client:
            return await fd.get_team_progress(client, "Real Madrid", "key123", index)

    result = _run(go())
    assert result["team_name"] == "Real Madrid CF"
    assert result["form"]["form_string"] == "W"
    assert result["standings"]["position"] == 1
