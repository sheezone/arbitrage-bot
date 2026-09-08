"""Recent form (last N games, any opponent) and league-table position from
football-data.org v4 -- fills the one gap API-Football's free tier can't (see
bot/webapp/football_stats.py's module docstring: `last=N` and `standings` both require a
paid API-Football plan). Same "strictly facts, no verdict/percentage" rule as the rest of
the analysis feature: this surfaces real results and a real table position, nothing that
scores or predicts an outcome.

NOT confirmed against a live football-data.org key from this environment (this session's
network egress is restricted to an allowlist that doesn't include api.football-data.org --
every call here degrades to None/[] on any HTTP error, same convention as
football_stats.py, so a wrong assumption about the free tier's exact response shape fails
silently rather than crashing, but it does mean this needs a real check against a live key
before being trusted in production). Verify once deployed and adjust the endpoints/shapes
below if the free tier behaves differently than documented.

Coverage gap worth being upfront about: football-data.org's free tier only covers
FREE_COMPETITIONS below -- mainly Western European top flights + international
tournaments. It does NOT include the Russian Premier League, so this block simply won't
appear for Spartak/CSKA/Zenit/etc. matches (same graceful "just don't show it" convention
as everywhere else in this feature) -- only API-Football's H2H still works for those.

No team-name search endpoint exists on the free tier, so team IDs are resolved by fetching
each free competition's own team list once (build_team_index) and matching on name/short
name -- avoids hardcoding any numeric IDs that can't be verified live."""
from __future__ import annotations

import httpx

API_BASE = "https://api.football-data.org/v4"
_REQUEST_TIMEOUT = 10.0
RECENT_FORM_MATCHES = 5

# Competitions covered by football-data.org's free tier (documented, not season-restricted
# the way API-Football's team+season queries are) -- codes are football-data.org's own.
FREE_COMPETITIONS: dict[str, str] = {
    "PL": "Premier League",
    "BL1": "Bundesliga",
    "FL1": "Ligue 1",
    "SA": "Serie A",
    "PD": "La Liga",
    "CL": "Лига чемпионов",
    "PPL": "Primeira Liga",
    "DED": "Eredivisie",
    "ELC": "Championship",
    "BSA": "Серия A (Бразилия)",
}


async def build_team_index(client: httpx.AsyncClient, api_key: str) -> dict[str, dict]:
    """{name_lower or short_name_lower: {"id", "competition_code", "competition_name",
    "display_name"}} across every FREE_COMPETITIONS -- one request per competition
    (~10), meant to be built once and cached with a long TTL by the caller (see api.py),
    not rebuilt per request. A competition that fails to fetch is just skipped, same
    fail-open convention as the rest of this module; {} if the key is empty or every
    competition fails."""
    if not api_key:
        return {}

    index: dict[str, dict] = {}
    for code, name in FREE_COMPETITIONS.items():
        try:
            resp = await client.get(
                f"{API_BASE}/competitions/{code}/teams",
                headers={"X-Auth-Token": api_key},
                timeout=_REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError):
            continue
        for team in data.get("teams") or []:
            team_id = team.get("id")
            if team_id is None:
                continue
            entry = {
                "id": team_id,
                "competition_code": code,
                "competition_name": name,
                "display_name": team.get("name") or team.get("shortName") or "",
            }
            for key in (team.get("name"), team.get("shortName"), team.get("tla")):
                if key:
                    index[key.lower()] = entry
    return index


def find_team(team_index: dict[str, dict], team_name: str) -> dict | None:
    """Substring match against the pre-built index (same tolerant convention as
    football_stats.resolve_search_name) -- e.g. "Реал Мадрид" won't match football-data's
    English names directly, so this is really only useful for teams whose bookmaker name
    is already Latin/English (the Western European clubs this source actually covers)."""
    lowered = team_name.lower()
    for key, entry in team_index.items():
        if key in lowered or lowered in key:
            return entry
    return None


async def get_recent_form(client: httpx.AsyncClient, team_id: int, api_key: str, limit: int = RECENT_FORM_MATCHES) -> dict | None:
    """None on any failure/no data. Otherwise {"form_string": "WWDLW" (newest last, left
    to right oldest->newest, matching how a form strip usually reads), "matches": [{"date",
    "opponent", "home_away", "result", "goals_for", "goals_against"}, ...]} (newest
    first)."""
    try:
        resp = await client.get(
            f"{API_BASE}/teams/{team_id}/matches",
            params={"status": "FINISHED", "limit": limit},
            headers={"X-Auth-Token": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    raw_matches = data.get("matches") or []
    raw_matches.sort(key=lambda m: m.get("utcDate") or "", reverse=True)

    matches = []
    for m in raw_matches[:limit]:
        home = m.get("homeTeam") or {}
        away = m.get("awayTeam") or {}
        score = m.get("score", {}).get("fullTime") or {}
        home_goals, away_goals = score.get("home"), score.get("away")
        if home_goals is None or away_goals is None:
            continue
        is_home = home.get("id") == team_id
        goals_for = home_goals if is_home else away_goals
        goals_against = away_goals if is_home else home_goals
        result = "W" if goals_for > goals_against else "L" if goals_for < goals_against else "D"
        matches.append({
            "date": (m.get("utcDate") or "")[:10],
            "opponent": (away if is_home else home).get("name"),
            "home_away": "home" if is_home else "away",
            "result": result,
            "goals_for": goals_for,
            "goals_against": goals_against,
        })

    if not matches:
        return None
    # Oldest-to-newest reading order, matching the H2H form strip's convention
    # (renderH2hForm in app.js reverses its own newest-first list the same way).
    form_string = "".join(m["result"] for m in reversed(matches))
    return {"form_string": form_string, "matches": matches}


async def get_standings_position(client: httpx.AsyncClient, team_id: int, competition_code: str, api_key: str) -> dict | None:
    """None on any failure/team not found. Otherwise {"position", "played", "points",
    "won", "draw", "lost", "goal_difference", "total_teams"} from the TOTAL standings
    table -- current league position, a fact, not an interpretation of it."""
    try:
        resp = await client.get(
            f"{API_BASE}/competitions/{competition_code}/standings",
            headers={"X-Auth-Token": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None

    for group in data.get("standings") or []:
        if group.get("type") != "TOTAL":
            continue
        table = group.get("table") or []
        for row in table:
            if (row.get("team") or {}).get("id") == team_id:
                return {
                    "position": row.get("position"),
                    "played": row.get("playedGames"),
                    "points": row.get("points"),
                    "won": row.get("won"),
                    "draw": row.get("draw"),
                    "lost": row.get("lost"),
                    "goal_difference": row.get("goalDifference"),
                    "total_teams": len(table),
                }
    return None


async def get_team_progress(client: httpx.AsyncClient, team_name: str, api_key: str, team_index: dict[str, dict]) -> dict | None:
    """End-to-end: resolve team_name against the pre-built index, then fetch recent form
    + standings position in that team's own competition. None if the team isn't covered
    by football-data.org's free-tier competitions (see module docstring) or the key is
    empty -- the analysis screen just doesn't show this block for that team, same
    graceful-degradation convention as everywhere else here."""
    if not api_key:
        return None
    team = find_team(team_index, team_name)
    if team is None:
        return None

    form = await get_recent_form(client, team["id"], api_key)
    standings = await get_standings_position(client, team["id"], team["competition_code"], api_key)
    if form is None and standings is None:
        return None
    return {
        "team_name": team["display_name"],
        "competition_name": team["competition_name"],
        "form": form,
        "standings": standings,
    }
