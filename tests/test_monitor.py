import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.monitor import format_match_start, within_time_horizon


def test_formats_utc_time_as_moscow_time():
    assert format_match_start("2026-08-21T15:00:00+00:00") == "21 авг, 18:00 МСК"


def test_handles_z_suffix():
    assert format_match_start("2026-08-21T15:00:00Z") == "21 авг, 18:00 МСК"


def test_returns_empty_string_for_missing_time():
    assert format_match_start("") == ""


def test_returns_empty_string_for_unparseable_time():
    assert format_match_start("not a timestamp") == ""


NOW = datetime(2026, 8, 22, tzinfo=timezone.utc)


def test_match_within_horizon_passes():
    start = (NOW + timedelta(days=2)).isoformat()
    assert within_time_horizon(start, [3], NOW)


def test_match_beyond_horizon_is_filtered():
    start = (NOW + timedelta(days=10)).isoformat()
    assert not within_time_horizon(start, [3], NOW)


def test_match_exactly_at_horizon_boundary_passes():
    start = (NOW + timedelta(days=3)).isoformat()
    assert within_time_horizon(start, [3], NOW)


def test_unknown_start_time_is_not_filtered():
    assert within_time_horizon("", [1], NOW)


def test_unparseable_start_time_is_not_filtered():
    assert within_time_horizon("garbage", [1], NOW)


def test_uses_the_widest_of_multiple_selected_horizons():
    start = (NOW + timedelta(days=10)).isoformat()
    assert not within_time_horizon(start, [1, 3], NOW)
    assert within_time_horizon(start, [1, 30], NOW)
