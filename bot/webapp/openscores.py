"""Extra fallback for football team form via OpenScores (owner 2026-10-11, key
osk_live_...). OpenScores is an MCP server (Streamable HTTP + JSON-RPC, not a plain
REST API) covering only the top-5 European leagues + the 2026 World Cup, so this is
wired in ai.py's form() chain AFTER football-data.org and ESPN, which cover far more
leagues -- OpenScores only ever gets used when both of those already failed for a
top-5/World Cup team. Each call opens its own short-lived MCP session (connection
handshake cost is fine here since this is a rare last-resort path, not the common one).
"""
from __future__ import annotations

import json

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from bot.webapp.football_stats import display_team_name

MCP_URL = "https://api.openscores.ai/mcp/"
FORM_MATCHES = 5
_TIMEOUT = 10.0


async def get_team_form(team_name: str, api_key: str) -> dict | None:
    """Same shape as bot.webapp.team_form.get_team_form, so the frontend (and the AI
    prompt) renders/reads it identically regardless of which source it came from."""
    if not api_key:
        return None
    try:
        client = httpx.AsyncClient(headers={"Authorization": f"Bearer {api_key}"}, timeout=_TIMEOUT)
        async with streamable_http_client(MCP_URL, http_client=client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                found_raw = await session.call_tool("search_teams", {"q": team_name})
                found = json.loads(found_raw.content[0].text) if found_raw.content else {}
                items = found.get("items") or []
                if not items:
                    return None
                team_raw = await session.call_tool("get_team", {"team_id": items[0]["id"]})
                team = json.loads(team_raw.content[0].text) if team_raw.content else {}
    except Exception:
        return None

    finished = [f for f in (team.get("recent_fixtures") or []) if f.get("status") == "finished"][:FORM_MATCHES]
    if not finished:
        return None
    name = team.get("name", team_name)
    results, matches, gf, ga = [], [], 0, 0
    for f in finished:
        home = f.get("home_team_name") == name
        us, them = (f.get("home_score"), f.get("away_score")) if home else (f.get("away_score"), f.get("home_score"))
        if us is None or them is None:
            continue
        gf, ga = gf + us, ga + them
        r = "W" if us > them else "D" if us == them else "L"
        results.append(r)
        matches.append({
            "date": (f.get("scheduled_kickoff_at") or "")[:10],
            "opponent": f.get("away_team_name") if home else f.get("home_team_name"),
            "result": r, "score": f"{f.get('home_score')}:{f.get('away_score')}",
        })
    if not results:
        return None
    return {
        "team": display_team_name(name),
        "form": "".join(results),
        "played": len(results),
        "wins": results.count("W"), "draws": results.count("D"), "losses": results.count("L"),
        "gf": gf, "ga": ga,
        "matches": matches,
        "standing": None,
    }
