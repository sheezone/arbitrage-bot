"""NHL pre-game facts from the league's own open API (api-web.nhle.com, no key; checked
live 2026-10-23) for the hockey analysis (owner: "как добавить больше точности в хоккее"):

- goalies: each team's goalies this season (games, record, save %, GAA) -- the single
  biggest factor in a hockey game;
- standings: points, goal difference, last-10, home/road records, streak;
- fatigue: whether a team played the previous day (back-to-back).

MoneyPuck (xG / Corsi) is behind Cloudflare bot protection, so it isn't used. Any
failure just returns "" and the analysis runs on the rest.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import httpx

API = "https://api-web.nhle.com/v1"

# Fonbet's Russian names -> NHL abbreviations (matched by "contains", city or nickname).
RU_TO_ABBR = {
    "Анахайм": "ANA", "Бостон": "BOS", "Баффало": "BUF", "Калгари": "CGY", "Каролина": "CAR",
    "Чикаго": "CHI", "Колорадо": "COL", "Коламбус": "CBJ", "Даллас": "DAL", "Детройт": "DET",
    "Эдмонтон": "EDM", "Флорида": "FLA", "Лос-Анджелес": "LAK", "Миннесота": "MIN",
    "Монреаль": "MTL", "Нэшвилл": "NSH", "Нью-Джерси": "NJD", "Айлендерс": "NYI",
    "Рейнджерс": "NYR", "Оттава": "OTT", "Филадельфия": "PHI", "Питтсбург": "PIT",
    "Сан-Хосе": "SJS", "Сиэтл": "SEA", "Сент-Луис": "STL", "Тампа": "TBL", "Торонто": "TOR",
    "Юта": "UTA", "Ванкувер": "VAN", "Вегас": "VGK", "Вашингтон": "WSH", "Виннипег": "WPG",
}


def abbr(ru_name: str) -> str | None:
    return next((a for k, a in RU_TO_ABBR.items() if k.lower() in ru_name.lower()), None)


async def _get(client: httpx.AsyncClient, path: str) -> dict:
    r = await client.get(API + path, timeout=15, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def _goalies(side: dict, name: str) -> list[str]:
    out = []
    t = side.get("teamTotals") or {}
    if t:
        out.append(f"{name}: вратари в сезоне — {t.get('record')}, % отражённых {t.get('savePctg', 0):.3f}, "
                   f"КН {t.get('gaa', 0):.2f}")
    for g in (side.get("leaders") or [])[:3]:
        n = (g.get("name") or {}).get("default", "?")
        out.append(f"  - {n}: {g.get('gamesPlayed')} игр, {g.get('record')}, "
                   f"% отражённых {g.get('savePctg', 0):.3f}, КН {g.get('gaa', 0):.2f}")
    return out


async def match_nhl(client: httpx.AsyncClient, team_a: str, team_b: str, start_utc: datetime) -> str:
    """Ready-to-paste Russian text block, or "" if the teams/game aren't found."""
    a, b = abbr(team_a), abbr(team_b)
    if not a or not b:
        return ""
    try:
        game = None
        for day in {start_utc.date(), (start_utc - timedelta(hours=12)).date()}:
            sched = await _get(client, f"/schedule/{day.isoformat()}")
            for wk in sched.get("gameWeek", []):
                for g in wk.get("games", []):
                    if {g["homeTeam"]["abbrev"], g["awayTeam"]["abbrev"]} == {a, b}:
                        game = g
            if game:
                break
        lines = [f"Данные НХЛ (официальный API) — {team_a} ({a}) против {team_b} ({b}):"]
        if game:
            land = await _get(client, f"/gamecenter/{game['id']}/landing")
            gc = (land.get("matchup") or {}).get("goalieComparison") or {}
            home_is_a = game["homeTeam"]["abbrev"] == a
            sides = ((gc.get("homeTeam"), team_a if home_is_a else team_b),
                     (gc.get("awayTeam"), team_b if home_is_a else team_a))
            lines.append("Вратари (стартовый объявляется в день игры — сверь с новостями):")
            for side, name in sides:
                if side:
                    lines += _goalies(side, name)
        stand = await _get(client, "/standings/now")
        for team, code in ((team_a, a), (team_b, b)):
            s = next((x for x in stand.get("standings", []) if (x.get("teamAbbrev") or {}).get("default") == code), None)
            if s:
                lines.append(
                    f"{team}: {s.get('points')} очков за {s.get('gamesPlayed')} игр, шайбы {s.get('goalFor')}-"
                    f"{s.get('goalAgainst')}, последние 10: {s.get('l10Wins')}-{s.get('l10Losses')}-{s.get('l10OtLosses')}, "
                    f"дома {s.get('homeWins')}-{s.get('homeLosses')}-{s.get('homeOtLosses')}, "
                    f"в гостях {s.get('roadWins')}-{s.get('roadLosses')}-{s.get('roadOtLosses')}, "
                    f"серия {s.get('streakCode', '')}{s.get('streakCount', '')}")
            try:
                sch = await _get(client, f"/club-schedule-season/{code}/now")
                prev = [g for g in sch.get("games", [])
                        if g.get("gameState") in ("OFF", "FINAL")
                        and datetime.fromisoformat(g["startTimeUTC"].replace("Z", "+00:00")) < start_utc]
                if prev:
                    last = datetime.fromisoformat(prev[-1]["startTimeUTC"].replace("Z", "+00:00"))
                    hours = (start_utc - last).total_seconds() / 3600
                    tag = " — ВТОРОЙ МАТЧ ПОДРЯД (back-to-back), усталость" if hours < 30 else ""
                    lines.append(f"{team}: прошлый матч {hours / 24:.1f} дн. назад{tag}")
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        return "\n".join(lines) if len(lines) > 1 else ""
    except (httpx.HTTPError, ValueError, KeyError):
        return ""
