"""Head-to-head history for hockey from API-Hockey (v1.hockey.api-sports.io) -- the
same api-sports.io account/key as API_FOOTBALL_KEY already covers this product too
(confirmed live 2026-10-11: no separate signup needed, same x-apisports-key header).
Mirrors bot/webapp/football_stats.py's get_match_h2h shape so bot/analysis/ai.py's
_gather can treat hockey the same way it already treats football.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

API_BASE = "https://v1.hockey.api-sports.io"
H2H_LOOKBACK_MATCHES = 8
_REQUEST_TIMEOUT = 10.0


async def _search_team(client: httpx.AsyncClient, team_name: str, api_key: str) -> dict | None:
    try:
        resp = await client.get(
            f"{API_BASE}/teams", params={"search": team_name},
            headers={"x-apisports-key": api_key}, timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        results = (resp.json() or {}).get("response") or []
    except (httpx.HTTPError, ValueError):
        return None
    return results[0] if results else None


async def get_match_h2h(client: httpx.AsyncClient, team_a: str, team_b: str, api_key: str) -> dict | None:
    """None on any failure/no data. Otherwise {"total", "team_a_wins", "team_b_wins",
    "matches": [{"date", "home", "away", "home_score", "away_score"}, ...]} (most
    recent H2H_LOOKBACK_MATCHES, newest first). Hockey has no draws (OT/shootout
    always decides a winner), so unlike football there's no "draws" count."""
    a, b = await _search_team(client, team_a, api_key), await _search_team(client, team_b, api_key)
    if not a or not b:
        return None
    try:
        resp = await client.get(
            f"{API_BASE}/games/h2h", params={"h2h": f"{a['id']}-{b['id']}"},
            headers={"x-apisports-key": api_key}, timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        games = (resp.json() or {}).get("response") or []
    except (httpx.HTTPError, ValueError):
        return None
    played = [g for g in games if (g.get("scores") or {}).get("home") is not None]
    if not played:
        return None
    played.sort(key=lambda g: g.get("timestamp") or 0, reverse=True)
    a_wins = b_wins = 0
    matches = []
    for g in played[:H2H_LOOKBACK_MATCHES]:
        home, away = g["teams"]["home"], g["teams"]["away"]
        hs, as_ = g["scores"]["home"], g["scores"]["away"]
        winner_id = home["id"] if hs > as_ else away["id"]
        a_wins += winner_id == a["id"]
        b_wins += winner_id == b["id"]
        matches.append({"date": g.get("date", "")[:10], "home": home["name"], "away": away["name"],
                        "home_score": hs, "away_score": as_})
    return {"total": len(played), "team_a_wins": a_wins, "team_b_wins": b_wins, "matches": matches}
