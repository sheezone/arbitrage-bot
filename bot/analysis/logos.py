"""Team crests for the Mini App.

Our line is in Russian ("Бавария", "Ман Сити"), while open crest databases are in
English, so: the AI translates new team names to their official English name in one
batched call, then TheSportsDB (free public key) gives the badge URL. Results -- found
or not -- are stored in the `team_logos` table, so each team is looked up once ever and
API requests never wait on it (unknown teams just show a monogram until the job runs).
Tennis/table tennis are players, not teams: no crests there (flags/monograms instead).
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from bot.analysis.catalog import get_full_line
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

SPORTSDB = "https://www.thesportsdb.com/api/v1/json/123/searchteams.php"
CREST_SPORTS = {"football": "Soccer", "hockey": "Ice Hockey", "basketball": "Basketball",
                "volleyball": "Volleyball", "esports": "Esports"}
BATCH = 40
RUN_EVERY_S = 600
HORIZON = timedelta(days=2)
API_FOOTBALL_PER_RUN = 8   # free plan: 100 requests/day, shared with the analyzer
# "Арка Гдыня U19", "Альбасете 2", "Спартак-2", "Реал Мадрид (жен)" -> the parent club's crest
SUFFIX = re.compile(r"(?:[\s-]+(?:U\d{2}|II|2|Б|мол|дубль|жен|w)\.?|\s*\([^)]*\))+$", re.IGNORECASE)

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"teams": {"type": "array", "items": {
        "type": "object",
        "properties": {"ru": {"type": "string"}, "en": {"type": "array", "items": {"type": "string"}}},
        "required": ["ru", "en"], "additionalProperties": False}}},
    "required": ["teams"], "additionalProperties": False,
}


def base_name(name: str) -> str:
    return SUFFIX.sub("", name).strip() or name


async def _translate(analyzer, names: list[str], sport: str) -> dict[str, list[str]]:
    result = await analyzer._json_call(
        "Переведи названия спортивных команд (вид спорта: " + sport + ") с русского на английский. "
        "Для каждой дай до 3 вариантов, как команду называют на англоязычных сайтах: полное "
        "официальное название, короткое и местное написание (например «Бавария» → «Bayern Munich», "
        "«FC Bayern München», «Bayern»; «Атлетико Минейро» → «Atletico Mineiro», «Atlético Mineiro»). "
        "Для киберспорта — название организации (Team Spirit, NAVI). Без пояснений. "
        "Сохрани поле ru ровно как дано.",
        "\n".join(names), TRANSLATE_SCHEMA,
    )
    return {t["ru"]: [e for e in t["en"] if e][:3] for t in (result or {}).get("teams", [])}


async def _sportsdb(client: httpx.AsyncClient, english: str, sport: str) -> str:
    try:
        resp = await client.get(SPORTSDB, params={"t": english})
        teams = (resp.json() or {}).get("teams") or []
    except (httpx.HTTPError, ValueError):
        return ""
    finally:
        await asyncio.sleep(2.1)  # free key: 30 requests/min
    want = CREST_SPORTS[sport]
    for t in teams:
        if t.get("strSport") == want and t.get("strBadge"):
            return t["strBadge"] + "/tiny"  # 250px variant, plenty for a 22px crest
    return ""


async def fill_logos(analyzer, repo: Repository, client: httpx.AsyncClient, api_football_key: str = "") -> int:
    from bot.webapp.football_stats import search_team_logo

    now = datetime.now(timezone.utc)
    matches = [m for m in await get_full_line() if m.sport in CREST_SPORTS and m.start_utc <= now + HORIZON]
    # popular matches first: these are the ones people actually open
    matches.sort(key=lambda m: (m.priority, -len(m.options), m.start_utc))
    todo: dict[str, list[str]] = {}
    for m in matches:
        for name in (m.team_a, m.team_b):
            if repo.team_logo(name, m.sport) is None and name not in todo.get(m.sport, []):
                todo.setdefault(m.sport, []).append(name)
    found, af_left = 0, API_FOOTBALL_PER_RUN if api_football_key else 0
    for sport, names in todo.items():
        for i in range(0, len(names), BATCH):
            chunk = names[i:i + BATCH]
            # youth/reserve sides share the parent club's crest
            pending = []
            for name in chunk:
                parent = base_name(name)
                if parent != name and repo.team_logo(parent, sport):
                    repo.save_team_logo(name, sport, repo.team_logo(parent, sport))
                    found += 1
                else:
                    pending.append(name)
            if not pending:
                continue
            english = await _translate(analyzer, [base_name(n) for n in pending], sport)
            for name in pending:
                url = ""
                for variant in english.get(base_name(name), []):
                    url = await _sportsdb(client, variant, sport)
                    if url:
                        break
                if not url and sport == "football" and af_left > 0 and english.get(base_name(name)):
                    af_left -= 1
                    url = await search_team_logo(client, english[base_name(name)][0], api_football_key) or ""
                repo.save_team_logo(name, sport, url)
                if base_name(name) != name and url:
                    repo.save_team_logo(base_name(name), sport, url)
                found += bool(url)
    return found


async def run_logo_filler(analyzer, repo: Repository, api_football_key: str = "") -> None:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        while True:
            try:
                n = await fill_logos(analyzer, repo, client, api_football_key)
                if n:
                    logger.info("Team crests found: %s", n)
            except Exception:
                logger.exception("Team crest lookup failed")
            await asyncio.sleep(RUN_EVERY_S)
