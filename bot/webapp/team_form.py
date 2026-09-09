"""Recent form + league position for a football team, from ESPN's hidden site JSON
(site.api.espn.com) -- free, no API key, no signup, and (unlike API-Football's free
tier, see football_stats.py) it actually exposes current-season results and standings.

Scope note: this is the "form / recent games / table position" context the user asked
for on 2026-09-08, when the earlier facts-only stance was relaxed (see that memory /
news.py). Everything here is still real results -- scores, W/D/L, table rank -- not an
invented verdict. The only probability the app shows is market-implied (derived from
the arb's own bookmaker odds in api.py), never a fabricated model output.

Best-effort throughout: every function returns None / [] on any failure rather than
raising, so a missing team or a flaky ESPN response just means no form block for that
match, same convention as football_stats.py.
"""
from __future__ import annotations

import unicodedata

import httpx

from bot.webapp.football_stats import display_team_name, resolve_search_name

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
ESPN_CORE_V2 = "https://site.api.espn.com/apis/v2/sports/soccer"
_REQUEST_TIMEOUT = 10.0
FORM_MATCHES = 5
# ESPN's edge 403s any User-Agent containing "Mozilla" or "bot" (confirmed live
# 2026-09-08) -- the shared AsyncClient in api.py sets "Mozilla/5.0", so every request
# here overrides it with a plain HTTP-library UA it does let through.
_HEADERS = {"User-Agent": "okhttp/4.9"}

# Domestic leagues we try to resolve a team id in, best-first. A team's /schedule
# endpoint returns every competition it plays (league + cups), so we only need its
# home-league slug to find the id. ESPN's own slugs.
LEAGUE_SLUGS = ["esp.1", "eng.1", "ita.1", "ger.1", "fra.1", "rus.1", "ned.1", "por.1"]

# Populated lazily on first lookup, kept for the life of the process -- the per-league
# team list changes only once a season.
_team_list_cache: dict[str, list[dict]] = {}


async def _league_teams(client: httpx.AsyncClient, slug: str) -> list[dict]:
    if slug in _team_list_cache:
        return _team_list_cache[slug]
    try:
        resp = await client.get(f"{ESPN_SITE}/{slug}/teams", headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        teams = [t["team"] for t in data["sports"][0]["leagues"][0]["teams"]]
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        teams = []
    _team_list_cache[slug] = teams
    return teams


def _norm(s: str) -> str:
    # Fold punctuation/spacing so "Paris Saint Germain" (our mapped form) matches
    # ESPN's "Paris Saint-Germain", "Atletico" vs "Atlético", etc.
    # Strip diacritics too: "Atlético" -> "atletico".
    folded = "".join(c for c in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(c))
    out = []
    for ch in folded:
        if ch.isalnum():
            out.append(ch)
        elif ch in " -_.'":
            out.append(" ")
    return " ".join("".join(out).split())


def _matches_name(team: dict, wanted: str) -> bool:
    w = _norm(wanted)
    if not w:
        return False
    for key in ("displayName", "shortDisplayName", "name", "nickname", "location"):
        val = _norm(team.get(key) or "")
        if val and (val == w or w in val or val in w):
            return True
    return False


async def _resolve_team(client: httpx.AsyncClient, team_name: str) -> tuple[str, str, str] | None:
    """(league_slug, team_id, espn_display_name) for the first league the name resolves
    in, or None. `resolve_search_name` first maps a Cyrillic bookmaker name (e.g.
    "Реал Мадрид") to the Latin form ESPN indexes."""
    wanted = resolve_search_name(team_name)
    for slug in LEAGUE_SLUGS:
        for team in await _league_teams(client, slug):
            if _matches_name(team, wanted):
                return slug, str(team.get("id")), team.get("displayName") or wanted
    return None


def _score_value(competitor: dict) -> int | None:
    raw = competitor.get("score")
    if isinstance(raw, dict):
        raw = raw.get("displayValue") or raw.get("value")
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return None


async def _recent_matches(client: httpx.AsyncClient, slug: str, team_id: str, display_name: str) -> list[dict]:
    try:
        resp = await client.get(f"{ESPN_SITE}/{slug}/teams/{team_id}/schedule", headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except (httpx.HTTPError, ValueError):
        return []

    out: list[dict] = []
    for ev in events:
        try:
            comp = ev["competitions"][0]
            if not comp["status"]["type"].get("completed"):
                continue
            competitors = comp["competitors"]
            me = next(c for c in competitors if str(c["team"].get("id")) == team_id)
            opp = next(c for c in competitors if c is not me)
        except (KeyError, IndexError, StopIteration):
            continue
        gf, ga = _score_value(me), _score_value(opp)
        if gf is None or ga is None:
            continue
        if me.get("winner") is True:
            result = "W"
        elif me.get("winner") is False and gf != ga:
            result = "L"
        else:
            result = "D"
        out.append({
            "date": (ev.get("date") or "")[:10],
            "opponent": display_team_name(opp["team"].get("displayName") or opp["team"].get("name") or "?"),
            "home_away": me.get("homeAway") or "",
            "gf": gf,
            "ga": ga,
            "result": result,
            "competition": (comp.get("type", {}) or {}).get("text") or ev.get("shortName") or "",
        })
    out.sort(key=lambda m: m["date"], reverse=True)
    return out[:FORM_MATCHES]


async def _standing(client: httpx.AsyncClient, slug: str, team_id: str) -> dict | None:
    try:
        resp = await client.get(f"{ESPN_CORE_V2}/{slug}/standings", headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except (httpx.HTTPError, ValueError):
        return None
    children = data.get("children") or [data]
    for child in children:
        for entry in (child.get("standings", {}) or {}).get("entries", []):
            if str(entry.get("team", {}).get("id")) != team_id:
                continue
            stats = {s.get("name"): s for s in entry.get("stats", [])}
            def _num(name):
                s = stats.get(name)
                if not s:
                    return None
                try:
                    return int(float(s.get("value")))
                except (TypeError, ValueError):
                    return None
            return {
                "rank": _num("rank"),
                "points": _num("points"),
                "played": _num("gamesPlayed"),
            }
    return None


async def get_team_form(client: httpx.AsyncClient, team_name: str) -> dict | None:
    """{"team", "form", "played", "wins", "draws", "losses", "gf", "ga", "matches":[...],
    "standing": {"rank","points","played"} | None} or None if the team can't be resolved
    / has no completed matches this season."""
    resolved = await _resolve_team(client, team_name)
    if resolved is None:
        return None
    slug, team_id, display_name = resolved
    matches = await _recent_matches(client, slug, team_id, display_name)
    if not matches:
        return None
    wins = sum(1 for m in matches if m["result"] == "W")
    draws = sum(1 for m in matches if m["result"] == "D")
    losses = sum(1 for m in matches if m["result"] == "L")
    return {
        "team": display_team_name(display_name),
        "form": "".join(m["result"] for m in matches),  # newest-first, e.g. "WWDLW"
        "played": len(matches),
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": sum(m["gf"] for m in matches),
        "ga": sum(m["ga"] for m in matches),
        "matches": matches,
        "standing": await _standing(client, slug, team_id),
    }


def implied_probabilities(best_odds) -> list[dict] | None:
    """Market-implied win probability per outcome, normalised so they sum to 1 --
    p_i = (1/odds_i) / sum(1/odds_j). `best_odds` is arb.best_odds (list of
    OutcomeOdds). Not a model, just the bookmakers' own prices with the margin
    divided out; the frontend labels it as such."""
    try:
        inv = [(o.outcome_name, 1.0 / o.odds) for o in best_odds]
    except (AttributeError, ZeroDivisionError, TypeError):
        return None
    total = sum(v for _, v in inv)
    if total <= 0:
        return None
    return [{"outcome": name, "prob": round(v / total, 4)} for name, v in inv]
