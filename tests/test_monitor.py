import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
from bot.core.monitor import (
    EMOJI_ALERT,
    _format_message,
    format_match_start,
    tg_emoji,
    user_allows_arb,
    within_time_horizon,
)


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


_BEST_ODDS = [OutcomeOdds("A", "fonbet", 2.1), OutcomeOdds("B", "olimpbet", 2.05)]


def test_empty_allowed_bookmakers_means_no_restriction():
    assert user_allows_arb([], _BEST_ODDS)


def test_arb_allowed_when_every_leg_is_at_an_allowed_bookmaker():
    assert user_allows_arb(["fonbet", "olimpbet"], _BEST_ODDS)


def test_arb_blocked_when_any_leg_is_at_a_disallowed_bookmaker():
    assert not user_allows_arb(["fonbet"], _BEST_ODDS)


def test_bookmaker_matching_is_case_insensitive():
    assert user_allows_arb(["FONBET", "OLIMPBET"], _BEST_ODDS)


def test_tg_emoji_renders_the_expected_html_tag():
    assert tg_emoji(EMOJI_ALERT) == '<tg-emoji emoji-id="5440660757194744323">🚨</tg-emoji>'


def test_format_message_includes_the_alert_emoji_tag():
    arb = ArbitrageResult(best_odds=_BEST_ODDS, arb_ratio=0.95, profit_pct=5.0)
    text = _format_message("football", "Team A", "Team B", arb)
    assert tg_emoji(EMOJI_ALERT) in text


def test_format_message_uses_high_profit_emoji_above_the_recheck_threshold():
    from bot.core.monitor import EMOJI_HIGH_PROFIT, HIGH_PROFIT_RECHECK_THRESHOLD

    high_arb = ArbitrageResult(best_odds=_BEST_ODDS, arb_ratio=0.8, profit_pct=HIGH_PROFIT_RECHECK_THRESHOLD + 1)
    text = _format_message("football", "Team A", "Team B", high_arb)
    assert tg_emoji(EMOJI_HIGH_PROFIT) in text
    assert "🚀 Прибыль" not in text
