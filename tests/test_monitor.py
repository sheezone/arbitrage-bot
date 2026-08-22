import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.monitor import format_match_start


def test_formats_utc_time_as_moscow_time():
    assert format_match_start("2026-08-21T15:00:00+00:00") == "21 авг, 18:00 МСК"


def test_handles_z_suffix():
    assert format_match_start("2026-08-21T15:00:00Z") == "21 авг, 18:00 МСК"


def test_returns_empty_string_for_missing_time():
    assert format_match_start("") == ""


def test_returns_empty_string_for_unparseable_time():
    assert format_match_start("not a timestamp") == ""
