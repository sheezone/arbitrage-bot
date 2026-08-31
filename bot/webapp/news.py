"""Real news headlines for a handful of "popular" upcoming matches -- deliberately NOT
predictions/percentages. See the conversation that led to this: the user originally asked
for AI-generated win-probability percentages factoring in things like a player's divorce
or injury news. That was declined -- presenting fabricated-precision numbers as if they
were rigorous analysis, for a paid tool people bet real money through, is a way to
mislead people into risky bets on made-up confidence. This module gives the honest
version instead: real headlines (injuries, suspensions, form, transfers, ...) for the
human to read and judge themselves, no invented odds/probabilities anywhere.

Source: Google News RSS (https://news.google.com/rss/search?q=...) -- free, no API key,
no signup. Not sports-specific (it's general news search), so results are filtered to the
last NEWS_LOOKBACK_HOURS and deliberately not deduplicated/reranked beyond what Google's
own relevance sort already does.

"Popular matches" is a real limitation worth being upfront about: this bot doesn't track a
full sports fixture calendar independent of arbitrage detection (see bot/core/monitor.py --
LatestState.matches only ever holds matches an arb was actually found for). So the pool
this picks from is "whatever's currently a found arb", not every match happening in the
next 24h -- POPULAR_TEAMS scoring just picks the recognizable-name matches out of that
pool, it doesn't go find matches beyond it."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import httpx

from bot.core.state import MatchSnapshot

NEWS_LOOKBACK_HOURS = 24
GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
MAX_HEADLINES_PER_MATCH = 5
_REQUEST_TIMEOUT = 10.0

# Well-known club/national-team names likely to be recognized and searched for by a
# Russian-speaking audience -- deliberately broad (RPL + the usual big European/national
# teams) rather than exhaustive; a match not in here still gets picked if there simply
# aren't 3 "popular" ones available (see pick_popular_matches).
POPULAR_TEAMS = {
    "спартак", "цска", "зенит", "динамо", "локомотив", "краснодар", "ростов", "рубин",
    "реал мадрид", "реал", "барселона", "атлетико", "манчестер юнайтед", "манчестер сити",
    "ливерпуль", "челси", "арсенал", "тоттенхэм", "бавария", "боруссия дортмунд", "псж",
    "ювентус", "милан", "интер", "наполи", "рома", "аякс", "порту", "бенфика",
    "россия", "бразилия", "аргентина", "франция", "германия", "испания", "англия",
    "португалия", "италия",
}


def _is_popular(team_a: str, team_b: str) -> bool:
    names = f"{team_a} {team_b}".lower()
    return any(popular in names for popular in POPULAR_TEAMS)


def pick_popular_matches(matches: list[MatchSnapshot], limit: int = 3) -> list[MatchSnapshot]:
    """Prefers recognizable-name matches, soonest first; pads out with the soonest
    remaining matches if fewer than `limit` popular ones are currently available at all
    (see module docstring for why the pool itself is limited to already-found arbs)."""
    popular = [m for m in matches if _is_popular(m.team_a, m.team_b)]
    rest = [m for m in matches if m not in popular]
    popular.sort(key=lambda m: m.start_time_utc or "9999")
    rest.sort(key=lambda m: m.start_time_utc or "9999")
    return (popular + rest)[:limit]


def _parse_pub_date(raw: str | None) -> float:
    if not raw:
        return 0.0
    try:
        return parsedate_to_datetime(raw).timestamp()
    except (TypeError, ValueError):
        return 0.0


async def fetch_team_news(client: httpx.AsyncClient, team_a: str, team_b: str) -> list[dict]:
    """Real headlines for one match, newest first, capped at MAX_HEADLINES_PER_MATCH and
    to the last NEWS_LOOKBACK_HOURS. Returns [] on any failure (network, malformed feed,
    no results) rather than raising -- one match's news being unavailable shouldn't break
    the other two."""
    query = f"{team_a} {team_b}"
    try:
        resp = await client.get(
            GOOGLE_NEWS_RSS,
            params={"q": query, "hl": "ru", "gl": "RU", "ceid": "RU:ru"},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
    except (httpx.HTTPError, ET.ParseError):
        return []

    cutoff = time.time() - NEWS_LOOKBACK_HOURS * 3600
    items = []
    for item in root.findall("./channel/item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub_date_raw = item.findtext("pubDate")
        pub_ts = _parse_pub_date(pub_date_raw)
        if not title or not link or pub_ts < cutoff:
            continue
        source_el = item.find("source")
        items.append({
            "title": title,
            "link": link,
            "source": source_el.text if source_el is not None else "",
            "published_at": pub_ts,
        })

    items.sort(key=lambda x: x["published_at"], reverse=True)
    return items[:MAX_HEADLINES_PER_MATCH]
