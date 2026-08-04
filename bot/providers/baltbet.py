"""Baltbet (baltbet.ru) -- unlike Fonbet/PARI, market cells here carry readable Russian
labels (`data-title="Исход (1)"`) instead of opaque numeric factor ids, so no live/
statistical guessing was needed to confirm which cell is which outcome.

Verified live 2026-08-04. The homepage's "hot events" widgets are plain server-rendered
HTML, no JS/CAPTCHA: `GET /widgets/<sport>/update/line` returns a flat list of
`<div class="events-table__body-row" data-title="Team A — Team B" data-islive="false">`
rows. Each row contains:
- an `<a href="/<category>/<slug>/.../<name>-id-<event_id>">` -- for cybersport specifically,
  the second path segment names the actual title (e.g. "dota-2", "counter-strike",
  "league-of-legends", "valorant"); tennis/basketball don't need this since the whole
  widget is a single game.
- exactly two `<div class="... m-market-0 ..." data-title="Исход (1)" data-coefvalue="1.47">`
  cells (2-way match-winner market) for cs2/dota2/lol/valorant/tennis/basketball -- confirmed
  no "Исход (X)" draw cell ever appears for these. If one ever does (unexpected), the event
  is skipped rather than mis-modeled as 2-way, same policy as every other provider here.

Known limitation: these are the HOME PAGE's curated "hot events" widgets, capped at ~15 rows
(7 for basketball) -- NOT the full line. The real full-line pages (`/prematch/<sport>`) are a
client-rendered SPA shell with no embedded data, and load Yandex SmartCaptcha, so they were
deliberately not pursued -- the tradeoff was to accept partial coverage (a handful of live
matches per game, all cybersport titles sharing one shared 15-row cap) rather than risk
running into a CAPTCHA wall chasing the full board.

Known gap: match start time isn't extracted (only separate day/time text nodes, no single
parseable attribute), so every quote here gets an empty start_time_utc, same tradeoff already
accepted for Marathon (bot/core/reconcile.py buckets by time only when available and falls
back to fuzzy team-name matching otherwise).
"""
from __future__ import annotations

import httpx
from bs4 import BeautifulSoup

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BOOKMAKER = "baltbet"
BASE_URL = "https://baltbet.ru"

WIDGET_PATHS = {
    "tennis": "/widgets/tennis/update/line",
    "basketball": "/widgets/basketball/update/line",
    "cybersport": "/widgets/cybersport/update/line",
}

CYBERSPORT_SLUG_TO_GAME = {
    "counter-strike": "cs2",
    "dota-2": "dota2",
    "league-of-legends": "lol",
    "valorant": "valorant",
}

TEAM_SEPARATOR = " — "  # em dash, used site-wide between team/player names


class BaltbetProvider(OddsProvider):
    def __init__(self, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20.0)

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        widgets = set()
        if "tennis" in games:
            widgets.add("tennis")
        if "basketball" in games:
            widgets.add("basketball")
        if any(g in games for g in CYBERSPORT_SLUG_TO_GAME.values()):
            widgets.add("cybersport")

        quotes: list[SourceQuote] = []
        for widget in widgets:
            resp = await self._client.get(WIDGET_PATHS[widget])
            resp.raise_for_status()
            quotes.extend(parse_widget_html(resp.text, widget, games))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_widget_html(html: str, widget: str, wanted_games: list[str]) -> list[SourceQuote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[SourceQuote] = []

    for row in soup.find_all("div", class_="events-table__body-row"):
        if row.get("data-islive") != "false":
            continue  # pre-match only; live in-play odds move too fast for this loop

        link = row.find("a", href=True)
        if link is None:
            continue
        path_parts = [p for p in link["href"].split("/") if p]

        if widget == "cybersport":
            game = CYBERSPORT_SLUG_TO_GAME.get(path_parts[1]) if len(path_parts) > 1 else None
        else:
            game = widget
        if game is None or game not in wanted_games:
            continue

        title = row.get("data-title", "")
        if TEAM_SEPARATOR not in title:
            continue
        team_a, team_b = title.split(TEAM_SEPARATOR, 1)

        odds_by_side: dict[str, str] = {}
        for cell in row.find_all("div", class_="m-market-0"):
            label = cell.get("data-title", "")
            value = cell.get("data-coefvalue")
            if "(1)" in label:
                odds_by_side["1"] = value
            elif "(2)" in label:
                odds_by_side["2"] = value
            elif "(X)" in label:
                odds_by_side["X"] = value

        if "X" in odds_by_side or "1" not in odds_by_side or "2" not in odds_by_side:
            continue  # unexpected 3-way market -- skip rather than mis-model as 2-way
        try:
            odds_a, odds_b = float(odds_by_side["1"]), float(odds_by_side["2"])
        except (TypeError, ValueError):
            continue
        if not odds_a or not odds_b:
            continue

        quotes.append(SourceQuote(game, team_a, team_b, "", BOOKMAKER, team_a, odds_a))
        quotes.append(SourceQuote(game, team_a, team_b, "", BOOKMAKER, team_b, odds_b))

    return quotes
