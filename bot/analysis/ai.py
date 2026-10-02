"""Claude-written analysis of one football match.

Gathers what real data there is (bookmaker odds for every option, recent form and table
position, head-to-head, last-24h news headlines), hands it to Claude, and gets back a
structured analysis whose pick must be one of the match's priced options (by id) -- so
the shown odds always exist, and the pick can be settled from the final score later
(bot/analysis/results.py) for an honest, real hit-rate.

One analysis per match, cached in ai_analyses and shared by every user: the same match
always gets the same verdict (no re-rolling until it says what someone wants), and a
popular match costs one model call, not one per user.

Policy (owner agreed 2026-10-02): no invented accuracy claims, no "guaranteed"/"sure"
language, confidence is a qualitative low/medium/high with the reasons spelled out.
"""
from __future__ import annotations

import asyncio
import json
import logging
import anthropic
import httpx

from bot.analysis.catalog import FootballMatch
from bot.db.repository import Repository
from bot.webapp.football_data import get_team_form as fd_team_form
from bot.webapp.football_stats import get_match_h2h
from bot.webapp.news import fetch_team_news
from bot.webapp.team_form import get_team_form as espn_team_form

logger = logging.getLogger(__name__)

CONFIDENCE = ("низкая", "средняя", "высокая")

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
                "required": ["title", "text"],
                "additionalProperties": False,
            },
        },
        "option_id": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "reasoning": {"type": "string"},
        "risks": {"type": "string"},
    },
    "required": ["summary", "factors", "option_id", "confidence", "reasoning", "risks"],
    "additionalProperties": False,
}

SYSTEM = """Ты — футбольный аналитик. По присланным данным разбери матч и выбери ОДНУ
ставку из списка доступных вариантов (верни её id в option_id).

Правила:
- Опирайся только на присланные данные: коэффициенты, форму, таблицу, личные встречи,
  новости. Ничего не выдумывай — ни травм, ни составов, ни цифр. Если каких-то данных
  нет, так и учитывай, и не делай вид, что знаешь больше.
- Коэффициенты — это мнение рынка с маржой букмекера. Ищи вариант, где, по данным,
  шанс выше, чем закладывает коэффициент (ценность), а не просто самый вероятный исход.
  Слишком низкие коэффициенты (< 1.30) не выбирай.
- confidence: «низкая» / «средняя» / «высокая» — честно. «Высокая» только когда
  несколько независимых факторов сходятся.
- Никаких слов «гарантированно», «100%», «точно зайдёт», «верняк».
- summary — 2–3 предложения о матче. factors — 3–5 пунктов (Форма, Личные встречи,
  Турнирная мотивация/таблица, Новости, Что говорит линия), по 1–2 предложения.
  reasoning — почему именно эта ставка, 2–3 предложения. risks — что может сломать
  прогноз, 1–2 предложения.
- Пиши по-русски, коротко и по делу."""


class Analyzer:
    def __init__(self, repo: Repository, api_key: str, *, model: str, api_football_key: str = "",
                 football_data_key: str = ""):
        self.repo = repo
        self.model = model
        self.api_football_key = api_football_key
        self.football_data_key = football_data_key
        self.claude = anthropic.AsyncAnthropic(api_key=api_key)
        self._locks: dict[str, asyncio.Lock] = {}

    async def analyze(self, match: FootballMatch) -> dict | None:
        """Cached analysis for the match (computed once). None if the model declined."""
        lock = self._locks.setdefault(match.id, asyncio.Lock())
        async with lock:  # two users tapping the same match at once -> one model call
            row = self.repo.get_ai_analysis(match.id)
            if row is not None:
                return json.loads(row["payload"])
            data = await self._gather(match)
            result = await self._ask(match, data)
            if result is None:
                return None
            option = match.option(result["option_id"])
            if option is None:
                logger.warning("AI picked unknown option %s for %s", result["option_id"], match.id)
                return None
            result["pick"] = {"id": option.id, "label": option.label, "odds": option.odds}
            self.repo.save_ai_analysis(
                match.id, match.team_a, match.team_b, match.league, match.start_utc.isoformat(),
                json.dumps(result, ensure_ascii=False), option.id, option.kind, option.line,
                option.label, option.odds, result["confidence"],
            )
            return result

    async def _gather(self, match: FootballMatch) -> dict:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            async def form(team: str):
                try:
                    return (await fd_team_form(client, team, self.football_data_key) if self.football_data_key else None) \
                        or await espn_team_form(client, team)
                except Exception:
                    return None

            async def h2h():
                try:
                    return await get_match_h2h(client, match.team_a, match.team_b, self.api_football_key) \
                        if self.api_football_key else None
                except Exception:
                    return None

            async def news():
                try:
                    return await fetch_team_news(client, match.team_a, match.team_b)
                except Exception:
                    return []

            form_a, form_b, h2h_data, headlines = await asyncio.gather(
                form(match.team_a), form(match.team_b), h2h(), news()
            )
        return {"form_a": form_a, "form_b": form_b, "h2h": h2h_data, "news": headlines}

    async def _ask(self, match: FootballMatch, data: dict) -> dict | None:
        options = "\n".join(f"- id={o.id}: {o.label} @ {o.odds:.2f}" for o in match.options)
        content = (
            f"Матч: {match.team_a} — {match.team_b}\nТурнир: {match.league}\n"
            f"Начало (UTC): {match.start_utc:%Y-%m-%d %H:%M}\n\n"
            f"Доступные ставки (выбери одну по id):\n{options}\n\n"
            f"Форма и таблица {match.team_a}: {json.dumps(data['form_a'], ensure_ascii=False, default=str)}\n"
            f"Форма и таблица {match.team_b}: {json.dumps(data['form_b'], ensure_ascii=False, default=str)}\n"
            f"Личные встречи: {json.dumps(data['h2h'], ensure_ascii=False, default=str)}\n"
            f"Новости за сутки (заголовки): {json.dumps(data['news'], ensure_ascii=False, default=str)}\n"
        )
        response = await self.claude.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            logger.warning("AI analysis for %s stopped: %s", match.id, response.stop_reason)
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)


def hit_rate_line(repo: Repository) -> str:
    wins, total = repo.ai_hit_rate()
    if total < 20:
        return f"📊 Проходимость ИИ-прогнозов: считаем (рассчитано {total} из 20 нужных для честной цифры)"
    return f"📊 Проходимость ИИ-прогнозов: <b>{wins / total:.0%}</b> ({wins} из {total}, по результатам матчей)"

