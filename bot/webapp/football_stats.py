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

from datetime import datetime, timedelta, timezone

import httpx

API_BASE = "https://v3.football.api-sports.io"
H2H_LOOKBACK_MATCHES = 8
_REQUEST_TIMEOUT = 10.0

# Well-known leagues, ranked by how "anticipated" a match in them generally is -- used to
# pick the 3 most notable upcoming fixtures out of a day's several hundred (2026-09-02
# live check: /fixtures?date=... returned 206 results for one day, worldwide, everything
# from top divisions to reserve/youth leagues). Lower value = shown first. IDs are
# API-Football's own (confirmed live).
LEAGUE_PRIORITY = {
    2: 0,    # UEFA Champions League
    3: 1,    # UEFA Europa League
    848: 2,  # UEFA Europa Conference League
    39: 3,   # Premier League
    140: 4,  # La Liga
    135: 5,  # Serie A
    78: 6,   # Bundesliga
    61: 7,   # Ligue 1
    235: 8,  # Russian Premier League
    88: 9,   # Eredivisie
    94: 10,  # Primeira Liga
}

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


# Best-effort reverse of TEAM_NAME_EN, for display -- e.g. "Real Madrid" -> "Реал
# Мадрид". A team not in the curated map just keeps its API-Football (English/Latin)
# name rather than showing nothing. TEAM_NAME_EN has some English values with more than
# one Cyrillic key pointing at them (e.g. both "реал" and "реал мадрид" -> "Real
# Madrid", so search hits on either) -- keep the LONGEST Cyrillic form for display
# rather than whichever happens to be last in dict order, so "Реал Мадрид" wins over
# the bare "Реал".
_TEAM_NAME_RU: dict[str, str] = {}
for _cyrillic, _english in TEAM_NAME_EN.items():
    _key = _english.lower()
    if _key not in _TEAM_NAME_RU or len(_cyrillic) > len(_TEAM_NAME_RU[_key]):
        _TEAM_NAME_RU[_key] = _cyrillic


def display_team_name(api_name: str) -> str:
    cyrillic = _TEAM_NAME_RU.get(api_name.lower())
    if cyrillic is None:
        return api_name
    return " ".join(word.capitalize() for word in cyrillic.split())


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


async def get_popular_upcoming_fixtures(
    client: httpx.AsyncClient, api_key: str, hours: int = 24, limit: int = 3
) -> list[dict]:
    """Real not-yet-started fixtures, kicking off within the next `hours`, from
    well-known leagues (see LEAGUE_PRIORITY) -- independent of
    bot/core/state.LatestState (which only ever holds matches an arb was actually found
    for, see news.py's module docstring). This answers "what are the most anticipated
    matches in the next 24h" for real, rather than "whatever happens to have a live arb
    right now".

    Uses the free tier's date-based /fixtures query -- confirmed live (2026-09-02) this
    does NOT hit the season-restriction error that blocks team+season/last queries (see
    the module docstring above). [] on any failure or if nothing in the priority leagues
    falls in the window, same "just don't show this section" convention as the rest of
    this module."""
    if not api_key:
        return []

    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(hours=hours)
    # The window can span two UTC calendar dates (e.g. checking at 23:00 UTC) -- the API
    # only takes a single `date`, so query every distinct date the window touches.
    dates = sorted({now.date().isoformat(), cutoff.date().isoformat()})

    fixtures: list[dict] = []
    for date_str in dates:
        try:
            resp = await client.get(
                f"{API_BASE}/fixtures",
                params={"date": date_str},
                headers={"x-apisports-key": api_key},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            continue
        fixtures.extend(data.get("response") or [])

    candidates = []
    for f in fixtures:
        if f.get("fixture", {}).get("status", {}).get("short") != "NS":  # not yet started
            continue
        ts = f.get("fixture", {}).get("timestamp")
        if not ts:
            continue
        kickoff = datetime.fromtimestamp(ts, tz=timezone.utc)
        if not (now <= kickoff <= cutoff):
            continue
        league_id = f.get("league", {}).get("id")
        priority = LEAGUE_PRIORITY.get(league_id)
        if priority is None:
            continue
        candidates.append((priority, kickoff, f))
    candidates.sort(key=lambda c: (c[0], c[1]))

    seen_pairs: set[tuple[str, str]] = set()
    out = []
    for _, kickoff, f in candidates:
        home = f.get("teams", {}).get("home", {}).get("name")
        away = f.get("teams", {}).get("away", {}).get("name")
        if not home or not away:
            continue
        pair = (home, away)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        out.append({
            "team_a": display_team_name(home),
            "team_b": display_team_name(away),
            "start_time_utc": kickoff.isoformat(),
            "league": f.get("league", {}).get("name"),
        })
        if len(out) >= limit:
            break
    return out
