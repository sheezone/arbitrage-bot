"""Upcoming football matches with bookmaker odds, for the AI analysis menu.

Source: Fonbet's prematch line (the same listBase dump bot/providers/fonbet.py reads),
fetched on demand and cached for CATALOG_TTL. Fonbet is used because it has the widest
line and readable league ("segment") names. Season-long markets ("Итоги", "Лучший
бомбардир", "Сезон 26/27", stats/specials) and virtual FC 26 leagues share the football
parent with real matches -- they're filtered out by name, and an event only counts as a
match when it carries a 1X2 price.

Each match carries a fixed list of priced options (1X2, total ladder, half-line
handicaps). The AI must pick one of these by id, so a prediction can never quote odds
that don't exist, and every option is settleable from the final score.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

from bot.providers import fonbet
from bot.providers._line_platform import (
    DRAW_FACTOR,
    HANDICAP_PAIRS,
    TEAM1_WIN_FACTOR,
    TEAM2_WIN_FACTOR,
    TOTAL_LADDER_PAIRS,
)

FOOTBALL_PARENT_SPORT = 1
CATALOG_TTL = 300
HORIZON = timedelta(hours=48)      # the browsable top list
LOOKUP_HORIZON = timedelta(days=7)  # matches a user can find by name/screenshot
MAX_MATCHES = 40

SKIP_SEGMENT_WORDS = ("Итоги", "Лучший бомбардир", "Сезон", "Статистическ", "Специальные", "FC 26", "Кибер")
# Shown first, in this order; anything else that's a real match comes after.
TOP_LEAGUES = (
    "Лига Чемпионов УЕФА", "Лига Европы УЕФА", "Лига конференций УЕФА", "Лига Конференций УЕФА",
    "Лига наций УЕФА", "Чемпионат мира", "Чемпионат Европы",
    "Англия. Премьер-Лига", "Испания. Примера", "Италия. Серия А", "Германия. Бундеслига",
    "Франция. Лига 1", "Россия. Премьер-Лига", "Фонбет Кубок России",
    "Нидерланды. Премьер-Лига", "Португалия. Премьер-Лига", "Турция. Суперлига",
    "Англия. Чемпионшип", "Бельгия. Премьер-Лига", "Шотландия. Премьер-Лига",
    "США. MLS", "Бразилия. Серия А", "Аргентина. Премьер-Лига",
)


@dataclass(frozen=True)
class Option:
    id: str      # stable within a match, e.g. "1", "X", "2", "TO2.5", "TU2.5", "H1-1.5"
    label: str   # human text, e.g. "П1", "Тотал больше 2.5", "Фора Реал (-1.5)"
    odds: float
    kind: str    # "1x2" | "total_over" | "total_under" | "handicap1" | "handicap2"
    line: float = 0.0


@dataclass
class FootballMatch:
    id: str
    team_a: str
    team_b: str
    start_utc: datetime
    league: str
    options: list[Option] = field(default_factory=list)

    @property
    def priority(self) -> int:
        for n, prefix in enumerate(TOP_LEAGUES):
            if self.league.startswith(prefix):
                return n
        return len(TOP_LEAGUES)

    def option(self, option_id: str) -> Option | None:
        return next((o for o in self.options if o.id == option_id), None)


def parse_catalog(raw: dict, now: datetime | None = None, horizon: timedelta = HORIZON,
                  limit: int | None = MAX_MATCHES) -> list[FootballMatch]:
    now = now or datetime.now(timezone.utc)
    segments = {
        s["id"]: s.get("name") or ""
        for s in raw.get("sports", [])
        if s.get("kind") == "segment" and s.get("parentId") == FOOTBALL_PARENT_SPORT
        and not any(w in (s.get("name") or "") for w in SKIP_SEGMENT_WORDS)
    }
    factors = {
        cf.get("e"): {f["f"]: f for f in cf.get("factors", [])}
        for cf in raw.get("customFactors", []) if cf.get("e") is not None
    }
    matches: list[FootballMatch] = []
    for ev in raw.get("events", []):
        if ev.get("place") != "line" or ev.get("level", 1) != 1 or ev.get("sportId") not in segments:
            continue
        a, b = ev.get("team1"), ev.get("team2")
        if not a or not b or "(" in a or "(" in b or {a, b} == {"Хозяева", "Гости"}:
            continue  # stat-prop pseudo matches: "(угловые)" names, "Хозяева - Гости" aggregates
        start = datetime.fromtimestamp(ev.get("startTime") or 0, tz=timezone.utc)
        if not (now < start <= now + horizon):
            continue
        f = factors.get(ev.get("id"), {})
        win = [f.get(TEAM1_WIN_FACTOR, {}).get("v"), f.get(DRAW_FACTOR, {}).get("v"), f.get(TEAM2_WIN_FACTOR, {}).get("v")]
        if not all(win):
            continue
        m = FootballMatch(str(ev["id"]), a, b, start, segments[ev["sportId"]])
        m.options += [
            Option("1", f"П1 ({a})", float(win[0]), "1x2"),
            Option("X", "Ничья", float(win[1]), "1x2"),
            Option("2", f"П2 ({b})", float(win[2]), "1x2"),
        ]
        m.options += _totals(f) + _handicaps(f, a, b)
        matches.append(m)
    matches.sort(key=lambda m: (m.priority, m.start_utc))
    return matches[:limit] if limit else matches


def _totals(f: dict) -> list[Option]:
    out, seen = [], set()
    for over_id, under_id in TOTAL_LADDER_PAIRS:
        over, under = f.get(over_id), f.get(under_id)
        try:
            line = float(over["pt"]) if over and under and over.get("pt") == under.get("pt") else None
        except (TypeError, ValueError):
            line = None
        if line is None or line in seen or (line * 2) % 2 != 1 or not 1.5 <= line <= 4.5:
            continue
        seen.add(line)
        out.append(Option(f"TO{line:g}", f"Тотал больше {line:g}", float(over["v"]), "total_over", line))
        out.append(Option(f"TU{line:g}", f"Тотал меньше {line:g}", float(under["v"]), "total_under", line))
    return sorted(out, key=lambda o: (o.line, o.kind))


def _handicaps(f: dict, a: str, b: str) -> list[Option]:
    out, seen = [], set()
    for t1, t2 in HANDICAP_PAIRS:
        f1, f2 = f.get(t1), f.get(t2)
        try:
            h1, h2 = float(f1["pt"]), float(f2["pt"])
            o1, o2 = float(f1["v"]), float(f2["v"])
        except (TypeError, ValueError, KeyError):
            continue
        if h1 != -h2 or (abs(h1) * 2) % 2 != 1 or abs(h1) > 2.5 or h1 in seen:
            continue
        seen.add(h1)
        out.append(Option(f"H1{h1:+g}", f"Фора {a} ({h1:+g})", o1, "handicap1", h1))
        out.append(Option(f"H2{h2:+g}", f"Фора {b} ({h2:+g})", o2, "handicap2", h2))
    return out


_cache: dict[str, object] = {"at": 0.0, "matches": []}


async def get_full_line() -> list[FootballMatch]:
    """Every real football match in the line for the next LOOKUP_HORIZON (cached)."""
    if time.time() - _cache["at"] < CATALOG_TTL and _cache["matches"]:
        return _cache["matches"]  # type: ignore[return-value]
    async with httpx.AsyncClient(base_url=fonbet.BASE_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as c:
        resp = await c.get(fonbet.EVENTS_PATH, params={"lang": "ru", "scopeMarket": fonbet.SCOPE_MARKET})
        resp.raise_for_status()
        matches = parse_catalog(resp.json(), horizon=LOOKUP_HORIZON, limit=None)
    _cache.update(at=time.time(), matches=matches)
    return matches


async def get_catalog() -> list[FootballMatch]:
    """The browsable list: top leagues first, next HORIZON only, MAX_MATCHES at most."""
    soon = datetime.now(timezone.utc) + HORIZON
    return [m for m in await get_full_line() if m.start_utc <= soon][:MAX_MATCHES]


async def find_match(match_id: str) -> FootballMatch | None:
    return next((m for m in await get_full_line() if m.id == match_id), None)


def _name_score(query: str, team: str) -> float:
    from bot.core.reconcile import _similarity, normalize_team

    q, t = normalize_team(query), normalize_team(team)
    if not q or not t:
        return 0.0
    if q == t:
        return 1.0
    # "Реал" for "Реал Мадрид", "Спартак" for "Спартак Москва": a whole-word prefix/part
    if f" {q} " in f" {t} " or t.startswith(q):
        return 0.9
    return _similarity(q, t)


NAME_THRESHOLD = 0.7


def match_by_teams(matches: list[FootballMatch], team_a: str, team_b: str) -> FootballMatch | None:
    """Best line match for two user-typed (or screenshot-read) team names, either order."""
    best, best_score = None, NAME_THRESHOLD * 2
    for m in matches:
        straight = _name_score(team_a, m.team_a) + _name_score(team_b, m.team_b)
        crossed = _name_score(team_a, m.team_b) + _name_score(team_b, m.team_a)
        score = max(straight, crossed)
        if min(_name_score(team_a, m.team_a), _name_score(team_b, m.team_b)) < NAME_THRESHOLD and                 min(_name_score(team_a, m.team_b), _name_score(team_b, m.team_a)) < NAME_THRESHOLD:
            continue  # each team must match on its own, not just on the sum
        if score > best_score or (score == best_score and best and m.start_utc < best.start_utc):
            best, best_score = m, score
    return best


def settle(option: Option, goals_a: int, goals_b: int) -> str:
    """'win' | 'lose' for an option given the final score (half lines only -> no push)."""
    total, diff = goals_a + goals_b, goals_a - goals_b
    if option.kind == "1x2":
        won = {"1": diff > 0, "X": diff == 0, "2": diff < 0}[option.id]
    elif option.kind == "total_over":
        won = total > option.line
    elif option.kind == "total_under":
        won = total < option.line
    elif option.kind == "handicap1":
        won = diff + option.line > 0
    elif option.kind == "handicap2":
        won = -diff + option.line > 0
    else:
        raise ValueError(option.kind)
    return "win" if won else "lose"
