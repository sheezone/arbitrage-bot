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
# Distinct matches a user may open per MSK day (re-opening one already opened is free).
AI_FREE_PER_DAY = 1
AI_PAID_PER_DAY = 15

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
        "probabilities": {
            "type": "object",
            "properties": {"p1": {"type": "integer"}, "x": {"type": "integer"}, "p2": {"type": "integer"}},
            "required": ["p1", "x", "p2"],
            "additionalProperties": False,
        },
        "option_id": {"type": "string"},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
        "reasoning": {"type": "string"},
        "risks": {"type": "string"},
    },
    "required": ["summary", "factors", "probabilities", "option_id", "confidence", "reasoning", "risks"],
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
- probabilities — твоя оценка шансов в процентах: p1 (победа первой команды), x (ничья),
  p2 (победа второй), целые числа, сумма 100. Если даны коэффициенты, рыночные
  вероятности из них — ориентир; отклоняйся от них только когда данные это обосновывают.
- Если списка доступных ставок нет (матча нет в линии), option_id — пустая строка, а
  reasoning объясняет, какой исход вероятнее и почему.
- Пиши по-русски, коротко и по делу."""

SCREENSHOT_SCHEMA = {
    "type": "object",
    "properties": {"team_a": {"type": "string"}, "team_b": {"type": "string"}, "found": {"type": "boolean"}},
    "required": ["team_a", "team_b", "found"],
    "additionalProperties": False,
}
RESOLVE_SCHEMA = {
    "type": "object",
    "properties": {"match_id": {"type": "string"}},
    "required": ["match_id"],
    "additionalProperties": False,
}


def market_probabilities(match: FootballMatch) -> dict | None:
    """1X2 odds with the bookmaker margin removed, in % -- what the market prices in."""
    odds = {o.id: o.odds for o in match.options if o.kind == "1x2"}
    if set(odds) != {"1", "X", "2"}:
        return None
    inv = {k: 1 / v for k, v in odds.items()}
    total = sum(inv.values())
    p1, x = round(inv["1"] / total * 100), round(inv["X"] / total * 100)
    return {"p1": p1, "x": x, "p2": 100 - p1 - x}


def _normalise(probs: dict) -> dict:
    vals = [max(0, int(probs.get(k, 0))) for k in ("p1", "x", "p2")]
    total = sum(vals) or 1
    p1, x = round(vals[0] * 100 / total), round(vals[1] * 100 / total)
    return {"p1": p1, "x": x, "p2": 100 - p1 - x}


class Analyzer:
    def __init__(self, repo: Repository, api_key: str, *, model: str, api_football_key: str = "",
                 football_data_key: str = ""):
        self.repo = repo
        self.model = model
        self.api_football_key = api_football_key
        self.football_data_key = football_data_key
        self.claude = anthropic.AsyncAnthropic(api_key=api_key)
        self._locks: dict[str, asyncio.Lock] = {}
        # analyses of matches not in the line (no pick to settle): memory only
        self._adhoc: dict[str, dict] = {}

    async def analyze(self, match: FootballMatch) -> dict | None:
        """Cached analysis for the match (computed once). None if the model declined."""
        lock = self._locks.setdefault(match.id, asyncio.Lock())
        async with lock:  # two users tapping the same match at once -> one model call
            row = self.repo.get_ai_analysis(match.id)
            if row is not None:
                return json.loads(row["payload"])
            if match.id in self._adhoc:
                return self._adhoc[match.id]
            data = await self._gather(match)
            result = await self._ask(match, data)
            if result is None:
                return None
            result["probabilities"] = _normalise(result["probabilities"])
            result["market"] = market_probabilities(match)
            if not match.options:
                result["pick"] = None
                self._adhoc[match.id] = result
                return result
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
        line = (f"Доступные ставки (выбери одну по id):\n{options}\n\n" if options
                else "Матча нет в линии букмекеров: коэффициентов нет, option_id оставь пустым.\n\n")
        when = f"Начало (UTC): {match.start_utc:%Y-%m-%d %H:%M}\n" if match.start_utc.year > 2000 else ""
        content = (
            f"Матч: {match.team_a} — {match.team_b}\nТурнир: {match.league or 'неизвестен'}\n"
            f"{when}\n{line}"
            f"Форма и таблица {match.team_a}: {json.dumps(data['form_a'], ensure_ascii=False, default=str)}\n"
            f"Форма и таблица {match.team_b}: {json.dumps(data['form_b'], ensure_ascii=False, default=str)}\n"
            f"Личные встречи: {json.dumps(data['h2h'], ensure_ascii=False, default=str)}\n"
            f"Новости за сутки (заголовки): {json.dumps(data['news'], ensure_ascii=False, default=str)}\n"
        )
        return await self._json_call(SYSTEM, content, SCHEMA, effort="medium")

    async def _json_call(self, system: str, content, schema: dict, effort: str = "low") -> dict | None:
        response = await self.claude.beta.messages.create(
            model=self.model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason in ("refusal", "max_tokens"):
            logger.warning("AI call stopped: %s", response.stop_reason)
            return None
        text = next((b.text for b in response.content if b.type == "text"), "")
        return json.loads(text)

    async def read_screenshot(self, image_b64: str, media_type: str) -> dict | None:
        """Team names of the football match shown on a screenshot (bookmaker app, TV, ...)."""
        return await self._json_call(
            "Определи по скриншоту футбольный матч: названия двух команд в том порядке, как на "
            "скриншоте (хозяева первыми), по-русски, как их пишут букмекеры. Если матча на "
            "скриншоте нет, found=false и пустые строки.",
            [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": "Какой матч на скриншоте?"},
            ],
            SCREENSHOT_SCHEMA,
        )

    async def resolve_teams(self, team_a: str, team_b: str, candidates: list[FootballMatch]) -> str:
        """The line match the user meant ("Man City vs Arsenal" -> its Russian line entry),
        or "" if none of the candidates is that match."""
        if not candidates:
            return ""
        listing = "\n".join(f"{m.id}: {m.team_a} — {m.team_b} ({m.league})" for m in candidates)
        result = await self._json_call(
            "Пользователь ввёл две футбольные команды (возможно, на английском, сокращённо или с "
            "опечатками). Найди этот матч в списке и верни его id. Если такого матча в списке нет, "
            "верни пустую строку. Не подбирай похожий матч, если команды другие.",
            f"Команды: {team_a} и {team_b}\n\nСписок матчей:\n{listing}",
            RESOLVE_SCHEMA,
        )
        return (result or {}).get("match_id", "")


def hit_rate_line(repo: Repository) -> str:
    wins, total = repo.ai_hit_rate()
    if total < 20:
        return f"📊 Проходимость ИИ-прогнозов: считаем (рассчитано {total} из 20 нужных для честной цифры)"
    return f"📊 Проходимость ИИ-прогнозов: <b>{wins / total:.0%}</b> ({wins} из {total}, по результатам матчей)"

