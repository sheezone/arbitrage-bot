"""Marathonbet (marathonbet.ru) -- unlike the SPA-style sources, this one is plain
server-rendered HTML: a bare `httpx.get()` on a category page (no JS, no CAPTCHA)
returns the odds table already baked in.

Verified live 2026-08-03. Each match is a `<div class="bg coupon-row"
data-event-eventid="..." data-event-path="...">` containing:
- a hidden `<td data-mutable-id="eventJsonInfo" data-json='{"teamNames": [...]}'>` with
  the two team/player names (Cyrillic for tennis, already-Latin for esports team names);
- exactly two `<td data-market-type="RESULT_2WAY" data-sel='{"epr": "1.51", ...}'>` cells
  -- `epr` is the decimal price for the 2-way match-winner market, in team order.

Esports events additionally encode which specific title in the first segment of
`data-event-path` (e.g. "Dota+2", "Counter-Strike+2", "LoL"), so one fetch of the combined
e-Sports category page covers cs2/dota2/lol at once; tennis is its own category page.

Basketball (added 2026-08-04) is also its own category page and, like tennis, is a single
game per page (no per-title split needed) -- confirmed its match-winner market is the same
`RESULT_2WAY` shape as tennis/esports. Hockey deliberately has NO entry: it only exposes a
3-way `RESULT` market (draw possible) plus `DOUBLE_CHANCE` on this site, never a clean 2-way
winner market, so it doesn't fit this provider's 2-way-only pipeline. Valorant also has no
entry: not observed as a segment on the live e-Sports page at time of writing (only Dota 2/
CS2/LoL were present), so there's nothing confirmed to map yet.

Football (added 2026-08-20) IS wired up, but via the `TOTAL` market (match goals total,
Over/Under a line) rather than `RESULT`/`RESULT_2WAY` -- a goal total is exactly two-way
regardless of whether the match itself can draw. Each `<td data-market-type="TOTAL">` cell
nests a `<span data-selection-key="<eventId>@Total_Goals0.Over_2.5">` (or `Under_...`) --
confirmed live this is literally labelled "Total_Goals", not corners/cards/bookings, and
`Total_Goals0` (vs `Total_Goals1`/`Total_Goals2` for individual-team totals, not used here)
is the match total. The line itself isn't in the JSON payload (`data-sel`), only in this
selection key, so it's extracted from there via regex.

Known gap: match start time isn't extracted (only a same-day "HH:MM" with no date is
shown in the markup), so every quote here gets an empty start_time_utc. bot/core/reconcile.py
buckets by time to avoid false cross-source merges -- without it, Marathon quotes only rely
on the fuzzy team-name match to avoid merging into the wrong match, which is a real but
minor risk (two genuinely different fixtures would need near-identical team-name pairs to
collide).
"""
from __future__ import annotations

import json
import re
from urllib.parse import unquote_plus

import httpx
from bs4 import BeautifulSoup

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

BOOKMAKER = "marathon"

CATEGORY_URLS = {
    "tennis": "https://www.marathonbet.ru/su/betting/Tennis",
    "esports": "https://www.marathonbet.ru/su/betting/e-Sports+-+1895085",
    "basketball": "https://www.marathonbet.ru/su/betting/Basketball",
    "football": "https://www.marathonbet.ru/su/betting/Football",
}

TOTAL_GOALS_KEY_RE = re.compile(r"Total_Goals0\.(Over|Under)_([\d.]+)")
PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal totals; guards against mismatched markets

ESPORTS_PATH_SEGMENT_TO_GAME = {
    "Dota 2": "dota2",
    "Counter-Strike 2": "cs2",
    "LoL": "lol",
}


class MarathonProvider(OddsProvider):
    def __init__(self):
        self._client = httpx.AsyncClient(headers={"User-Agent": "Mozilla/5.0"}, timeout=20.0)

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        categories = set()
        if "tennis" in games:
            categories.add("tennis")
        if any(g in games for g in ("cs2", "dota2", "lol")):
            categories.add("esports")
        if "basketball" in games:
            categories.add("basketball")
        if "football" in games:
            categories.add("football")

        quotes: list[SourceQuote] = []
        for category in categories:
            resp = await self._client.get(CATEGORY_URLS[category])
            resp.raise_for_status()
            quotes.extend(parse_category_page(resp.text, category, games))
        return quotes

    async def close(self) -> None:
        await self._client.aclose()


def parse_category_page(html: str, category: str, wanted_games: list[str]) -> list[SourceQuote]:
    soup = BeautifulSoup(html, "html.parser")
    quotes: list[SourceQuote] = []

    for event in soup.find_all("div", attrs={"data-event-eventid": True}):
        path = event.get("data-event-path", "")
        if category in ("tennis", "basketball", "football"):
            game = category
        else:
            parts = unquote_plus(path).split("/")
            segment = parts[1] if len(parts) > 1 else ""
            game = ESPORTS_PATH_SEGMENT_TO_GAME.get(segment)
        if game is None or game not in wanted_games:
            continue

        json_td = event.find("td", attrs={"data-mutable-id": "eventJsonInfo"})
        if json_td is None or not json_td.get("data-json"):
            continue
        try:
            info = json.loads(json_td["data-json"])
            team_a, team_b = info["teamNames"]
        except (KeyError, ValueError, json.JSONDecodeError):
            continue

        if game == "football":
            quotes.extend(_parse_football_totals(event, team_a, team_b))
            continue

        price_tds = event.find_all("td", attrs={"data-market-type": "RESULT_2WAY"})
        if len(price_tds) != 2:
            continue
        try:
            prices = [float(json.loads(td["data-sel"])["epr"]) for td in price_tds]
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
        if not all(prices):
            continue

        quotes.append(SourceQuote(game, team_a, team_b, "", BOOKMAKER, team_a, prices[0]))
        quotes.append(SourceQuote(game, team_a, team_b, "", BOOKMAKER, team_b, prices[1]))

    return quotes


def _parse_football_totals(event, team_a: str, team_b: str) -> list[SourceQuote]:
    """Extract the match Total goals (Over/Under) market -- see module docstring for why
    this, not RESULT_2WAY, is what makes football fit the 2-way-only pipeline."""
    parsed: list[tuple[str, float, float]] = []  # (direction, line, price)
    for td in event.find_all("td", attrs={"data-market-type": "TOTAL"}):
        span = td.find("span", attrs={"data-selection-key": True})
        if span is None:
            continue
        match = TOTAL_GOALS_KEY_RE.search(span["data-selection-key"])
        if match is None:
            continue
        direction, line_str = match.groups()
        try:
            price = float(json.loads(td["data-sel"])["epr"])
            line = float(line_str)
        except (KeyError, ValueError, json.JSONDecodeError):
            continue
        if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
            continue
        parsed.append((direction, line, price))

    overs = [p for p in parsed if p[0] == "Over"]
    unders = [p for p in parsed if p[0] == "Under"]
    if len(overs) != 1 or len(unders) != 1:
        return []
    _, line_over, price_over = overs[0]
    _, line_under, price_under = unders[0]
    if line_over != line_under:
        return []

    market = f"total_{line_over}"
    return [
        SourceQuote("football", team_a, team_b, "", BOOKMAKER, f"Тотал больше {line_over}", price_over, market),
        SourceQuote("football", team_a, team_b, "", BOOKMAKER, f"Тотал меньше {line_over}", price_under, market),
    ]
