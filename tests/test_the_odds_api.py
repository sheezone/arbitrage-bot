import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.the_odds_api import parse_events


def test_parses_h2h_two_way_market():
    raw = [
        {
            "home_team": "Boston Celtics",
            "away_team": "Detroit Pistons",
            "commence_time": "2026-08-05T00:00:00Z",
            "bookmakers": [
                {
                    "key": "onexbet",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "Boston Celtics", "price": 1.8},
                                {"name": "Detroit Pistons", "price": 2.1},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    quotes = parse_events("basketball", raw)
    assert len(quotes) == 2
    assert {q.outcome_name for q in quotes} == {"Boston Celtics", "Detroit Pistons"}
    assert all(q.bookmaker == "onexbet" and q.game == "basketball" for q in quotes)
    assert quotes[0].start_time_utc == "2026-08-05T00:00:00Z"


def test_skips_market_with_unexpected_outcome_count():
    raw = [
        {
            "home_team": "A",
            "away_team": "B",
            "bookmakers": [
                {
                    "key": "onexbet",
                    "markets": [
                        {
                            "key": "h2h",
                            "outcomes": [
                                {"name": "A", "price": 1.8},
                                {"name": "Draw", "price": 3.4},
                                {"name": "B", "price": 2.1},
                            ],
                        }
                    ],
                }
            ],
        }
    ]
    assert parse_events("basketball", raw) == []


def test_ignores_non_h2h_markets():
    raw = [
        {
            "home_team": "A",
            "away_team": "B",
            "bookmakers": [
                {
                    "key": "onexbet",
                    "markets": [
                        {"key": "spreads", "outcomes": [{"name": "A", "price": 1.9}, {"name": "B", "price": 1.9}]}
                    ],
                }
            ],
        }
    ]
    assert parse_events("basketball", raw) == []


def test_skips_event_missing_team_names():
    raw = [{"home_team": None, "away_team": "B", "bookmakers": []}]
    assert parse_events("basketball", raw) == []
