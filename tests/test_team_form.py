import asyncio

import httpx

from bot.core.arbitrage import OutcomeOdds
from bot.webapp import team_form as tf


def _run(coro):
    return asyncio.run(coro)


def test_implied_probabilities_normalise_to_one():
    best = [OutcomeOdds("Team A", "bk1", 2.0), OutcomeOdds("Team B", "bk2", 2.0)]
    probs = tf.implied_probabilities(best)
    assert probs == [{"outcome": "Team A", "prob": 0.5}, {"outcome": "Team B", "prob": 0.5}]


def test_implied_probabilities_removes_margin():
    # 1/1.8 + 1/2.1 = 0.5556 + 0.4762 = 1.0317 (a ~3% book margin) -> normalised.
    best = [OutcomeOdds("A", "bk", 1.8), OutcomeOdds("B", "bk", 2.1)]
    probs = tf.implied_probabilities(best)
    assert abs(sum(p["prob"] for p in probs) - 1.0) < 1e-6
    assert probs[0]["prob"] > probs[1]["prob"]  # shorter odds -> higher implied prob


def test_implied_probabilities_none_on_bad_input():
    assert tf.implied_probabilities([]) is None
    assert tf.implied_probabilities(None) is None


def _transport(handler):
    return httpx.MockTransport(handler)


def _teams_payload(entries):
    return {"sports": [{"leagues": [{"teams": [{"team": t} for t in entries]}]}]}


def _schedule_payload(events):
    return {"events": events}


def _event(date, opp_name, opp_id, my_id, my_score, opp_score, my_winner, home_away="home"):
    return {
        "date": date,
        "competitions": [
            {
                "status": {"type": {"completed": True}},
                "type": {"text": "La Liga"},
                "competitors": [
                    {"team": {"id": my_id, "displayName": "Real Madrid"}, "homeAway": home_away,
                     "score": {"displayValue": str(my_score)}, "winner": my_winner},
                    {"team": {"id": opp_id, "displayName": opp_name},
                     "homeAway": "away" if home_away == "home" else "home",
                     "score": {"displayValue": str(opp_score)}, "winner": not my_winner if my_score != opp_score else False},
                ],
            }
        ],
    }


def test_get_team_form_parses_recent_results():
    tf._team_list_cache.clear()

    def handler(request):
        url = str(request.url)
        if url.endswith("/esp.1/teams"):
            return httpx.Response(200, json=_teams_payload([{"id": "86", "displayName": "Real Madrid"}]))
        if "/teams/86/schedule" in url:
            return httpx.Response(200, json=_schedule_payload([
                _event("2026-08-30T00:00Z", "Malaga", "99", "86", 4, 0, True),
                _event("2026-08-22T00:00Z", "Espanyol", "88", "86", 1, 2, False, home_away="away"),
            ]))
        if "/esp.1/standings" in url:
            return httpx.Response(200, json={"children": [{"standings": {"entries": [
                {"team": {"id": "86"}, "stats": [
                    {"name": "rank", "value": 3}, {"name": "points", "value": 9}, {"name": "gamesPlayed", "value": 4},
                ]},
            ]}}]})
        return httpx.Response(404, json={})

    async def go():
        async with httpx.AsyncClient(transport=_transport(handler)) as client:
            return await tf.get_team_form(client, "Реал Мадрид")

    form = _run(go())
    assert form is not None
    assert form["team"] == "Real Madrid"
    assert form["form"] == "WL"  # newest-first
    assert form["wins"] == 1 and form["losses"] == 1 and form["draws"] == 0
    assert form["gf"] == 5 and form["ga"] == 2
    assert form["standing"] == {"rank": 3, "points": 9, "played": 4}


def test_get_team_form_none_when_team_unresolved():
    tf._team_list_cache.clear()

    def handler(request):
        if str(request.url).endswith("/teams"):
            return httpx.Response(200, json=_teams_payload([{"id": "1", "displayName": "Some Other Club"}]))
        return httpx.Response(404, json={})

    async def go():
        async with httpx.AsyncClient(transport=_transport(handler)) as client:
            return await tf.get_team_form(client, "Полностью Неизвестный Клуб XYZ")

    assert _run(go()) is None
