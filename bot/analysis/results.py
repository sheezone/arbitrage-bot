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
                 "table_tennis": "46", "esports": "73", "hockey": "7"}
# Hockey markets (1X2, totals) are regular time only, while sc_ev includes OT/shootout --
# so for hockey the score is the sum of the first three periods from sc_ext_ev
# ("2:0, 0:1, 2:1[, 1:0 OT]"); confirmed in the feed 2026-10-17.
REGULATION_PERIODS = {"7": 3}
SETTLE_AFTER = timedelta(hours=2, minutes=30)
GIVE_UP_AFTER = timedelta(days=3)
CHECK_EVERY_S = 1800
MSK = timezone(timedelta(hours=3))


def parse_results(raw: dict, sport_id: str = FOOTBALL, regulation: bool = True) -> list[tuple[str, str, datetime, int, int]]:
    out = []
    sport = ((raw.get("reply") or {}).get("sports") or {}).get(sport_id) or {}
    for champ in (sport.get("chmps") or {}).values():
        for ev in (champ.get("evts") or {}).values():
            m = re.fullmatch(r"\s*(\d+)\s*:\s*(\d+)\s*", str(ev.get("sc_ev") or ""))
            if not m or not ev.get("name_ht") or not ev.get("name_at"):
                continue
            if "(" in ev["name_ht"] or "(" in ev["name_at"]:
                continue  # stat pseudo-events: "Авангард (бр)", "(штр)"...
            ht, at = ev["name_ht"].split(), ev["name_at"].split()
            # lowercase shared tail (stat words) or an all-caps shared head (УГЛ/ЖК) --
            # never a capitalised shared word, so "Реал Мадрид — Реал Сосьедад" survives
            if len(ht) > 1 and len(at) > 1 and ((ht[-1] == at[-1] and ht[-1].islower())
                                                or (ht[0] == at[0] and ht[0].isupper() and len(ht[0]) <= 4)):
                # football stat rows share a suffix/prefix: "Ливерпуль удары в створ" /
                # "Вильярреал удары в створ", "УГЛ ПСЖ" / "УГЛ Барселона" (2026-10-21)
                continue
            periods = REGULATION_PERIODS.get(sport_id) if regulation else None
            if periods:
                parts = re.findall(r"(\d+)\s*:\s*(\d+)", str(ev.get("sc_ext_ev") or ""))
                if len(parts) < periods:
                    continue
                ga = sum(int(a) for a, _ in parts[:periods])
                gb = sum(int(b) for _, b in parts[:periods])
            else:
                ga, gb = int(m.group(1)), int(m.group(2))
            try:
                when = datetime.strptime(ev.get("date_ev", "")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=MSK)
            except ValueError:
                continue
            out.append((ev["name_ht"], ev["name_at"], when, ga, gb))
    return out


def _prefix(ours: str, theirs: str) -> bool:
    """Our name equals the leading word(s) of theirs ("авангард" vs "авангард омск")."""
    o, t = ours.split(), theirs.split()
    return bool(o) and len(o) < len(t) and t[:len(o)] == o and len(ours) >= 4


def find_score(team_a: str, team_b: str, start: datetime, results) -> tuple[int, int] | None:
    pair = (normalize_team(team_a), normalize_team(team_b))
    best, best_score = None, NAME_MATCH_THRESHOLD
    for home, away, when, ga, gb in results:
        if abs(when - start) > timedelta(hours=14):
            continue
        h, a = normalize_team(home), normalize_team(away)
        # Results often add the city: "Авангард" vs "Авангард Омск" (hockey, 2026-10-17)
        if _prefix(pair[0], h) and _prefix(pair[1], a):
            return ga, gb
        if _prefix(pair[0], a) and _prefix(pair[1], h):
            return gb, ga
        same = _pair_similarity(pair, (h, a))
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
                # No final score ever arrived (no results feed for this sport/tournament):
                # the outcome is unknown, so the stake is returned -- NOT counted as a loss
                # (owner 2026-10-11: that was 100 of 119 "losses" and the whole -28k).
                repo.settle_ai_bet(bet["match_id"], "void", bet["stake"])
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
        fin = row["final_score"] if "final_score" in row.keys() else None
        res = winner_result(row["payload"], fin or row["score"])
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
        final = None
        if sid in REGULATION_PERIODS and score is not None:
            # hockey: the winner stat counts who won the GAME (OT/shootout included,
            # owner 2026-10-22); bets keep settling on regular time like the bookmaker
            key = sport + ":final"
            if key not in by_sport:
                by_sport[key] = [r for raw in raws for r in parse_results(raw, sid, regulation=False)]
            fs = find_score(row["team_a"], row["team_b"], start, by_sport[key])
            final = f"{fs[0]}:{fs[1]}" if fs else None
        if score is None:
            if now - start > GIVE_UP_AFTER:
                repo.settle_ai_analysis(row["match_id"], "unknown", "")
            continue
        option = Option(row["option_id"], row["option_label"], row["odds"], row["option_kind"], row["option_line"])
        repo.settle_ai_analysis(row["match_id"], settle(option, *score), f"{score[0]}:{score[1]}", final)
        settled += 1
    return settled



async def report_losses(repo: Repository, bot, admin_chat_ids) -> int:
    """Every lost pick that counts in the public stats goes to the admins' DMs with the
    AI's own reasoning and the risks it named (owner 2026-10-21). The loss STAYS in the
    statistics -- this is a report, not a filter. Each match is reported once."""
    import html

    if bot is None or not admin_chat_ids:
        return 0
    since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    sent = 0
    for row in repo.day_results(since, datetime.now(timezone.utc).isoformat()):
        if row["stat_hit"] != "lose" or repo.alert_sent(f"loss_{row['match_id']}"):
            continue
        repo.mark_alert_sent(f"loss_{row['match_id']}")
        try:
            p = json.loads(row["payload"])
        except ValueError:
            p = {}
        pr = p.get("probabilities") or {}
        text = (
            f"❌ <b>Прогноз не зашёл</b>\n\n"
            f"<b>{html.escape(row['team_a'])} — {html.escape(row['team_b'])}</b>, счёт <b>{html.escape(row['score'] or '?')}</b>\n"
            f"{html.escape(row['league'] or '')}\n\n"
            f"Ставка ИИ: <b>{html.escape(row['option_label'] or '')}</b> @ {row['odds']:.2f} · уверенность {html.escape(row['confidence'] or '')}\n"
            f"Шансы по ИИ: П1 {pr.get('p1')}% · Х {pr.get('x')}% · П2 {pr.get('p2')}%"
            + (" · финальный разбор" if p.get("final") else "") + "\n\n"
            f"<b>Почему ставил:</b> {html.escape((p.get('reasoning') or '')[:600])}\n\n"
            f"<b>Риски, которые ИИ называл:</b> {html.escape((p.get('risks') or '')[:400])}"
        )
        for admin in admin_chat_ids:
            try:
                await bot.send_message(admin, text, parse_mode="HTML", disable_notification=True)
            except Exception:
                logger.warning("loss report to %s failed", admin)
        sent += 1
    return sent

async def run_settler(repo: Repository, bot=None, admin_chat_ids=frozenset()) -> None:
    async with httpx.AsyncClient(timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as client:
        while True:
            try:
                n = await settle_pending(repo, client)
                score_winners(repo)
                await settle_ai_bets(repo)
                await report_losses(repo, bot, admin_chat_ids)
                if n:
                    logger.info("AI picks settled: %s", n)
            except Exception:
                logger.exception("AI pick settlement failed")
            await asyncio.sleep(CHECK_EVERY_S)
