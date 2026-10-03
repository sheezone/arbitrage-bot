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
from datetime import datetime, timedelta, timezone

import httpx

from bot.analysis.catalog import get_full_line
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

SPORTSDB = "https://www.thesportsdb.com/api/v1/json/123/searchteams.php"
CREST_SPORTS = {"football": "Soccer", "hockey": "Ice Hockey", "basketball": "Basketball",
                "volleyball": "Volleyball", "esports": "Esports"}
BATCH = 40
RUN_EVERY_S = 1800
HORIZON = timedelta(days=2)

TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"teams": {"type": "array", "items": {
        "type": "object",
        "properties": {"ru": {"type": "string"}, "en": {"type": "string"}},
        "required": ["ru", "en"], "additionalProperties": False}}},
    "required": ["teams"], "additionalProperties": False,
}


async def _translate(analyzer, names: list[str], sport: str) -> dict[str, str]:
    result = await analyzer._json_call(
        "Переведи названия спортивных команд (вид спорта: " + sport + ") с русского на их "
        "официальное английское название, как на англоязычных сайтах (например «Бавария» → "
        "«Bayern Munich», «Ман Сити» → «Manchester City»). Без пояснений; если не знаешь — "
        "транслитерируй. Сохрани поле ru ровно как дано.",
        "\n".join(names), TRANSLATE_SCHEMA,
    )
    return {t["ru"]: t["en"] for t in (result or {}).get("teams", []) if t.get("en")}


async def _badge(client: httpx.AsyncClient, english: str, sport: str) -> str:
    try:
        resp = await client.get(SPORTSDB, params={"t": english})
        teams = (resp.json() or {}).get("teams") or []
    except (httpx.HTTPError, ValueError):
        return ""
    want = CREST_SPORTS[sport]
    for t in teams:
        if t.get("strSport") == want and t.get("strBadge"):
            return t["strBadge"] + "/tiny"  # 250px variant, plenty for a 22px crest
    return ""


async def fill_logos(analyzer, repo: Repository, client: httpx.AsyncClient) -> int:
    soon = datetime.now(timezone.utc) + HORIZON
    by_sport: dict[str, set[str]] = {}
    for m in await get_full_line():
        if m.sport in CREST_SPORTS and m.start_utc <= soon:
            by_sport.setdefault(m.sport, set()).update((m.team_a, m.team_b))
    found = 0
    for sport, names in by_sport.items():
        todo = sorted(n for n in names if repo.team_logo(n, sport) is None)
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            english = await _translate(analyzer, chunk, sport)
            for name in chunk:
                url = await _badge(client, english[name], sport) if name in english else ""
                repo.save_team_logo(name, sport, url)
                found += bool(url)
                await asyncio.sleep(2.1)  # free key: 30 requests/min
    return found


async def run_logo_filler(analyzer, repo: Repository) -> None:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": "Mozilla/5.0"}) as client:
        while True:
            try:
                n = await fill_logos(analyzer, repo, client)
                if n:
                    logger.info("Team crests found: %s", n)
            except Exception:
                logger.exception("Team crest lookup failed")
            await asyncio.sleep(RUN_EVERY_S)
