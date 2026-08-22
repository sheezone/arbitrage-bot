"""Melbet (sport.melbet.ru) -- unlike every other bookmaker here, Melbet's REST API
returns encrypted payloads. Confirmed live (2026-08-21): a bare HTTP GET to any of its
`.../prematch/...` or `.../live/...` endpoints returns `{"payload": "<opaque blob>"}` --
the real numbers are decrypted client-side via `Scripts/dec/decrypt.js` + a WASM module
loaded on every page. This is why bot/providers/baltbet.py's module docstring lists
Melbet as unreachable directly: that investigation only tried bare HTTP.

A real browser executes that decryption transparently, so this provider drives one via
Playwright instead of httpx -- the one source in this codebase that needs it. Found live
that the page's own JS already exposes a clean, already-decrypted data-fetching layer on
`window.$httpApi`: calling `$httpApi.getTopEventsList(sportId, stakeTypeIds, langId,
partnerId, countryCode)` from inside the page returns fully decrypted JSON (team names in
Russian and English, kickoff time, and every requested stake type with its odds) without
needing to reverse-engineer their WASM encryption at all -- their own code does the work,
we just read the result. This is a "top events" list (~69 for football at check time),
not the exhaustive per-league line other sources scrape here -- similar limitation to
baltbet.py's "hot events" widget.

Football and hockey use the "Тотал" (Total goals/pucks) stake type (Id=3) rather than
"Исход" (1X2, Id=1), for the same reason as everywhere else in this codebase: both allow
a draw in regulation time, but a goals/pucks total is exactly two-way. See
bot/providers/_line_platform.py's module docstring for the fuller rationale -- this
mirrors that decision on an independent bookmaker. Unlike Fonbet (which offers one
dynamic "closest to fair" line per match), Melbet's Total group lists several lines at
once (e.g. 1.5, 2, 2.5, 3...) -- all of them plausible are emitted as separate quotes
(one market per line), so whichever line another source happens to be offering for the
same match still has a chance to find a match here.

One persistent Chromium page is launched once (on first fetch_quotes call) and reused
across every poll -- launching a fresh browser per cycle would be far too heavy for this
VPS. Still meaningfully heavier than every other source here (a real browser vs plain
HTTP/HTML): this is the deliberate trade-off for reaching a bookmaker no other approach
here could. If the browser process dies or memory pressure becomes a real problem on the
production VPS, that's the signal to drop this provider rather than chase it further.
"""
from __future__ import annotations

import logging

from bot.providers.base import OddsProvider
from bot.providers.models import SourceQuote

logger = logging.getLogger(__name__)

BASE_URL = "https://sport.melbet.ru"
PARTNER_ID = 3000057
LANG_ID = 1

SPORT_IDS = {
    "football": 1,
    "hockey": 10,
}

WIN_STAKE_TYPE_ID = 1
TOTAL_STAKE_TYPE_ID = 3
# Ask for a handful of common markets (not just Total) -- costs nothing extra per request
# and keeps the door open for adding more games/markets later without touching the call.
REQUESTED_STAKE_TYPES = [WIN_STAKE_TYPE_ID, 702, 2, TOTAL_STAKE_TYPE_ID, 992, 46]

PLAUSIBLE_TOTAL_LINE_RANGE = (0.5, 8.5)  # real match goal/puck totals; guards against garbage entries

_FETCH_JS = (
    "([sportId, stakeTypes, langId, partnerId]) => "
    "window.$httpApi.getTopEventsList(sportId, stakeTypes, langId, partnerId, '')"
)


class MelbetProvider(OddsProvider):
    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None

    async def _ensure_page(self):
        if self._page is not None:
            return self._page
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage"]
        )
        self._page = await self._browser.new_page()
        await self._page.goto(BASE_URL, wait_until="domcontentloaded", timeout=30000)
        return self._page

    async def _reset(self) -> None:
        """Confirmed live: discarding just `self._page` on a failed evaluate() and
        leaving `self._browser`/`self._playwright` in place meant the next cycle's
        `_ensure_page()` launched a whole SECOND browser on top of the first (only
        `self._page` was checked) -- a full Chromium process leaked every failed cycle,
        1GB+ and 70+ processes within minutes on the production VPS. Close everything
        before dropping the references so the next cycle starts genuinely clean."""
        try:
            await self.close()
        except Exception:
            logger.exception("Melbet: error while closing browser during reset")
        self._page = None
        self._browser = None
        self._playwright = None

    async def fetch_quotes(self, games: list[str]) -> list[SourceQuote]:
        wanted = [g for g in games if g in SPORT_IDS]
        if not wanted:
            return []

        try:
            page = await self._ensure_page()
        except Exception:
            logger.exception("Melbet: failed to launch/attach browser page")
            await self._reset()
            return []

        quotes: list[SourceQuote] = []
        for game in wanted:
            try:
                raw_events = await page.evaluate(
                    _FETCH_JS, [SPORT_IDS[game], REQUESTED_STAKE_TYPES, LANG_ID, PARTNER_ID]
                )
            except Exception:
                logger.exception("Melbet: fetch failed for %s -- resetting browser for next cycle", game)
                await self._reset()
                break  # the page/browser is gone -- no point trying the remaining games this cycle
            quotes.extend(parse_events(game, raw_events or []))
        return quotes

    async def close(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()


def parse_events(game: str, raw_events: list[dict]) -> list[SourceQuote]:
    quotes: list[SourceQuote] = []
    for event in raw_events:
        team_a, team_b = event.get("HT"), event.get("AT")
        if not team_a or not team_b:
            continue
        start_time_utc = event.get("D") or ""

        total_group = next((st for st in event.get("StakeTypes") or [] if st.get("Id") == TOTAL_STAKE_TYPE_ID), None)
        if total_group is None:
            continue

        by_line: dict[float, dict[str, float]] = {}
        for stake in total_group.get("Stakes") or []:
            line, odds, direction = stake.get("A"), stake.get("F"), stake.get("N")
            if line is None or not odds or direction not in ("Больше", "Меньше"):
                continue
            line = float(line)
            if not (PLAUSIBLE_TOTAL_LINE_RANGE[0] <= line <= PLAUSIBLE_TOTAL_LINE_RANGE[1]):
                continue
            by_line.setdefault(line, {})[direction] = float(odds)

        for line, sides in by_line.items():
            over, under = sides.get("Больше"), sides.get("Меньше")
            if not over or not under:
                continue
            market = f"total_{line}"
            quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "melbet", f"Тотал больше {line}", over, market))
            quotes.append(SourceQuote(game, team_a, team_b, start_time_utc, "melbet", f"Тотал меньше {line}", under, market))

    return quotes
