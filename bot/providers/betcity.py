"""Betcity (betcity.ru) -- the whole prematch line is one open JSON endpoint on the
site's own API host, `https://ad.betcity.ru/d/off/events` (confirmed live 2026-09-25:
~7600 events across 33 sports in one ~10 MB response, no cookies, auth or anti-bot).

Shape: `reply.sports[<id_sp>].chmps[<id_ch>].evts[<id_ev>]`, each event with
`name_ht`/`name_at` (home/away), `date_ev` (unix) and `main[<market id>]`:
  69 "Фактический исход": blocks.Wm   {P1, X?, P2} -> {kf}
  71 "Фора":              blocks.F1m  {Kf_F1:{kf, lv}, Kf_F2:{kf, lv}}
  72 "Тотал":             blocks.T1m  {Tm:{kf}, Tb:{kf}, Tot}
Stat sub-markets reuse the same ids with a prefixed name ("УГЛ. Тотал" = corners,
"ЖК. Фора" = cards), so a market is taken only when its name is exactly the plain one.
Outrights ("... Победитель") have no `name_at` and are skipped.

Same per-sport market rules as zenit.py: totals for football/hockey (draw-free), match
winner for two-way sports (only when X is unpriced), half-line handicaps for football
and basketball.
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BASE_URL = "https://ad.betcity.ru"
LINE_PATH = "/d/off/events"
LINE_PARAMS = {"rev": 1, "ver": 1, "csn": "ooca9s"}
BOOKMAKER = "betcity"

SPORT_IDS = {
    "football": "1",
    "hockey": "7",
    "basketball": "3",
    "tennis": "2",
    # All esports share sport 73; the game is the championship-name prefix.
    "cs2": "73",
    "dota2": "73",
    "lol": "73",
    "valorant": "73",
}
ESPORTS_PREFIXES = {
    "cs2": ("CS2.", "CS 2.", "CS:GO."),
    "dota2": ("Dota 2.",),
    "lol": ("League of Legends.",),
    "valorant": ("Valorant.",),
}
TOTALS_GAMES = frozenset({"football", "hockey"})
WINNER_GAMES = frozenset({"basketball", "tennis", "cs2", "dota2", "lol", "valorant"})
HANDICAP_GAMES = frozenset({"football", "basketball"})

WIN_MARKET, HANDICAP_MARKET, TOTAL_MARKET = "69", "71", "72"
WIN_NAME, HANDICAP_NAME, TOTAL_NAME = "Фактический исход", "Фора", "Тотал"
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)


class BetcityProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, timeout=60.0, headers={"User-Agent": "Mozilla/5.0"})

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = [g for g in games if g in SPORT_IDS]
        if not wanted:
            return []
        resp = await self._client.get(LINE_PATH, params=LINE_PARAMS)
        resp.raise_for_status()
        raw = resp.json()
        quotes: list[SourceQuote] = []
        for game in wanted:
            quotes.extend(parse_line(game, raw))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_line(game: str, raw: dict) -> list[SourceQuote]:
    sport = ((raw.get("reply") or {}).get("sports") or {}).get(SPORT_IDS.get(game, ""))
    if not sport:
        return []
    prefixes = ESPORTS_PREFIXES.get(game)
    quotes: list[SourceQuote] = []
    for champ in (sport.get("chmps") or {}).values():
        if prefixes is not None and not (champ.get("name_ch") or "").startswith(prefixes):
            continue
        for event in (champ.get("evts") or {}).values():
            team_a, team_b = event.get("name_ht"), event.get("name_at")
            if not team_a or not team_b:
                continue
            start = _unix_to_iso(event.get("date_ev"))
            main = event.get("main") or {}
            if game in HANDICAP_GAMES:
                quotes.extend(_handicap_quotes(game, team_a, team_b, start, main))
            if game in WINNER_GAMES:
                quotes.extend(_winner_quotes(game, team_a, team_b, start, main))
            if game in TOTALS_GAMES:
                quotes.extend(_total_quotes(game, team_a, team_b, start, main))
    return quotes


def _block(main: dict, market_id: str, name: str, block: str) -> dict | None:
    market = main.get(market_id)
    if not market or market.get("name") != name:
        return None
    for entry in (market.get("data") or {}).values():
        found = (entry.get("blocks") or {}).get(block)
        if found:
            return found
    return None


def _kf(side) -> float | None:
    try:
        value = float((side or {}).get("kf"))
    except (TypeError, ValueError):
        return None
    return value if value > 1.0 else None


def _winner_quotes(game, team_a, team_b, start, main) -> list[SourceQuote]:
    block = _block(main, WIN_MARKET, WIN_NAME, "Wm")
    if not block or _kf(block.get("X")):
        return []  # missing, or draw priced -> three-way market
    odds_a, odds_b = _kf(block.get("P1")), _kf(block.get("P2"))
    if not odds_a or not odds_b:
        return []
    return [
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, team_a, odds_a),
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, team_b, odds_b),
    ]


def _total_quotes(game, team_a, team_b, start, main) -> list[SourceQuote]:
    block = _block(main, TOTAL_MARKET, TOTAL_NAME, "T1m")
    if not block:
        return []
    under, over = _kf(block.get("Tm")), _kf(block.get("Tb"))
    try:
        line = float(block.get("Tot"))
    except (TypeError, ValueError):
        return []
    if not under or not over or not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
        return []
    market = f"total_{line}"
    return [
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, f"Тотал больше {line}", over, market),
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, f"Тотал меньше {line}", under, market),
    ]


def _handicap_quotes(game, team_a, team_b, start, main) -> list[SourceQuote]:
    """Half lines only (no push); reconcile.py lines H1/H2 up across books."""
    block = _block(main, HANDICAP_MARKET, HANDICAP_NAME, "F1m")
    if not block:
        return []
    f1, f2 = block.get("Kf_F1") or {}, block.get("Kf_F2") or {}
    o1, o2 = _kf(f1), _kf(f2)
    try:
        h1, h2 = float(f1.get("lv")), float(f2.get("lv"))
    except (TypeError, ValueError):
        return []
    if not o1 or not o2 or h1 != -h2 or (abs(h1) * 2) % 2 != 1:
        return []
    return [
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, f"H1:{h1}", o1, "hcp"),
        SourceQuote(game, team_a, team_b, start, BOOKMAKER, f"H2:{h2}", o2, "hcp"),
    ]


def _unix_to_iso(ts) -> str:
    if not ts:
        return ""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
