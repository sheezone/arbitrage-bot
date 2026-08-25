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


def test_match_under_24h_passes_when_that_bucket_selected():
    start = (NOW + timedelta(hours=12)).isoformat()
    assert within_time_horizon(start, [1], NOW)


def test_match_under_24h_filtered_when_only_over_24h_selected():
    start = (NOW + timedelta(hours=12)).isoformat()
    assert not within_time_horizon(start, [2], NOW)


def test_match_over_24h_passes_when_that_bucket_selected():
    start = (NOW + timedelta(days=10)).isoformat()
    assert within_time_horizon(start, [2], NOW)


def test_match_over_24h_filtered_when_only_under_24h_selected():
    start = (NOW + timedelta(days=10)).isoformat()
    assert not within_time_horizon(start, [1], NOW)


def test_match_exactly_at_24h_boundary_counts_as_under():
    start = (NOW + timedelta(hours=24)).isoformat()
    assert within_time_horizon(start, [1], NOW)
    assert not within_time_horizon(start, [2], NOW)


def test_both_buckets_selected_passes_regardless_of_timing():
    soon = (NOW + timedelta(hours=1)).isoformat()
    later = (NOW + timedelta(days=30)).isoformat()
    assert within_time_horizon(soon, [1, 2], NOW)
    assert within_time_horizon(later, [1, 2], NOW)


def test_unknown_start_time_is_not_filtered():
    assert within_time_horizon("", [1], NOW)


def test_unparseable_start_time_is_not_filtered():
    assert within_time_horizon("garbage", [1], NOW)
