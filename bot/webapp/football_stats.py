"""Head-to-head (H2H) history from API-Football (api-sports.io) -- the one section of
the user's original "full analyst report" template that's actually usable on its free
tier. Checked live against the account's own free key (2026-08-31):

- `fixtures?team=...&last=N` (recent form) -> "Free plans do not have access to the Last
  parameter."
- `fixtures?team=...&season=2025` (current-season fixtures) -> "Free plans do not have
  access to this season, try from 2022 to 2024."
- `injuries?team=...&season=2025` -- same season restriction, so no CURRENT injury data
  either (only 2021-2024, useless for a match happening now).
- `fixtures/headtohead?h2h=<idA>-<idB>` -- no season restriction at all, returned 51 real
  historical meetings between Real Madrid/Barcelona going back to 2018. This is the only
  piece that's both current-relevant and actually within the free tier's limits.

Still strictly facts, no verdict/percentage -- same rule as bot/webapp/news.py, this just
adds one more real data point (H2H record + score history) to look at alongside the
headlines, nothing that predicts the outcome."""
from __future__ import annotations

import httpx

API_BASE = "https://v3.football.api-sports.io"
H2H_LOOKBACK_MATCHES = 8
_REQUEST_TIMEOUT = 10.0

# API-Football's team search is Latin-name based -- our own team names come from
# Russian bookmakers, so a Cyrillic "Спартак" needs mapping to what the API actually
# indexes before search_team_id has anything to find. Same curated scope as
# news.POPULAR_TEAMS (deliberately not exhaustive -- a team missing from here just
# means no H2H block for that match, not an error).
TEAM_NAME_EN = {
    "спартак": "Spartak Moscow",
    "цска": "CSKA Moscow",
    "зенит": "Zenit",
    "динамо": "Dynamo Moscow",
    "локомотив": "Lokomotiv Moscow",
    "краснодар": "Krasnodar",
    "ростов": "Rostov",
    "рубин": "Rubin Kazan",
    "реал мадрид": "Real Madrid",
    "реал": "Real Madrid",
    "барселона": "Barcelona",
    "атлетико": "Atletico Madrid",
    "манчестер юнайтед": "Manchester United",
    "манчестер сити": "Manchester City",
    "ливерпуль": "Liverpool",
    "челси": "Chelsea",
    "арсенал": "Arsenal",
    "тоттенхэм": "Tottenham",
    "бавария": "Bayern Munich",
    "боруссия дортмунд": "Borussia Dortmund",
    "псж": "Paris Saint Germain",
    "ювентус": "Juventus",
    "милан": "AC Milan",
    "интер": "Inter",
    "наполи": "Napoli",
    "рома": "AS Roma",
    "аякс": "Ajax",
    "порту": "FC Porto",
    "бенфика": "Benfica",
    "россия": "Russia",
    "бразилия": "Brazil",
    "аргентина": "Argentina",
    "франция": "France",
    "германия": "Germany",
    "испания": "Spain",
    "англия": "England",
    "португалия": "Portugal",
    "италия": "Italy",
}


def resolve_search_name(team_name: str) -> str:
    """Falls back to the original name unchanged if it's not a recognized RU club/
    national-team name -- lets an already-Latin name (rare in this bot's data, but
    possible) through untouched rather than mangling it."""
    lowered = team_name.lower()
    for key, english in TEAM_NAME_EN.items():
        if key in lowered:
            return english
    return team_name


async def search_team_id(client: httpx.AsyncClient, team_name: str, api_key: str) -> int | None:
    try:
        resp = await client.get(
            f"{API_BASE}/teams",
            params={"search": resolve_search_name(team_name)},
            headers={"x-apisports-key": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    results = data.get("response") or []
    if not results:
        return None
    # First hit is API-Football's own best-relevance match (confirmed live: searching
    # "Real Madrid" ranks the senior men's team above "Real Madrid U19" etc.).
    return results[0].get("team", {}).get("id")


async def get_head_to_head(client: httpx.AsyncClient, team_a_id: int, team_b_id: int, api_key: str) -> dict | None:
    """None on any failure/no data. Otherwise {"total", "team_a_wins", "team_b_wins",
    "draws", "matches": [{"date", "home", "away", "home_score", "away_score"}, ...]}
    (most recent H2H_LOOKBACK_MATCHES, newest first)."""
    try:
        resp = await client.get(
            f"{API_BASE}/fixtures/headtohead",
            params={"h2h": f"{team_a_id}-{team_b_id}"},
            headers={"x-apisports-key": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    fixtures = [f for f in (data.get("response") or []) if (f.get("fixture", {}).get("status", {}).get("short")) == "FT"]
    if not fixtures:
        return None
    fixtures.sort(key=lambda f: f.get("fixture", {}).get("timestamp") or 0, reverse=True)

    team_a_wins = team_b_wins = draws = 0
    matches = []
    for f in fixtures:
        home = f.get("teams", {}).get("home", {})
        away = f.get("teams", {}).get("away", {})
        goals = f.get("goals", {})
        home_score, away_score = goals.get("home"), goals.get("away")
        if home_score is None or away_score is None:
            continue

        home_id = home.get("id")
        if home_score == away_score:
            draws += 1
        elif (home_score > away_score) == (home_id == team_a_id):
            team_a_wins += 1
        else:
            team_b_wins += 1

        if len(matches) < H2H_LOOKBACK_MATCHES:
            matches.append({
                "date": (f.get("fixture", {}).get("date") or "")[:10],
                "home": home.get("name"),
                "away": away.get("name"),
                "home_score": home_score,
                "away_score": away_score,
            })

    total = team_a_wins + team_b_wins + draws
    if total == 0:
        return None
    return {"total": total, "team_a_wins": team_a_wins, "team_b_wins": team_b_wins, "draws": draws, "matches": matches}


async def get_match_h2h(client: httpx.AsyncClient, team_a: str, team_b: str, api_key: str) -> dict | None:
    """End-to-end: resolve both team names to API-Football IDs, then fetch H2H. None at
    any step (team not found, no shared history, request failure) rather than raising --
    an H2H block just doesn't appear for that match, the rest of the news digest still
    does."""
    if not api_key:
        return None
    team_a_id = await search_team_id(client, team_a, api_key)
    team_b_id = await search_team_id(client, team_b, api_key)
    if team_a_id is None or team_b_id is None:
        return None
    return await get_head_to_head(client, team_a_id, team_b_id, api_key)
