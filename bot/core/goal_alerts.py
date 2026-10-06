"""«ГООООЛ!» posts in the news channel for goals in top football matches (owner
2026-10-07: "когда гол от топовых матчей пиши типа как в пабликах гооооол!").

Live scores come from the same Fonbet line dump the AI catalog reads (events with
place == "live" + eventMiscs score1/score2). Only leagues in catalog.TOP_LEAGUES count.
The first time a match is seen its score is just recorded (no post), so a restart or a
match joining mid-game never floods the channel with old goals; after that every score
increase is one post. Last seen scores live in the goal_alerts table.
"""
from __future__ import annotations

import asyncio
import html
import logging
import random
import time
from datetime import datetime, timedelta, timezone

import httpx
from aiogram import Bot

from bot.analysis.catalog import PARENT_TO_SPORT, SKIP_SEGMENT_WORDS, TOP_LEAGUES
from bot.db.repository import Repository
from bot.providers import fonbet

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))
CHECK_EVERY_S = 45
SHOUTS = ("ГООООООЛ!", "ГОООЛ!!!", "ГОЛ-ГОЛ-ГОЛ!", "ГООООЛ! 🔥", "ЕСТЬ ГОЛ!")


def live_top_matches(raw: dict) -> list[dict]:
    segments = {
        s["id"]: s.get("name") or ""
        for s in raw.get("sports", [])
        if s.get("kind") == "segment" and PARENT_TO_SPORT.get(s.get("parentId")) == "football"
        and not any(w in (s.get("name") or "") for w in SKIP_SEGMENT_WORDS)
    }
    misc = {m["id"]: m for m in raw.get("eventMiscs", [])}
    out = []
    for ev in raw.get("events", []):
        if ev.get("place") != "live" or ev.get("level", 1) != 1 or ev.get("sportId") not in segments:
            continue
        league = segments[ev["sportId"]]
        if not any(league.startswith(p) for p in TOP_LEAGUES):
            continue
        a, b, m = ev.get("team1"), ev.get("team2"), misc.get(ev.get("id"))
        if not a or not b or m is None or "score1" not in m:
            continue
        minute = None
        if m.get("timerDirection") == 1 and m.get("timerUpdateTimestampMsec"):
            secs = m.get("timerSeconds", 0) + (time.time() * 1000 - m["timerUpdateTimestampMsec"]) / 1000
            minute = int(secs // 60) + 1
        out.append({"id": str(ev["id"]), "team_a": a, "team_b": b, "league": league,
                    "s1": int(m["score1"]), "s2": int(m["score2"]), "minute": minute})
    return out


def goal_text(m: dict, scorer: str, channel_username: str) -> str:
    a, b = html.escape(m["team_a"]), html.escape(m["team_b"])
    if scorer == "a":
        a = f"<b>{a}</b>"
    else:
        b = f"<b>{b}</b>"
    minute = f" · {m['minute']}'" if m["minute"] and 0 < m["minute"] <= 130 else ""
    lines = [f"⚽️ <b>{random.choice(SHOUTS)}</b>", "",
             f"{a} <b>{m['s1']}:{m['s2']}</b> {b}",
             f"🏆 {html.escape(m['league'])}{minute}"]
    if channel_username:
        lines += ["", f"📡 @{channel_username}"]
    return "\n".join(lines)


async def run_goal_alerts(bot: Bot, repo: Repository, chat_id: int, channel_username: str = "") -> None:
    async with httpx.AsyncClient(base_url=fonbet.BASE_URL, timeout=60, headers={"User-Agent": "Mozilla/5.0"}) as c:
        while True:
            try:
                resp = await c.get(fonbet.EVENTS_PATH, params={"lang": "ru", "scopeMarket": fonbet.SCOPE_MARKET})
                resp.raise_for_status()
                for m in live_top_matches(resp.json()):
                    prev = repo.goal_score(m["id"])
                    repo.set_goal_score(m["id"], m["s1"], m["s2"])
                    if prev is None:
                        continue  # first sighting: record only
                    p1, p2 = prev
                    for scorer, gained in (("a", m["s1"] - p1), ("b", m["s2"] - p2)):
                        if gained <= 0 or gained > 2:  # >2 at once = data jump, not a goal
                            continue
                        try:
                            await bot.send_message(chat_id, goal_text(m, scorer, channel_username), parse_mode="HTML",
                                                   disable_web_page_preview=True)
                        except Exception:
                            logger.exception("Goal post failed for %s", m["id"])
            except Exception:
                logger.exception("Goal alerts check failed")
            await asyncio.sleep(CHECK_EVERY_S)
