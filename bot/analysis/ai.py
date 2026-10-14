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
from datetime import datetime, timezone
import anthropic
import httpx

from bot.analysis.catalog import FootballMatch, Option
from bot.db.repository import Repository
from bot.webapp.football_data import get_team_form as fd_team_form
from bot.webapp.football_stats import get_match_h2h
from bot.webapp.hockey_stats import get_match_h2h as hockey_h2h
from bot.webapp.tennis_elo import match_elo as tennis_elo
from bot.webapp.news import fetch_team_news
from bot.webapp.openligadb import get_team_form as openligadb_form
from bot.webapp.openscores import get_team_form as openscores_form
from bot.webapp.team_form import get_team_form as espn_team_form

logger = logging.getLogger(__name__)

# 5 levels (owner, 2026-10-03); "sure" = средняя and up.
CONFIDENCE = ("очень низкая", "низкая", "средняя", "высокая", "очень высокая")
SURE_CONFIDENCE = ("средняя", "высокая", "очень высокая")
HIGH_CONFIDENCE = ("высокая", "очень высокая")
# Distinct matches a user may open per MSK day (re-opening one already opened is free).
AI_FREE_PER_DAY = 1
AI_PAID_PER_DAY = 5

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
        "injuries": {"type": "string"},
        "motivation": {"type": "string"},
        "skip": {"type": "boolean"},
        "skip_reason": {"type": "string"},
    },
    "required": ["summary", "factors", "probabilities", "option_id", "confidence", "reasoning", "risks",
                 "injuries", "motivation", "skip", "skip_reason"],
    "additionalProperties": False,
}

SYSTEM = """Ты — спортивный аналитик (футбол, хоккей, баскетбол, теннис, киберспорт, MMA, бокс и др.). По присланным данным разбери матч и выбери ОДНУ
ставку из списка доступных вариантов (верни её id в option_id).

Правила:
- Опирайся только на присланные данные: коэффициенты, форму, таблицу, личные встречи,
  новости. Ничего не выдумывай — ни травм, ни составов, ни цифр. Если каких-то данных
  нет, так и учитывай, и не делай вид, что знаешь больше.
- Ставка ОБЯЗАНА совпадать с твоим выводом: никогда не ставь на исход, который ты сам
  оцениваешь ниже другого (если по твоим процентам вероятнее победа первой команды —
  нельзя ставить на победу второй или на ничью). Подходящие варианты: победа фаворита,
  фора/тотал, которые следуют из твоего разбора.
- Коэффициент ставки — от 1.30 до 3.00. Выше 3.00 — только при уверенности «высокая» или «очень высокая».
- confidence: «очень низкая» / «низкая» / «средняя» / «высокая» / «очень высокая» — честно.
  «Очень высокая» — редкость: только когда почти все факторы и линия единодушны. «Высокая» только когда
  несколько независимых факторов сходятся.
- Никаких слов «гарантированно», «100%», «точно зайдёт», «верняк».
- Разбор должен быть ГЛУБОКИМ и с цифрами (владелец: "бот должен проводить более
  глубокий анализ"): опирайся на конкретные числа из данных — счёт последних матчей,
  забито/пропущено, дома/в гостях отдельно, место и очки в таблице, личные встречи со
  счётом, средний тотал, усталость от календаря, кадровые потери. Общие слова без цифр
  («команда в хорошей форме») — брак. Перед ставкой на тотал проверь, как часто у обеих
  команд матчи проходили выше/ниже этой линии; перед ставкой на победу — сравни дом/выезд.
- summary — 3–5 предложений о матче. factors — 5–7 пунктов (Форма, Дом/выезд, Личные
  встречи, Таблица и мотивация, Состав и травмы, Статистика/тоталы, Что говорит линия),
  по 2–3 предложения, в каждом минимум одна конкретная цифра, если она есть в данных.
  reasoning — почему именно эта ставка, 3–5 предложений с цифрами. risks — что может
  сломать прогноз, 2–3 предложения.
- probabilities — твоя оценка шансов в процентах: p1 (победа первой команды), x (ничья),
  p2 (победа второй), целые числа, сумма 100. В видах спорта без ничьих (теннис,
  баскетбол, волейбол, киберспорт, настольный теннис, MMA, бокс) x = 0. Если даны коэффициенты, рыночные
  вероятности из них — ориентир, а не ответ: сначала оцени шансы сам по фактам, потом сверь с линией.
  Обязательно учитывай раздел «Скрытые факторы» (высота и климат стадиона, перелёт и усталость,
  смена тренера, конфликты, деньги, мотивация, погода) — именно они часто делают букмекерскую
  линию неточной; если такой фактор весомый, отклоняйся от рынка и прямо назови его в factors.
- Основной состав: сравни ожидаемый состав с обычной основой (раздел «Травмы и дисквалификации»).
  Без лучшего бомбардира, основного вратаря или 2+ игроков основы шансы команды заметно ниже —
  учти это в probabilities и в пункте «Состав и травмы» назови фамилии и их вклад в цифрах.
- Если списка доступных ставок нет (матча нет в линии), option_id — пустая строка, а
  reasoning объясняет, какой исход вероятнее и почему.
- injuries — травмы, дисквалификации и ожидаемые составы по данным из раздела «Свежая
  информация из интернета», 1–3 предложения; если таких данных нет — так и напиши.
- motivation — турнирная мотивация: за что борются команды, ротация, график, 1–2
  предложения.
- skip — честная рекомендация «лучше пропустить матч» (owner 2026-10-15). Ставка всё равно
  выбирается как обычно, но skip = true ТОЛЬКО если: шансы почти равны (у фаворита меньше 52%),
  или собранные факты прямо противоречат друг другу, или выбранная ставка явно невыгодна (твоя
  вероятность ниже той, что заложена в коэффициент). Фаворит 55%+ — нормальная ставка, НЕ пропуск
  (владелец 2026-10-15). Нехватка части данных сама по себе — не повод для skip: просто честно
  понизь уверенность. skip_reason — 1 предложение с цифрой; при skip = false — пустая строка.
- Пиши по-русски, коротко и по делу."""

# Team form + head-to-head come from a real API for football only (bot/webapp/football_data.py,
# football_stats.py) -- everywhere else, web search is the ONLY source of this, so each
# sport gets an explicit checklist instead of one football-shaped prompt (owner
# 2026-10-05: "подтянуть остальные виды спорта до уровня футбола").
RESEARCH_CHECKLISTS = {
    "football": ("травмы и дисквалификации, ожидаемые/подтверждённые составы, слова тренеров, "
                 "турнирную мотивацию (положение, за что борются, ротация перед другими турнирами), "
                 "смену тренера, превью и статистику последних игр"),
    "hockey": ("результаты последних 5-7 игр каждой команды, личные встречи в этом сезоне, "
               "травмы и дисквалификации ключевых игроков, состояние вратарей (ротация, статистика), "
               "турнирное положение и мотивацию, усталость от календаря (число игр за последние дни)"),
    "basketball": ("результаты последних 5-7 игр, личные встречи, травмы и статус звёздных игроков "
                   "(играет/под вопросом/не играет), ротацию состава, турнирное положение, "
                   "усталость от календаря и травел (back-to-back игры)"),
    "tennis": ("результаты последних 5-7 матчей каждого игрока, личные встречи и их счёт, "
               "статистику игрока именно на этом покрытии (хард/грунт/трава), травмы и снятия, "
               "сколько сетов/времени игрок провёл на корте в предыдущем матче (усталость), "
               "текущую форму и серии побед/поражений"),
    "esports": ("результаты последних матчей и турниров каждой команды, личные встречи, "
                "изменения состава (трансферы, замены игроков), статистику по картам/дисциплинам "
                "если есть, форму и буткемп перед турниром"),
    "table_tennis": ("результаты последних матчей, личные встречи, текущую форму и серии, "
                      "любые травмы или снятия"),
    "volleyball": ("результаты последних 5-7 игр, личные встречи, травмы ключевых игроков, "
                    "турнирное положение и мотивацию"),
    "combat": ("рекорд и статистику обоих спортсменов (победы нокаутом/сабмишеном/по очкам), личные "
               "встречи, рост и размах рук (reach), стойку и стиль (панчер/технарь/грэпплер), сгонку "
               "веса и как переносили её раньше, лагерь подготовки и спарринг-партнёров, травмы и "
               "смену соперника в последний момент, серию побед/поражений и возраст/физическое "
               "состояние; для MMA дополнительно -- умение бороться и стоять в партере"),
}


def _research_system(sport: str) -> str:
    checklist = RESEARCH_CHECKLISTS.get(sport, RESEARCH_CHECKLISTS["football"])
    return (
        f"Ты собираешь фактуру перед матчем ({sport}). Найди в интернете свежую информацию именно "
        f"об этом матче: {checklist}. Выпиши только факты с короткой пометкой, откуда они (сайт). "
        "Никаких прогнозов и ставок. По-русски, списком, до 25 пунктов, с цифрами. Если по матчу ничего не "
        "нашлось — так и напиши."
    )
WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
HLTV_SYSTEM = ("Ты собираешь статистику перед матчем Counter-Strike 2 только с сайта hltv.org. Найди: "
               "место обеих команд в рейтинге HLTV, результаты их последних 5-10 матчей, личные встречи, "
               "маппул и винрейт по картам (какие карты пикают/банят), текущий состав и замены/стендины, "
               "рейтинг ключевых игроков (Rating 3.0) за последние месяцы, формат матча (Bo1/Bo3). "
               "Только факты с цифрами, без прогнозов. По-русски, списком, до 15 пунктов. Если на HLTV "
               "ничего не нашлось — так и напиши.")
TENNIS_SYSTEM = ("Ты собираешь статистику перед теннисным матчем с сайта tennisabstract.com. Найди: "
                 "результаты последних 10 матчей каждого игрока со счётом и соперниками, винрейт на этом покрытии "
                 "за сезон, личные встречи, процент выигранных геймов на своей подаче и на приёме, сколько матчей "
                 "сыграно за последние 2 недели (усталость), снятия и травмы. Только факты с цифрами, без прогнозов. "
                 "По-русски, списком, до 15 пунктов. Если ничего не нашлось — так и напиши.")
# "Hidden" factors the line often underprices (owner 2026-10-15: altitude / climate of
# the home ground, coach sacked, off-field news -- "не только на основе коэффициента БК").
CONTEXT_SPORTS = ("football", "hockey", "basketball")
CONTEXT_SYSTEM = ("Ты ищешь НЕОЧЕВИДНЫЕ факторы перед матчем, которые букмекеры часто недооценивают. "
                  "Найди в интернете и выпиши только то, что реально нашлось, с цифрами и источником:\n"
                  "1) Стадион и условия: город, высота над уровнем моря, климат и прогноз погоды на день "
                  "матча (мороз, жара, дождь, ветер, снег), искусственный газон или натуральный, размеры поля, "
                  "атмосфера и посещаемость, насколько команда сильнее дома, чем в гостях (цифры).\n"
                  "2) Дорога гостей: расстояние перелёта, смена часовых поясов, дни отдыха, плотность календаря, "
                  "ротация перед еврокубками/важным матчем.\n"
                  "3) Новости клуба: смена или угроза увольнения тренера (эффект нового тренера), конфликты в "
                  "раздевалке, задержки зарплат и финансовые проблемы, трансферы, дисквалификации, "
                  "возвращение лидеров после травм.\n"
                  "4) Мотивация: дерби, принципиальность, борьба за выживание или титул, команде уже ничего не "
                  "нужно, матч памяти, юбилей, судья и его статистика карточек/пенальти.\n"
                  "5) Движение линии: если коэффициент сильно изменился за последние дни — почему.\n"
                  "Никаких прогнозов. По-русски, списком, до 15 пунктов. Чего не нашлось — не выдумывай.")
INJURIES_SYSTEM = ("Ты собираешь данные об ОСНОВНОМ СОСТАВЕ и потерях перед футбольным матчем с сайтов "
                   "football-lineups.com и transfermarkt. Для каждой команды:\n"
                   "1) Обычный стартовый состав последних матчей (схема и 11 фамилий) и ожидаемый/"
                   "подтверждённый состав на этот матч — кто из основы выпадает и кем его заменят.\n"
                   "2) Травмы и дисквалификации: кто не сыграет, причина, под вопросом ли, дата возвращения.\n"
                   "3) Насколько потеря ключевая: голы/передачи в сезоне, сколько матчей сыграл в старте, "
                   "капитан/вратарь/лучший бомбардир; кто вернулся после травмы.\n"
                   "Только факты, без прогнозов. По-русски, списком по командам. Если ничего не нашлось — "
                   "так и напиши.")
INJURY_DOMAINS = ["football-lineups.com", "transfermarkt.com", "transfermarkt.ru", "transfermarkt.de"]
FLASHSCORE_SYSTEM = ("Ты собираешь данные перед матчем только с сайта Flashscore (flashscorekz.com / "
                     "flashscore.com). Найди: результаты последних 5-10 матчей каждой команды/игрока со "
                     "счётом и датами, отдельно дома и в гостях, место и очки в турнирной таблице, личные "
                     "встречи со счётом, ожидаемые составы и отсутствующих игроков, если указаны. Только факты "
                     "с цифрами, без прогнозов. По-русски, списком, до 15 пунктов. Если ничего не нашлось — "
                     "так и напиши.")
STATS_SYSTEM = ("Ты собираешь ЦИФРЫ перед матчем, не новости. Найди в интернете: результаты последних "
                "5-10 матчей каждого участника со счётом, отдельно дома и в гостях; место, очки и разницу в "
                "таблице; средний тотал и как часто матчи проходили выше/ниже типичных линий; личные встречи "
                "со счётом за последние годы; сколько дней отдыха перед матчем и сколько игр за последние 2 "
                "недели. Только цифры и факты с пометкой сайта, без прогнозов. По-русски, списком, до 25 пунктов.")
SELF_CHECK_SYSTEM = ("Ты перепроверяешь чужой спортивный прогноз на завышенную уверенность. "
                     "Отвечай только честной, при необходимости пониженной уверенностью.")
SELF_CHECK_SCHEMA = {
    "type": "object",
    "properties": {"confidence": {"type": "string", "enum": list(CONFIDENCE)}},
    "required": ["confidence"], "additionalProperties": False,
}

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


def _accuracy_note(repo: Repository, sport: str) -> str:
    rate = repo.ai_winner_rate().get("by_sport", {}).get(sport)
    if not rate or rate["total"] < 15:
        return ""
    pct = round(rate["wins"] / rate["total"] * 100)
    if pct >= 65:
        return ""  # calibrated fine, nothing to flag
    return (f"Справка: в этом виде спорта твои прошлые прогнозы победителя сбывались в {pct}% "
            f"случаев ({rate['wins']} из {rate['total']}) -- заметно ниже обычного. Будь строже "
            "с уверенностью «высокая»/«очень высокая» здесь, пока данные это не подтвердят "
            "железно.\n\n")


def market_probabilities(match: FootballMatch) -> dict | None:
    """1X2 odds with the bookmaker margin removed, in % -- what the market prices in."""
    odds = {o.id: o.odds for o in match.options if o.kind in ("1x2", "winner")}
    if set(odds) == {"1", "2"}:
        odds["X"] = 0
    if not {"1", "2"} <= set(odds) or "X" not in odds:
        return None
    if not odds["X"]:
        inv1, inv2 = 1 / odds["1"], 1 / odds["2"]
        p1 = round(inv1 / (inv1 + inv2) * 100)
        return {"p1": p1, "x": 0, "p2": 100 - p1}
    inv = {k: 1 / v for k, v in odds.items()}
    total = sum(inv.values())
    p1, x = round(inv["1"] / total * 100), round(inv["X"] / total * 100)
    return {"p1": p1, "x": x, "p2": 100 - p1 - x}


def _consistent_option(match: FootballMatch, result: dict, option: Option | None) -> Option | None:
    """The pick must agree with the AI's own verdict: no betting on an outcome it rates
    below the favourite, and no long shots (> 3.00) without high confidence. Otherwise
    fall back to the favourite's own win/1X2 option (owner's complaint 2026-10-03: the
    verdict said team A 49% while the "confident bet" was team B @ 4.70)."""
    p = result.get("probabilities") or {}
    ranked = sorted((("1", p.get("p1", 0)), ("X", p.get("x", 0)), ("2", p.get("p2", 0))), key=lambda t: -t[1])
    favourite = ranked[0][0]
    fav_option = match.option(favourite) or match.option("1" if p.get("p1", 0) >= p.get("p2", 0) else "2")
    if option is None:
        return fav_option
    contradicts = option.kind in ("1x2", "winner") and option.id != favourite
    long_shot = option.odds > 3.0 and result.get("confidence") not in HIGH_CONFIDENCE
    if (contradicts or long_shot) and fav_option is not None:
        return fav_option
    return option


def _normalise(probs: dict) -> dict:
    vals = [max(0, int(probs.get(k, 0))) for k in ("p1", "x", "p2")]
    total = sum(vals) or 1
    p1, x = round(vals[0] * 100 / total), round(vals[1] * 100 / total)
    return {"p1": p1, "x": x, "p2": 100 - p1 - x}


class Analyzer:
    def __init__(self, repo: Repository, api_key: str, *, model: str, api_football_key: str = "",
                 football_data_key: str = "", openscores_api_key: str = "",
                 bot=None, admin_chat_ids: frozenset[int] = frozenset()):
        self.repo = repo
        self.model = model
        self.api_football_key = api_football_key
        self.football_data_key = football_data_key
        self.openscores_api_key = openscores_api_key
        self.bot = bot
        self.admin_chat_ids = admin_chat_ids
        self.claude = anthropic.AsyncAnthropic(api_key=api_key)
        self._locks: dict[str, asyncio.Lock] = {}
        # analyses of matches not in the line (no pick to settle): memory only
        self._adhoc: dict[str, dict] = {}

    async def analyze(self, match: FootballMatch, final: bool = False) -> dict | None:
        """Cached analysis for the match (computed once). None if the model declined.
        final=True: the pre-kickoff re-analysis (see bot/analysis/final_pass.py) --
        recomputes over the early one unless that's already been done."""
        lock = self._locks.setdefault(match.id, asyncio.Lock())
        async with lock:  # two users tapping the same match at once -> one model call
            row = self.repo.get_ai_analysis(match.id)
            if row is not None:
                cached = json.loads(row["payload"])
                if not final or cached.get("final"):
                    return cached
            if match.id in self._adhoc and not final:
                return self._adhoc[match.id]
            data = await self._gather(match)
            result = await self._ask(match, data)
            if result is None:
                return None
            from bot.analysis.catalog import SPORTS

            probs = dict(result["probabilities"])
            if match.options and not SPORTS.get(match.sport, {}).get("draw", True):
                probs["x"] = 0
            result["probabilities"] = _normalise(probs)
            result["sport"] = match.sport
            result["final"] = final
            result["market"] = market_probabilities(match)
            if not match.options:
                result["pick"] = None
                self._adhoc[match.id] = result
                return result
            option = _consistent_option(match, result, match.option(result["option_id"]))
            if option is None:
                logger.warning("AI picked unknown option %s for %s", result["option_id"], match.id)
                return None
            if option.odds > 2.5 or result["confidence"] in HIGH_CONFIDENCE:
                result["confidence"] = await self._self_check(match, result, option) or result["confidence"]
            result["option_id"] = option.id
            result["pick"] = {"id": option.id, "label": option.label, "odds": option.odds}
            self.repo.save_ai_analysis(
                match.id, match.team_a, match.team_b, match.league, match.start_utc.isoformat(),
                json.dumps(result, ensure_ascii=False), option.id, option.kind, option.line,
                option.label, option.odds, result["confidence"], match.sport, match.is_popular,
            )
            from bot.analysis.bankroll import place_bet

            if not result.get("skip"):  # AI itself advises sitting this one out
                place_bet(self.repo, match, option, result["confidence"])
            return result

    async def _self_check(self, match: FootballMatch, result: dict, option: Option) -> str | None:
        """Second, cold look at an already-built high-stakes verdict (long-shot odds or
        top confidence) -- catches overconfidence a single pass can miss. Can only lower
        confidence, never raise it; None/any failure leaves the original untouched."""
        content = (
            f"Матч: {match.team_a} — {match.team_b}. Твой вывод: {result['summary']}\n"
            f"Факторы: {json.dumps(result.get('factors', []), ensure_ascii=False)}\n"
            f"Твоя ставка: {option.label} @ {option.odds:.2f}, заявленная уверенность: {result['confidence']}.\n"
            "Трезво перепроверь: данных и правда достаточно для такой уверенности, или это скорее "
            "предположение? Если сомнения есть -- понизь уверенность на один шаг честно."
        )
        checked = await self._json_call(SELF_CHECK_SYSTEM, content, SELF_CHECK_SCHEMA, effort="low")
        if not checked or checked.get("confidence") not in CONFIDENCE:
            return None
        if CONFIDENCE.index(checked["confidence"]) > CONFIDENCE.index(result["confidence"]):
            return None  # never talk itself into MORE confidence on a second pass
        return checked["confidence"]

    async def _gather(self, match: FootballMatch) -> dict:
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": "Mozilla/5.0"}) as client:
            async def form(team: str):
                try:
                    # Free/no-key first (German leagues only, but costs nothing to try),
                    # then the paid/keyed sources, then OpenScores as the last resort
                    # (top-5 leagues + World Cup only -- see openscores.py's docstring).
                    found = await openligadb_form(client, team) \
                        or (await fd_team_form(client, team, self.football_data_key) if self.football_data_key else None) \
                        or await espn_team_form(client, team)
                    if found:
                        return found
                    return await openscores_form(team, self.openscores_api_key) if self.openscores_api_key else None
                except Exception:
                    return None

            async def h2h():
                try:
                    return await get_match_h2h(client, match.team_a, match.team_b, self.api_football_key) \
                        if self.api_football_key else None
                except Exception:
                    return None

            async def hockey_h2h_():
                # Same api-sports.io account as football's key -- confirmed live
                # 2026-10-11 that it works on v1.hockey.api-sports.io too, no separate key.
                try:
                    return await hockey_h2h(client, match.team_a, match.team_b, self.api_football_key) \
                        if self.api_football_key else None
                except Exception:
                    return None

            async def news():
                try:
                    return await fetch_team_news(client, match.team_a, match.team_b)
                except Exception:
                    return []

            if match.sport == "football":
                form_a, form_b, h2h_data, headlines = await asyncio.gather(
                    form(match.team_a), form(match.team_b), h2h(), news()
                )
            elif match.sport == "hockey":
                form_a = form_b = None
                h2h_data, headlines = await asyncio.gather(hockey_h2h_(), news())
            elif match.sport == "tennis":
                form_a = form_b = h2h_data = None
                elo, headlines = await asyncio.gather(
                    tennis_elo(client, match.team_a, match.team_b, match.league), news())
            else:
                form_a = form_b = h2h_data = None
                headlines = await news()
        # Flashscore (owner 2026-10-15): results, standings, H2H, lineups for every sport.
        # Read through web search restricted to its domains, runs alongside the other passes.
        flash_task = asyncio.create_task(self._research(
            match, FLASHSCORE_SYSTEM,
            {**WEB_SEARCH_TOOL, "max_uses": 4, "allowed_domains": ["flashscorekz.com", "flashscore.com"]}))
        # Injuries (owner 2026-10-15): football-lineups.com is behind Cloudflare for direct
        # requests (403 from the server), so it's read via domain-restricted web search.
        inj_task = asyncio.create_task(self._research(
            match, INJURIES_SYSTEM, {**WEB_SEARCH_TOOL, "max_uses": 4, "allowed_domains": INJURY_DOMAINS},
        )) if match.sport == "football" else None
        if match.sport == "esports" and match.league.startswith("Counter-Strike"):
            # CS2: HLTV is the reference source (ranking, map pool, recent results, H2H).
            # hltv.org is behind Cloudflare bot protection for direct requests, so it's
            # read through the web search tool restricted to that domain (owner 2026-10-06).
            general, hltv = await asyncio.gather(self._research(match), self._research(
                match, HLTV_SYSTEM, {**WEB_SEARCH_TOOL, "max_uses": 6, "allowed_domains": ["hltv.org"]}))
            research = f"{general}\n\nДанные HLTV:\n{hltv}" if hltv else general
        elif match.sport == "tennis":
            # Tennis (owner 2026-10-15): Elo from Tennis Abstract + a search pass on that site.
            general, ta = await asyncio.gather(self._research(match), self._research(
                match, TENNIS_SYSTEM, {**WEB_SEARCH_TOOL, "max_uses": 6, "allowed_domains": ["tennisabstract.com"]}))
            research = "\n\n".join(x for x in (elo, general, f"Данные Tennis Abstract:\n{ta}" if ta else "") if x)
        else:
            # Deeper analysis (owner 2026-10-11): a second, stats-only search pass in
            # parallel with the general one -- numbers (home/away splits, totals trend,
            # table, schedule) rather than news.
            passes = [self._research(match), self._research(match, STATS_SYSTEM)]
            if match.sport in CONTEXT_SPORTS:
                passes.append(self._research(match, CONTEXT_SYSTEM))
            general, stats, *ctx = await asyncio.gather(*passes)
            research = general + (f"\n\nСтатистика:\n{stats}" if stats else "") \
                + (f"\n\nСкрытые факторы:\n{ctx[0]}" if ctx and ctx[0] else "")
        flash = await flash_task
        if flash:
            research += f"\n\nДанные Flashscore:\n{flash}"
        injuries = await inj_task if inj_task else ""
        if injuries:
            research += f"\n\nТравмы и дисквалификации (football-lineups / transfermarkt):\n{injuries}"
        return {"form_a": form_a, "form_b": form_b, "h2h": h2h_data, "news": headlines, "research": research}

    async def _research(self, match: FootballMatch, system: str | None = None, tool: dict | None = None) -> str:
        """Fresh facts from the web (injuries, lineups, motivation, previews) via the
        server-side web search tool. Best effort: "" if the gateway doesn't support the
        tool or anything fails -- the analysis then runs on the other data alone."""
        when = f" ({match.start_utc:%d.%m.%Y})" if match.start_utc.year > 2000 else ""
        messages = [{"role": "user", "content": f"Матч: {match.team_a} — {match.team_b}{when}. {match.league}"}]
        # Non-football sports have no structured form/H2H API behind them (see _gather),
        # so web search is doing double duty there -- give it a bigger budget.
        tool = tool or {**WEB_SEARCH_TOOL, "max_uses": 8 if match.sport == "football" else 10}
        system = (system or _research_system(match.sport)) + (
            f"\n\nСегодня {datetime.now(timezone.utc):%d.%m.%Y} — это реальная текущая дата, матч предстоящий. "
            "Используй инструменты web_search и fetch_page: сначала поиск, потом открой 2-4 самые полезные "
            "страницы и выпиши факты. Не отвечай по памяти.")
        domains = tool.get("allowed_domains")
        budget = int(tool.get("max_uses", 6)) + 3  # searches + page reads
        # The gateway's server-side web_search is unavailable (2026-10-15), so we run
        # our own tools (bot/analysis/websearch.py) in a client-side tool loop.
        from bot.analysis import websearch
        from bot.analysis.token_budget import record as _record_token_usage

        try:
            async with httpx.AsyncClient() as http:
                used = 0
                for _ in range(budget + 1):
                    response = await self.claude.messages.create(
                        model=self.model, max_tokens=6000, system=system, messages=messages,
                        tools=websearch.TOOLS, output_config={"effort": "low"},
                        tool_choice={"type": "auto"} if used < budget else {"type": "none"},
                    )
                    await _record_token_usage(self.repo, response, self.bot, self.admin_chat_ids)
                    calls = [b for b in response.content if b.type == "tool_use"]
                    if response.stop_reason != "tool_use" or not calls:
                        break
                    results = []
                    for c in calls:
                        used += 1
                        if c.name == "web_search":
                            out = await websearch.search(http, str(c.input.get("query", "")), domains)
                        else:
                            out = await websearch.fetch(http, str(c.input.get("url", "")), domains)
                        results.append({"type": "tool_result", "tool_use_id": c.id, "content": out})
                    messages = messages + [{"role": "assistant", "content": response.content},
                                           {"role": "user", "content": results}]
            if response.stop_reason == "refusal":
                return ""
            return "\n".join(b.text for b in response.content if b.type == "text").strip()[:10000]
        except Exception:
            logger.warning("Web research unavailable for %s", match.id, exc_info=True)
            return ""

    async def _ask(self, match: FootballMatch, data: dict) -> dict | None:
        options = "\n".join(f"- id={o.id}: {o.label} @ {o.odds:.2f}" for o in match.options)
        line = (f"Доступные ставки (выбери одну по id):\n{options}\n\n" if options
                else "Матча нет в линии букмекеров: коэффициентов нет, option_id оставь пустым.\n\n")
        market = market_probabilities(match)
        market_line = (f"Рынок (коэффициенты без маржи букмекера) закладывает: "
                       f"П1 {market['p1']}%" + (f", ничья {market['x']}%" if market['x'] else "")
                       + f", П2 {market['p2']}%. Это ориентир, а не готовый ответ -- "
                       "отклоняйся от него только когда собранные данные это реально обосновывают.\n\n"
                       if market else "")
        accuracy_line = _accuracy_note(self.repo, match.sport)
        lessons = self.repo.get_ai_note("lessons")
        if lessons:
            accuracy_line += f"Уроки твоих прошлых ошибок (разбор незашедших прогнозов за 2 недели):\n{lessons}\n\n"
        when = f"Начало (UTC): {match.start_utc:%Y-%m-%d %H:%M}\n" if match.start_utc.year > 2000 else ""
        from bot.analysis.catalog import SPORTS

        sport_name = SPORTS.get(match.sport, {}).get("name", "определи сам")
        content = (
            f"Вид спорта: {sport_name}\n"
            f"Матч: {match.team_a} — {match.team_b}\nТурнир: {match.league or 'неизвестен'}\n"
            f"{when}\n{line}{market_line}{accuracy_line}"
            f"Форма и таблица {match.team_a}: {json.dumps(data['form_a'], ensure_ascii=False, default=str)}\n"
            f"Форма и таблица {match.team_b}: {json.dumps(data['form_b'], ensure_ascii=False, default=str)}\n"
            f"Личные встречи: {json.dumps(data['h2h'], ensure_ascii=False, default=str)}\n"
            f"Новости за сутки (заголовки): {json.dumps(data['news'], ensure_ascii=False, default=str)}\n"
            f"\nСвежая информация из интернета:\n{data.get('research') or 'нет данных'}\n"
        )
        return await self._json_call(SYSTEM, content, SCHEMA, effort="high")

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
        from bot.analysis.token_budget import record as _record_token_usage

        await _record_token_usage(self.repo, response, self.bot, self.admin_chat_ids)
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

