"""Settle AI picks from final scores, for the honest hit-rate shown to users.

Scores come from Betcity's open results feed (`ad.betcity.ru/d/score/sports`, the same
API host bot/providers/betcity.py reads; confirmed live 2026-09-25: name_ht/name_at +
sc_ev "2:1" per finished event). A pick is matched to a result by team-name similarity
(the same normaliser the arb matcher uses) and kick-off date; anything that can't be
matched confidently just stays unsettled rather than being guessed.
"""
from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from bot.analysis.catalog import Option, settle
from bot.core.reconcile import NAME_MATCH_THRESHOLD, _pair_similarity, normalize_team
from bot.db.repository import Repository

logger = logging.getLogger(__name__)

SCORE_URL = "https://ad.betcity.ru/d/score/sports"
SCORE_PARAMS = {"rev": 1, "ver": 1, "csn": "ooca9s"}
FOOTBALL = "1"
SETTLE_AFTER = timedelta(hours=2, minutes=30)
GIVE_UP_AFTER = timedelta(days=3)
CHECK_EVERY_S = 1800
MSK = timezone(timedelta(hours=3))


def parse_results(raw: dict) -> list[tuple[str, str, datetime, int, int]]:
    out = []
    sport = ((raw.get("reply") or {}).get("sports") or {}).get(FOOTBALL) or {}
    for champ in (sport.get("chmps") or {}).values():
        for ev in (champ.get("evts") or {}).values():
            m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(ev.get("sc_ev") or ""))
            if not m or not ev.get("name_ht") or not ev.get("name_at"):
                continue
            try:
                when = datetime.strptime(ev.get("date_ev", "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
            except ValueError:
                continue
            out.append((ev["name_ht"], ev["name_at"], when, int(m.group(1)), int(m.group(2))))
    return out


def find_score(team_a: str, team_b: str, start: datetime, results) -> tuple[int, int] | None:
    pair = (normalize_team(team_a), normalize_team(team_b))
    best, best_score = None, NAME_MATCH_THRESHOLD
    for home, away, when, ga, gb in results:
        if abs(when - start) > timedelta(hours=14):
            continue
        same = _pair_similarity(pair, (normalize_team(home), normalize_team(away)))
        if same >= best_score:
            # orientation: if the result lists the teams swapped, swap the score too
            straight = _pair_similarity((pair[0], ""), (normalize_team(home), ""))
            crossed = _pair_similarity((pair[0], ""), (normalize_team(away), ""))
            best, best_score = ((ga, gb) if straight >= crossed else (gb, ga)), same
    return best


async def settle_pending(repo: Repository, client: httpx.AsyncClient) -> int:
    now = datetime.now(timezone.utc)
    pending = repo.unsettled_ai_analyses((now - SETTLE_AFTER).isoformat())
    if not pending:
        return 0
    raw = (await client.get(SCORE_URL, params=SCORE_PARAMS)).json()
    results = parse_results(raw)
    settled = 0
    for row in pending:
        start = datetime.fromisoformat(row["start_utc"])
        score = find_score(row["team_a"], row["team_b"], start, results)
        if score is None:
            if now - start > GIVE_UP_AFTER:
                repo.settle_ai_analysis(row["match_id"], "unknown", "")
            continue
        option = Option(row["option_id"], row["option_label"], row["odds"], row["option_kind"], row["option_line"])
        repo.settle_ai_analysis(row["match_id"], settle(option, *score), f"{score[0]}:{score[1]}")
        settled += 1
    return settled


async def run_settler(repo: Repository) -> None:
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as client:
        while True:
            try:
                n = await settle_pending(repo, client)
                if n:
                    logger.info("AI picks settled: %s", n)
            except Exception:
                logger.exception("AI pick settlement failed")
            await asyncio.sleep(CHECK_EVERY_S)
