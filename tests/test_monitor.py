import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
from bot.core.monitor import (
    EMOJI_ALERT,
    MIN_DISPLAYABLE_PROFIT_PCT,
    _evaluate_group,
    _format_message,
    _showcase_key,
    format_match_start,
    tg_emoji,
    user_allows_arb,
    within_time_horizon,
)
from bot.core.state import MatchSnapshot
from bot.providers.models import SourceQuote


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
    # Multi-glyph fallback text (e.g. two emoji in one captured ID) makes Telegram reject
    # the tag outright (ENTITY_TEXT_INVALID) -- confirmed live 2026-08-28, broke the search
    # screen in production. tg_emoji() itself is still fine; it's *which* IDs get used
    # (single-glyph fallback only) that matters. See bot/core/monitor.py's EMOJI_* comment.
    assert tg_emoji(EMOJI_ALERT) == '<tg-emoji emoji-id="5440660757194744323">🚨</tg-emoji>'


def test_format_message_still_uses_plain_emoji_not_yet_reintroduced_animated_ones():
    """Animated emoji were reverted live 2026-08-28 (ENTITY_TEXT_INVALID broke the search
    screen) -- this guards against silently reintroducing a broken tg-emoji tag into
    _format_message without it being a deliberate, tested change."""
    arb = ArbitrageResult(best_odds=_BEST_ODDS, arb_ratio=0.95, profit_pct=5.0)
    text = _format_message("football", "Team A", "Team B", arb)
    assert "<tg-emoji" not in text
    assert "🚀 Прибыль" in text


def _snapshot(odds_a: float, odds_b: float) -> MatchSnapshot:
    best_odds = [OutcomeOdds("Team A", "fonbet", odds_a), OutcomeOdds("Team B", "olimpbet", odds_b)]
    arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=10.0)
    return MatchSnapshot("football", "Team A", "Team B", arb, "2026-08-29T20:00:00+00:00")


def test_showcase_key_ignores_odds_so_a_price_wiggle_isnt_treated_as_a_new_match():
    """Confirmed live 2026-08-29: keying on the odds hash made the same match repost
    every cycle its price shifted by even a cent -- identity here is the match/market
    only, not the current price."""
    assert _showcase_key(_snapshot(2.10, 2.05)) == _showcase_key(_snapshot(2.11, 2.06))


def test_showcase_key_differs_for_a_different_match():
    other = MatchSnapshot(
        "football", "Team C", "Team D",
        ArbitrageResult(best_odds=_BEST_ODDS, arb_ratio=0.9, profit_pct=10.0),
        "2026-08-29T20:00:00+00:00",
    )
    assert _showcase_key(_snapshot(2.10, 2.05)) != _showcase_key(other)


def _quote_group(odds_a: float, odds_b: float) -> list[SourceQuote]:
    return [
        SourceQuote("football", "Team A", "Team B", "2026-08-29T20:00:00+00:00", "fonbet", "Team A", odds_a),
        SourceQuote("football", "Team A", "Team B", "2026-08-29T20:00:00+00:00", "olimpbet", "Team B", odds_b),
    ]


def test_evaluate_group_drops_arbs_below_the_min_displayable_floor():
    # ~0.25% profit -- a real arb, just below MIN_DISPLAYABLE_PROFIT_PCT (0.60%)
    assert _evaluate_group(_quote_group(2.005, 2.005)) == []


def test_evaluate_group_keeps_arbs_at_or_above_the_min_displayable_floor():
    # ~1% profit -- comfortably above the floor
    results = _evaluate_group(_quote_group(2.02, 2.02))
    assert len(results) == 1
    assert results[0][4].profit_pct >= MIN_DISPLAYABLE_PROFIT_PCT
