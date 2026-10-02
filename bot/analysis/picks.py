"""Ready-made picks and expresses, built only from real AI analyses (bot/analysis/ai.py).

A background job analyses the day's top matches on its own so the "Готовые прогнозы"
feed and the expresses fill up without anyone tapping a match first. Expresses combine
2-4 of those picks (different matches, confidence medium/high) into total odds 2-6; the
express is exactly as good as its legs -- no extra "AI selection" claim beyond that.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from itertools import combinations

from bot.analysis.ai import Analyzer
from bot.analysis.catalog import get_catalog
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
DAILY_TARGET = 14         # analyses wanted for matches in the next 24h (owner: more express candidates)
PER_RUN = 4               # model calls per job run (spreads cost/latency over the day)
RUN_EVERY_S = 2 * 3600
ACTIVE_HOURS = range(8, 23)
EXPRESS_LEG_ODDS = (1.3, 2.4)
EXPRESS_TOTAL = (2.0, 6.0)
EXPRESS_COUNT = 3


def pick_from_row(row) -> dict:
    payload = json.loads(row["payload"])
    return {
        "match_id": row["match_id"],
        "team_a": row["team_a"],
        "team_b": row["team_b"],
        "league": row["league"],
        "start_utc": row["start_utc"],
        "label": row["option_label"],
        "odds": row["odds"],
        "confidence": row["confidence"],
        "summary": payload.get("summary", ""),
        "reasoning": payload.get("reasoning", ""),
        "result": row["result"],
        "score": row["score"],
    }


def upcoming_picks(repo: Repository, now: datetime | None = None, hours: int = 48) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = repo.ai_analyses_between(now.isoformat(), (now + timedelta(hours=hours)).isoformat())
    return [pick_from_row(r) for r in rows]


def build_expresses(picks: list[dict]) -> list[dict]:
    legs = [p for p in picks if p["confidence"] in ("средняя", "высокая")
            and EXPRESS_LEG_ODDS[0] <= p["odds"] <= EXPRESS_LEG_ODDS[1]]
    legs.sort(key=lambda p: (p["confidence"] != "высокая", p["odds"]))
    out: list[dict] = []
    used: set[str] = set()
    for size in (3, 2, 4):
        for combo in combinations(legs, size):
            ids = {p["match_id"] for p in combo}
            if ids & used or len(ids) < size:
                continue
            total = 1.0
            for p in combo:
                total *= p["odds"]
            if EXPRESS_TOTAL[0] <= total <= EXPRESS_TOTAL[1]:
                out.append({"legs": list(combo), "total_odds": round(total, 2)})
                used |= ids
            if len(out) >= EXPRESS_COUNT:
                return out
    return out


async def run_daily_picks(analyzer: Analyzer, repo: Repository) -> None:
    while True:
        try:
            now = datetime.now(timezone.utc)
            if now.astimezone(MSK).hour in ACTIVE_HOURS:
                have = {p["match_id"] for p in upcoming_picks(repo, now, hours=24)}
                if len(have) < DAILY_TARGET:
                    soon = [m for m in await get_catalog() if m.start_utc <= now + timedelta(hours=24) and m.id not in have]
                    for match in soon[:min(PER_RUN, DAILY_TARGET - len(have))]:
                        await analyzer.analyze(match)
        except Exception:
            logger.exception("Daily AI picks run failed")
        await asyncio.sleep(RUN_EVERY_S)
