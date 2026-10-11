"""OpenLigaDB (api.openligadb.de) -- free, no API key, for German leagues (owner
2026-10-11). Plugged into ai.py's form() chain BEFORE the paid/keyed fallbacks (nothing
to lose by trying it first: no quota, no signup) -- it only ever actually returns data
for a Bundesliga-family team, everything else falls through to football-data.org/ESPN
same as before.

Best-effort: None on any failure, same convention as football_data.py/team_form.py.
"""
from __future__ import annotations

import time

import httpx

from bot.webapp.football_stats import display_team_name, resolve_search_name
from bot.webapp.team_form import _norm

API_BASE = "https://api.openligadb.de"
_REQUEST_TIMEOUT = 10.0
_MATCHES_TTL = 1800  # 30 min -- a season's match list changes at most once a matchday
FORM_MATCHES = 5

# German leagues on OpenLigaDB, domestic tiers first (a team resolves there before
# cup competitions get checked).
LEAGUES = ["bl1", "bl2", "bl3", "dfb"]

_cache: dict[str, dict] = {}


async def _season_matches(client: httpx.AsyncClient, shortcut: str) -> list[dict]:
    cached = _cache.get(shortcut)
    if cached and time.time() - cached["at"] < _MATCHES_TTL:
        return cached["rows"]
    try:
        resp = await client.get(f"{API_BASE}/getmatchdata/{shortcut}", timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        rows = resp.json() or []
    except (httpx.HTTPError, ValueError):
        return cached["rows"] if cached else []
    _cache[shortcut] = {"at": time.time(), "rows": rows}
    return rows


def _is_team(name: str, wanted: str) -> bool:
    n = _norm(name)
    if not n:
        return False
    if n == wanted or wanted in n or n in wanted:
        return True
    # "Bayern Munich" (our mapped form) vs OpenLigaDB's "FC Bayern München" ("munchen"
    # after accent-stripping, not "munich") -- a shared distinctive word is enough.
    n_words = {w for w in n.split() if len(w) >= 4}
    wanted_words = {w for w in wanted.split() if len(w) >= 4}
    return bool(n_words & wanted_words)


def _team_matches(rows: list[dict], wanted: str) -> list[tuple[dict, bool]]:
    """Each match this team played, paired with whether it was team1 (home)."""
    out = []
    for m in rows:
        if _is_team(m["team1"]["teamName"], wanted):
            out.append((m, True))
        elif _is_team(m["team2"]["teamName"], wanted):
            out.append((m, False))
    return out


async def get_team_form(client: httpx.AsyncClient, team_name: str) -> dict | None:
    """Same shape as team_form.get_team_form (so the frontend/AI prompt reads it
    identically): {team, form, played, wins, draws, losses, gf, ga, matches, standing}."""
    wanted = _norm(resolve_search_name(team_name))
    if not wanted:
        return None
    for shortcut in LEAGUES:
        rows = await _season_matches(client, shortcut)
        if not rows:
            continue
        matches = _team_matches(rows, wanted)
        if not matches:
            continue
        name = matches[0][0]["team1"]["teamName"] if matches[0][1] else matches[0][0]["team2"]["teamName"]
        finished = [(m, home) for m, home in matches if m.get("matchIsFinished")]
        finished.sort(key=lambda t: t[0].get("matchDateTimeUTC") or "", reverse=True)
        finished = finished[:FORM_MATCHES]
        if not finished:
            continue
        results, out_matches, gf, ga = [], [], 0, 0
        for m, home in finished:
            final = next((r for r in m.get("matchResults", []) if r.get("resultTypeKind") == "After90Minutes"), None) \
                or (m["matchResults"][-1] if m.get("matchResults") else None)
            if final is None:
                continue
            us, them = (final["pointsTeam1"], final["pointsTeam2"]) if home else (final["pointsTeam2"], final["pointsTeam1"])
            gf, ga = gf + us, ga + them
            r = "W" if us > them else "D" if us == them else "L"
            results.append(r)
            out_matches.append({
                "date": (m.get("matchDateTimeUTC") or "")[:10],
                "opponent": m["team2"]["teamName"] if home else m["team1"]["teamName"],
                "result": r, "score": f"{us}:{them}",
            })
        if not results:
            continue
        return {
            "team": display_team_name(name),
            "form": "".join(results),
            "played": len(results),
            "wins": results.count("W"), "draws": results.count("D"), "losses": results.count("L"),
            "gf": gf, "ga": ga,
            "matches": out_matches,
            "standing": None,
        }
    return None
