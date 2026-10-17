"""Pre-kickoff re-analysis and weekly lessons (owner 2026-10-15).

1. Final pass: ~60 minutes before kick-off (owner: "не 90 минут а 60"), every already-analysed top match (popular
   = the elite tournaments that feed the stats) is analysed again from scratch --
   confirmed lineups, last-minute injuries and line moves are only known by then.
   The fresh verdict replaces the early one (payload "final": true), so stats and the
   AI bankroll judge the final pick.
2. Lessons: once a week the AI reviews its own missed picks of the last 2 weeks and
   writes short, concrete rules; they're fed into every analysis prompt (see _ask).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from bot.analysis.ai import Analyzer
from bot.analysis.catalog import get_full_line
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

FINAL_WINDOW = (timedelta(minutes=25), timedelta(minutes=70))  # wide: room for retries
FINAL_CHECK_EVERY = 10 * 60
FINAL_PER_RUN = 10
LESSONS_EVERY = timedelta(days=7)
LESSONS_LOOKBACK = timedelta(days=14)

LESSONS_SYSTEM = (
    "Ты спортивный аналитик и разбираешь СВОИ незашедшие прогнозы. По списку ниже найди "
    "повторяющиеся ошибки: в каких видах спорта/лигах, на каких коэффициентах, на каких типах "
    "ставок (победа, тотал, фора), где переоценивал фаворитов или уверенность. Сформулируй 3-7 "
    "коротких конкретных правил на будущее (каждое 1 предложение, с цифрами, если видно). Не "
    "выдумывай закономерностей, которых нет в данных; если промахов мало — так и напиши одним правилом."
)
LESSONS_SCHEMA = {
    "type": "object",
    "properties": {"rules": {"type": "array", "items": {"type": "string"}}},
    "required": ["rules"], "additionalProperties": False,
}


async def _final_once(analyzer: Analyzer, repo: Repository) -> int:
    now = datetime.now(timezone.utc)
    lo, hi = now + FINAL_WINDOW[0], now + FINAL_WINDOW[1]
    due = []
    for m in await get_full_line():
        if not (lo <= m.start_utc <= hi) or not m.is_popular or not m.options:
            continue
        row = repo.get_ai_analysis(m.id)
        # every top match gets one, analysed earlier or not (owner 2026-10-17: "по хоккею не было")
        if row is not None and json.loads(row["payload"]).get("final"):
            continue
        due.append(m)
    n = 0
    for m in sorted(due, key=lambda m: m.start_utc)[:FINAL_PER_RUN]:
        try:
            if await analyzer.analyze(m, final=True):
                n += 1
        except Exception:
            logger.exception("final pass failed for %s", m.id)
    return n


async def _lessons_once(analyzer: Analyzer, repo: Repository) -> None:
    since = (datetime.now(timezone.utc) - LESSONS_LOOKBACK).isoformat()
    misses = repo.ai_recent_misses(since)
    if len(misses) < 3:
        return
    lines = []
    for r in misses:
        try:
            p = json.loads(r["payload"])
        except ValueError:
            continue
        pr = p.get("probabilities") or {}
        lines.append(
            f"- {r['sport']} | {r['league']} | {r['team_a']} — {r['team_b']} | счёт {r['score']} | "
            f"ставка {r['option_label']} @ {r['odds']} ({r['result']}) | уверенность {r['confidence']} | "
            f"вероятности П1 {pr.get('p1')} Х {pr.get('x')} П2 {pr.get('p2')} | победитель: {r['winner_result']}"
        )
    out = await analyzer._json_call(LESSONS_SYSTEM, "Незашедшие прогнозы:\n" + "\n".join(lines),
                                    LESSONS_SCHEMA, effort="high")
    rules = [r.strip() for r in (out or {}).get("rules", []) if r.strip()]
    if rules:
        repo.set_ai_note("lessons", "\n".join(f"- {r}" for r in rules[:7]))
        logger.info("AI lessons updated: %d rules", len(rules))


async def run_final_pass(analyzer: Analyzer, repo: Repository) -> None:
    last_lessons = datetime.min.replace(tzinfo=timezone.utc)
    while True:
        try:
            n = await _final_once(analyzer, repo)
            if n:
                logger.info("final pre-kickoff analyses: %d", n)
        except Exception:
            logger.exception("final pass loop failed")
        now = datetime.now(timezone.utc)
        if now - last_lessons >= LESSONS_EVERY:
            try:
                await _lessons_once(analyzer, repo)
            except Exception:
                logger.exception("lessons pass failed")
            last_lessons = now
        await asyncio.sleep(FINAL_CHECK_EVERY)
