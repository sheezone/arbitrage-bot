import asyncio
import sys
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
from bot.core.state import MatchSnapshot
from bot.webapp.news import _is_popular, _parse_pub_date, fetch_team_news, pick_popular_matches

_ARB = ArbitrageResult(best_odds=[OutcomeOdds("A", "fonbet", 2.1), OutcomeOdds("B", "olimpbet", 2.05)], arb_ratio=0.9, profit_pct=5.0)


def _match(team_a: str, team_b: str, start: str = "2026-08-31T18:00:00+00:00") -> MatchSnapshot:
    return MatchSnapshot("football", team_a, team_b, _ARB, start)


def test_is_popular_matches_known_club_names_case_insensitively():
    assert _is_popular("Реал Мадрид", "Барселона")
    assert _is_popular("реал мадрид", "неизвестная команда")


def test_is_popular_false_for_unknown_teams():
    assert not _is_popular("ФК Ноунейм", "Другой Клуб")


def test_pick_popular_matches_prefers_recognizable_names():
    matches = [
        _match("ФК Икс", "ФК Игрек", "2026-08-31T10:00:00+00:00"),
        _match("Реал Мадрид", "Барселона", "2026-08-31T20:00:00+00:00"),
    ]
    picked = pick_popular_matches(matches, limit=1)
    assert picked == [matches[1]]


def test_pick_popular_matches_pads_with_unpopular_when_not_enough():
    matches = [_match("ФК Икс", "ФК Игрек"), _match("Реал Мадрид", "Барселона")]
    picked = pick_popular_matches(matches, limit=2)
    assert len(picked) == 2


def test_pick_popular_matches_sorts_soonest_first_within_each_group():
    later = _match("Реал Мадрид", "Барселона", "2026-09-01T20:00:00+00:00")
    sooner = _match("Ливерпуль", "Челси", "2026-08-31T10:00:00+00:00")
    picked = pick_popular_matches([later, sooner], limit=2)
    assert picked == [sooner, later]


def test_parse_pub_date_handles_rfc822():
    now = time.time()
    raw = format_datetime(datetime.fromtimestamp(now, tz=timezone.utc))
    assert abs(_parse_pub_date(raw) - now) < 2


def test_parse_pub_date_returns_zero_for_garbage():
    assert _parse_pub_date("not a date") == 0.0
    assert _parse_pub_date(None) == 0.0


_RSS_TEMPLATE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title>{title}</title><link>{link}</link><pubDate>{pub_date}</pubDate><source url="https://example.com">Example</source></item>
</channel></rss>"""


def test_fetch_team_news_returns_recent_items(monkeypatch):
    recent = format_datetime(datetime.now(tz=timezone.utc))
    xml = _RSS_TEMPLATE.format(title="Игрок травмирован перед матчем", link="https://example.com/a", pub_date=recent)

    async def fake_get(self, url, params=None, timeout=None):
        return httpx.Response(200, text=xml, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_team_news(client, "Реал Мадрид", "Барселона")

    items = asyncio.run(run())
    assert len(items) == 1
    assert items[0]["title"] == "Игрок травмирован перед матчем"
    assert items[0]["source"] == "Example"


def test_fetch_team_news_drops_stale_items(monkeypatch):
    old = format_datetime(datetime(2020, 1, 1, tzinfo=timezone.utc))
    xml = _RSS_TEMPLATE.format(title="Старая новость", link="https://example.com/b", pub_date=old)

    async def fake_get(self, url, params=None, timeout=None):
        return httpx.Response(200, text=xml, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_team_news(client, "A", "B")

    assert asyncio.run(run()) == []


def test_fetch_team_news_returns_empty_on_http_error(monkeypatch):
    async def fake_get(self, url, params=None, timeout=None):
        raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_team_news(client, "A", "B")

    assert asyncio.run(run()) == []


def test_fetch_team_news_returns_empty_on_malformed_xml(monkeypatch):
    async def fake_get(self, url, params=None, timeout=None):
        return httpx.Response(200, text="not xml at all", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async def run():
        async with httpx.AsyncClient() as client:
            return await fetch_team_news(client, "A", "B")

    assert asyncio.run(run()) == []
