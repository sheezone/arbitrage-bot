import asyncio

import httpx

from bot.webapp import football_data as fd


def _run(coro):
    return asyncio.run(coro)


def _standings_payload(rows):
    return {
        "standings": [
            {"type": "TOTAL", "table": [
                {"position": p, "points": pts, "playedGames": pld, "team": {"id": tid, "name": name}}
                for (p, pts, pld, tid, name) in rows
            ]}
        ]
    }


def _matches_payload(entries):
    return {"matches": entries}


def _m(date, home, hid, away, aid, hs, as_):
    return {
        "utcDate": date + "T19:00:00Z",
        "homeTeam": {"id": hid, "name": home, "shortName": home},
        "awayTeam": {"id": aid, "name": away, "shortName": away},
        "score": {"fullTime": {"home": hs, "away": as_}},
    }


def test_returns_none_without_key():
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as c:
            return await fd.get_team_form(c, "Real Madrid", "")

    assert _run(go()) is None


def test_resolves_team_and_builds_form():
    fd._standings_cache.clear()

    def handler(request):
        url = str(request.url)
        assert request.headers["X-Auth-Token"] == "k"
        if "/competitions/PD/standings" in url:
            return httpx.Response(200, json=_standings_payload([
                (1, 12, 4, 81, "FC Barcelona"),
                (3, 9, 4, 86, "Real Madrid CF"),
            ]))
        if url.startswith("https://api.football-data.org/v4/competitions/") and url.endswith("/standings"):
            return httpx.Response(200, json=_standings_payload([]))
        if "/teams/86/matches" in url:
            return httpx.Response(200, json=_matches_payload([
                _m("2026-08-22", "Espanyol", 77, "Real Madrid CF", 86, 1, 2),
                _m("2026-08-30", "Real Madrid CF", 86, "Malaga", 99, 4, 0),
                _m("2026-09-04", "Real Betis", 90, "Real Madrid CF", 86, 1, 0),
            ]))
        return httpx.Response(404, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fd.get_team_form(c, "Реал Мадрид", "k")

    form = _run(go())
    assert form is not None
    assert form["team"] == "Real Madrid CF"
    assert form["form"] == "LWW"  # newest-first: 04.09 L, 30.08 W, 22.08 W
    assert form["wins"] == 2 and form["losses"] == 1
    assert form["gf"] == 6 and form["ga"] == 2
    assert form["standing"] == {"rank": 3, "points": 9, "played": 4}


def test_returns_none_when_team_not_in_any_table():
    fd._standings_cache.clear()

    def handler(request):
        if str(request.url).endswith("/standings"):
            return httpx.Response(200, json=_standings_payload([(1, 3, 1, 1, "Some Club")]))
        return httpx.Response(404, json={})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            return await fd.get_team_form(c, "Totally Unknown FC", "k")

    assert _run(go()) is None
