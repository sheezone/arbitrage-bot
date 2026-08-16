"""SureBet (surebet.com) -- unlike every other source in this codebase, its API returns
matches it has ALREADY identified as genuine surebets (each one ships as exactly two
"prongs": one bookmaker's price on each outcome), not a raw per-bookmaker line dump. So
there's nothing here for bot/core/reconcile.py to do -- SureBet already did the
cross-bookmaker matching for us. What we still do ourselves: recompute the arbitrage
math independently with bot/core/arbitrage.calc_arbitrage() rather than trusting
SureBet's own numbers -- same "verify, don't trust a third party's math" policy as
everywhere else here.

Checked live 2026-08-16: most surebets on the free test tier turn out to be totals or
handicap markets (e.g. "total rounds map2 over/under 10.5"), not simple match-winner
markets -- so outcome labels are derived generically from each prong's `type` field
(over/under/handicap/moneyline) rather than assumed to be team names, unlike every other
provider here where outcome_name always matches team_a/team_b.

Uses the TEST API token published publicly on ru.surebet.com/site/xml for developers to
try before buying -- not a personal account, no login involved. This tier caps surebets
at <=1% margin and is rate-limited to roughly one request/minute, so it's cached hard
(MIN_INTERVAL_SECONDS below), independent of the bot's main poll interval. If the bot
owner ever buys real API access (100 EUR/bookmaker/month via surebet.com), point
SUREBET_API_TOKEN at the paid token in .env -- nothing else here needs to change.

Bookmaker list is deliberately limited to ones this bot cannot reach directly itself
(Melbet/Winline/Zenit/Betcity/BetBoom are all behind Cloudflare/Qrator/WebSocket -- see
bot/providers/baltbet.py's module docstring for the investigation), not Fonbet/PARI/
Marathon/Baltbet, which are already scraped directly and would just be redundant here.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import httpx

from bot.core.arbitrage import ArbitrageResult, OutcomeOdds, calc_arbitrage

logger = logging.getLogger(__name__)

BASE_URL = "https://api.apostasseguras.com"
DEFAULT_TEST_TOKEN = "57cd1f13-fd58-4556-a5b9-05f2bcc2eab5"

GAME_TO_SPORT_ID = {
    "cs2": "CounterStrike",
    "dota2": "Dota",
    "lol": "LeagueOfLegends",
    "valorant": "Valorant",
    "tennis": "Tennis",
    "basketball": "Basketball",
}
SPORT_ID_TO_GAME = {v: k for k, v in GAME_TO_SPORT_ID.items()}

BOOKMAKERS = ["melbet", "winline", "zenit", "betcity", "bingoboom"]

MIN_INTERVAL_SECONDS = 65  # test tier is rate-limited to ~1 request/minute

SurebetMatch = tuple[str, str, str, str, ArbitrageResult]  # game, team_a, team_b, start_time_utc, arb


class SurebetFinder:
    def __init__(self, api_token: str = DEFAULT_TEST_TOKEN, base_url: str = BASE_URL):
        self._api_token = api_token
        self._client = httpx.AsyncClient(base_url=base_url, timeout=15.0)
        self._cache: list[SurebetMatch] = []
        self._cache_at = 0.0

    async def find(self, games: list[str]) -> list[SurebetMatch]:
        sport_ids = [GAME_TO_SPORT_ID[g] for g in games if g in GAME_TO_SPORT_ID]
        if not sport_ids:
            return []

        if time.time() - self._cache_at < MIN_INTERVAL_SECONDS:
            return self._cache

        try:
            resp = await self._client.get(
                "/request",
                params={"product": "surebets", "source": "|".join(BOOKMAKERS), "sport": "|".join(sport_ids)},
                headers={"Authorization": f"Bearer {self._api_token}"},
            )
            resp.raise_for_status()
        except httpx.HTTPError:
            logger.exception("SureBet API request failed")
            return self._cache

        self._cache = parse_records(resp.json())
        self._cache_at = time.time()
        return self._cache

    async def close(self) -> None:
        await self._client.aclose()


def parse_records(data: dict) -> list[SurebetMatch]:
    results: list[SurebetMatch] = []
    for record in data.get("records", []):
        prongs = record.get("prongs", [])
        if len(prongs) != 2:
            continue  # not a clean 2-way surebet -- skip rather than guess

        sport_ids = {p.get("sport_id") for p in prongs}
        if len(sport_ids) != 1:
            continue
        game = SPORT_ID_TO_GAME.get(next(iter(sport_ids)))
        if game is None:
            continue

        teams = prongs[0].get("teams") or []
        if len(teams) != 2:
            continue
        team_a, team_b = teams

        odds_by_outcome: dict[str, list[OutcomeOdds]] = {}
        valid = True
        for prong in prongs:
            bookmaker = prong.get("bk")
            value = prong.get("value")
            if not bookmaker or not value:
                valid = False
                break
            label = _describe_outcome(prong)
            odds_by_outcome.setdefault(label, []).append(OutcomeOdds(label, f"surebet:{bookmaker}", float(value)))
        if not valid or len(odds_by_outcome) != 2:
            continue  # both prongs landed on the same label, or something was missing -- skip

        arb = calc_arbitrage(odds_by_outcome)
        if not arb.is_arbitrage:
            continue  # our own recompute disagreed with SureBet's -- skip, don't trust blindly

        start_time_utc = _unix_ms_to_iso(prongs[0].get("time"))
        results.append((game, team_a, team_b, start_time_utc, arb))
    return results


def _describe_outcome(prong: dict) -> str:
    t = prong.get("type") or {}
    kind = t.get("type", "")
    condition = t.get("condition", "")
    period = t.get("period", "")
    teams = prong.get("teams") or ["", ""]
    period_label = f" ({period})" if period and period not in ("regularTime", "full") else ""

    if kind == "over":
        return f"Тотал > {condition}{period_label}"
    if kind == "under":
        return f"Тотал < {condition}{period_label}"
    if kind == "ah1":
        return f"Фора {teams[0]} {condition}{period_label}"
    if kind == "ah2":
        return f"Фора {teams[1]} {condition}{period_label}"
    if kind in ("win1", "1", "winOnly1"):
        return teams[0]
    if kind in ("win2", "2", "winOnly2"):
        return teams[1]
    return f"{kind or 'Исход'} {condition}{period_label}".strip()


def _unix_ms_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).isoformat()
