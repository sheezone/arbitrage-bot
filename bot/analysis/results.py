"""Settle AI picks from final scores, for the honest hit-rate shown to users.

Scores come from Betcity's open results feed (`ad.betcity.ru/d/score/sports`, the same
API host bot/providers/betcity.py reads; confirmed live 2026-09-25: name_ht/name_at +
sc_ev "2:1" per finished event). A pick is matched to a result by team-name similarity
(the same normaliser the arb matcher uses) and kick-off date; anything that can't be
matched confidently just stays unsettled rather than being guessed.
"""
from __future__ import annotations

import asyncio
import json
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
BETCITY_SPORT = {"football": "1", "basketball": "3", "tennis": "2", "volleyball": "12",
                 "table_tennis": "46", "esports": "73"}  # hockey deliberately absent
SETTLE_AFTER = timedelta(hours=2, minutes=30)
GIVE_UP_AFTER = timedelta(days=3)
CHECK_EVERY_S = 1800
MSK = timezone(timedelta(hours=3))


def parse_results(raw: dict, sport_id: str = FOOTBALL) -> list[tuple[str, str, datetime, int, int]]:
    out = []
    sport = ((raw.get("reply") or {}).get("sports") or {}).get(sport_id) or {}
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


def predicted_outcome(payload: str) -> str | None:
    """'1' / 'X' / '2': the outcome the AI rated most likely in its own probabilities."""
    try:
        p = json.loads(payload).get("probabilities") or {}
    except (ValueError, AttributeError):
        return None
    ranked = sorted((("1", p.get("p1") or 0), ("X", p.get("x") or 0), ("2", p.get("p2") or 0)), key=lambda t: -t[1])
    return ranked[0][0] if ranked[0][1] > 0 else None


def winner_result(payload: str, score: str) -> str | None:
    m = re.fullmatch(r"(\d+):(\d+)", score or "")
    pred = predicted_outcome(payload)
    if not m or pred is None:
        return None
    a, b = int(m.group(1)), int(m.group(2))
    actual = "1" if a > b else "2" if b > a else "X"
    return "win" if actual == pred else "lose"


async def settle_ai_bets(repo: Repository) -> int:
    """Settles the AI's own virtual bets once their match has a final score -- same
    GIVE_UP_AFTER window as the public hit-rate settlement, reusing whatever
    ai_analyses.result already worked out for that match_id (win/lose/unknown)."""
    now = datetime.now(timezone.utc)
    n = 0
    for bet in repo.pending_ai_bets((now - SETTLE_AFTER).isoformat()):
        row = repo.get_ai_analysis(bet["match_id"])
        if row is None or row["result"] not in ("win", "lose"):
            if now - datetime.fromisoformat(bet["start_utc"]) > GIVE_UP_AFTER:
                repo.settle_ai_bet(bet["match_id"], "lose", 0.0)  # abandoned stake, not a win
                n += 1
            continue
        payout = bet["stake"] * bet["odds"] if row["result"] == "win" else 0.0
        repo.settle_ai_bet(bet["match_id"], row["result"], payout)
        n += 1
    return n


def score_winners(repo: Repository) -> int:
    """Record whether the predicted winner won, for every settled match not yet scored."""
    n = 0
    for row in repo.ai_winner_unscored():
        res = winner_result(row["payload"], row["score"])
        repo.set_ai_winner_result(row["match_id"], res or "unknown")
        n += res is not None
    return n


async def settle_pending(repo: Repository, client: httpx.AsyncClient) -> int:
    now = datetime.now(timezone.utc)
    pending = repo.unsettled_ai_analyses((now - SETTLE_AFTER).isoformat())
    if not pending:
        return 0
    # the feed returns one MSK day per request (`date=YYYY-MM-DD`, default today)
    days = {datetime.fromisoformat(r["start_utc"]).astimezone(MSK).date() for r in pending}
    days.add(now.astimezone(MSK).date())
    raws = []
    for day in sorted(days):
        raws.append((await client.get(SCORE_URL, params={**SCORE_PARAMS, "date": day.isoformat()})).json())
    by_sport: dict[str, list] = {}
    settled = 0
    for row in pending:
        start = datetime.fromisoformat(row["start_utc"])
        sport = row["sport"] if "sport" in row.keys() else "football"
        sid = BETCITY_SPORT.get(sport)
        if sid is None:  # e.g. hockey: regular-time markets vs a final score incl. OT
            if now - start > GIVE_UP_AFTER:
                repo.settle_ai_analysis(row["match_id"], "unknown", "")
            continue
        if sport not in by_sport:
            by_sport[sport] = [r for raw in raws for r in parse_results(raw, sid)]
        score = find_score(row["team_a"], row["team_b"], start, by_sport[sport])
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
                score_winners(repo)
                await settle_ai_bets(repo)
                if n:
                    logger.info("AI picks settled: %s", n)
            except Exception:
                logger.exception("AI pick settlement failed")
            await asyncio.sleep(CHECK_EVERY_S)
