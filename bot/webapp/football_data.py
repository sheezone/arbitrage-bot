"""football-data.org (v4) — authoritative league table + recent form for ~10 top
competitions on the free tier. Preferred over ESPN's hidden JSON (bot/webapp/
team_form.py) when a key is configured; ESPN stays the fallback for everything this
doesn't cover.

Free tier: 10 requests/minute, competitions PL / PD / SA / BL1 / FL1 / DED / PPL /
ELC / BSA / CL. Standings per competition are cached for the process lifetime of a
short TTL since they move at most once per matchday, so a lookup is usually 1 request
(the team's recent matches) on top of a warm cache.

Best-effort: every function returns None on any failure, same convention as
team_form.py / football_stats.py.
"""
from __future__ import annotations

import time

import httpx

from bot.webapp.football_stats import display_team_name, resolve_search_name
from bot.webapp.team_form import _norm

API_BASE = "https://api.football-data.org/v4"
_REQUEST_TIMEOUT = 10.0
_STANDINGS_TTL = 900  # 15 min
FORM_MATCHES = 5

# Club competitions on the free tier. Domestic leagues first (a team resolves there),
# CL last -- a team's /matches endpoint returns all competitions anyway.
COMPETITIONS = ["PD", "PL", "SA", "BL1", "FL1", "DED", "PPL", "ELC", "BSA", "CL"]

# {code: {"at": ts, "rows": [ {name, id, position, points, playedGames} ]}}
_standings_cache: dict[str, dict] = {}


def _headers(api_key: str) -> dict:
    return {"X-Auth-Token": api_key, "User-Agent": "arbitrage-bot"}


async def _standings(client: httpx.AsyncClient, code: str, api_key: str) -> list[dict]:
    cached = _standings_cache.get(code)
    if cached and time.time() - cached["at"] < _STANDINGS_TTL:
        return cached["rows"]
    try:
        resp = await client.get(
            f"{API_BASE}/competitions/{code}/standings",
            headers=_headers(api_key),
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        total = next((t for t in data.get("standings", []) if t.get("type") == "TOTAL"), None)
        rows = [
            {
                "name": r["team"]["name"],
                "id": r["team"]["id"],
                "position": r.get("position"),
                "points": r.get("points"),
                "playedGames": r.get("playedGames"),
            }
            for r in (total or {}).get("table", [])
        ]
    except (httpx.HTTPError, ValueError, KeyError, StopIteration):
        rows = []
    _standings_cache[code] = {"at": time.time(), "rows": rows}
    return rows


def _match_row(rows: list[dict], team_name: str) -> dict | None:
    # resolve_search_name maps a Cyrillic bookmaker name ("Реал Мадрид") to the Latin
    # form these APIs index; _norm then folds punctuation/diacritics/case.
    wanted = _norm(resolve_search_name(team_name))
    if not wanted:
        return None
    for row in rows:
        n = _norm(row["name"])
        if n and (n == wanted or wanted in n or n in wanted):
            return row
    return None


async def _recent_matches(client: httpx.AsyncClient, team_id: int, api_key: str) -> list[dict]:
    try:
        resp = await client.get(
            f"{API_BASE}/teams/{team_id}/matches",
            params={"status": "FINISHED", "limit": FORM_MATCHES},
            headers=_headers(api_key),
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        matches = resp.json().get("matches", [])
    except (httpx.HTTPError, ValueError):
        return []

    out: list[dict] = []
    for m in matches:
        try:
            home_id = m["homeTeam"]["id"]
            ft = m["score"]["fullTime"]
            hs, as_ = ft["home"], ft["away"]
            if hs is None or as_ is None:
                continue
            is_home = home_id == team_id
            gf, ga = (hs, as_) if is_home else (as_, hs)
            opp = m["awayTeam"] if is_home else m["homeTeam"]
        except (KeyError, TypeError):
            continue
        result = "W" if gf > ga else "L" if gf < ga else "D"
        out.append({
            "date": (m.get("utcDate") or "")[:10],
            "opponent": display_team_name(opp.get("shortName") or opp.get("name") or "?"),
            "home_away": "home" if is_home else "away",
            "gf": gf,
            "ga": ga,
            "result": result,
        })
    out.sort(key=lambda x: x["date"], reverse=True)
    return out[:FORM_MATCHES]


async def get_team_form(client: httpx.AsyncClient, team_name: str, api_key: str) -> dict | None:
    """Same shape as team_form.get_team_form (so the frontend renders it identically):
    {team, form, played, wins, draws, losses, gf, ga, matches, standing}."""
    if not api_key:
        return None
    for code in COMPETITIONS:
        rows = await _standings(client, code, api_key)
        row = _match_row(rows, team_name)
        if row is None:
            continue
        matches = await _recent_matches(client, row["id"], api_key)
        if not matches:
            return None
        wins = sum(1 for m in matches if m["result"] == "W")
        draws = sum(1 for m in matches if m["result"] == "D")
        losses = sum(1 for m in matches if m["result"] == "L")
        return {
            "team": display_team_name(row["name"]),
            "form": "".join(m["result"] for m in matches),
            "played": len(matches),
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "gf": sum(m["gf"] for m in matches),
            "ga": sum(m["ga"] for m in matches),
            "matches": matches,
            "standing": {
                "rank": row["position"],
                "points": row["points"],
                "played": row["playedGames"],
            } if row.get("position") else None,
        }
    return None
