import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.reconcile import group_quotes, normalize_team, split_by_market, to_arbitrage_input
from bot.providers.models import SourceQuote


def test_normalize_team_collapses_case_and_punctuation():
    assert normalize_team("Team  Liquid") == "team liquid"
    assert normalize_team("Team-Liquid!!") == "team liquid"
    assert normalize_team("  Spirit  ") == "spirit"


def test_group_quotes_merges_same_match_across_sources():
    start = "2026-07-30T08:00:00.000Z"
    quotes = [
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Team Liquid", 1.65),
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Team Spirit", 2.18),
        # Different case/spacing from another source, same real match, close start time.
        SourceQuote("cs2", "team liquid", "team spirit", "2026-07-30T08:05:00.000Z", "bet365", "team liquid", 1.80),
        SourceQuote("cs2", "team liquid", "team spirit", "2026-07-30T08:05:00.000Z", "bet365", "team spirit", 2.05),
        # Unrelated match must stay separate.
        SourceQuote("cs2", "Astralis", "MOUZ", start, "fonbet", "Astralis", 1.90),
        SourceQuote("cs2", "Astralis", "MOUZ", start, "fonbet", "MOUZ", 1.90),
    ]

    groups = group_quotes(quotes)
    assert len(groups) == 2
    sizes = sorted(len(g) for g in groups)
    assert sizes == [2, 4]


def test_group_quotes_merges_transliterated_cyrillic_tennis_names():
    start = "2026-07-30T08:00:00.000Z"
    quotes = [
        SourceQuote("tennis", "Джокович Н.", "Алькарас К.", start, "pari", "Джокович Н.", 1.90),
        SourceQuote("tennis", "Джокович Н.", "Алькарас К.", start, "pari", "Алькарас К.", 1.95),
        SourceQuote("tennis", "Djokovic N.", "Alcaraz C.", start, "bet365", "Djokovic N.", 1.85),
        SourceQuote("tennis", "Djokovic N.", "Alcaraz C.", start, "bet365", "Alcaraz C.", 2.05),
    ]

    groups = group_quotes(quotes)
    assert len(groups) == 1
    assert len(groups[0]) == 4


def test_to_arbitrage_input_remaps_outcome_names_onto_canonical_labels():
    start = "2026-07-30T08:00:00.000Z"
    group = [
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Team Liquid", 1.65),
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Team Spirit", 2.18),
        SourceQuote("cs2", "team liquid", "team spirit", start, "bet365", "team liquid", 1.80),
        SourceQuote("cs2", "team liquid", "team spirit", start, "bet365", "team spirit", 2.05),
    ]

    team_a, team_b, odds_by_outcome = to_arbitrage_input(group)
    assert team_a == "Team Liquid"
    assert team_b == "Team Spirit"
    assert {o.bookmaker for o in odds_by_outcome[team_a]} == {"fonbet", "bet365"}
    assert {o.bookmaker for o in odds_by_outcome[team_b]} == {"fonbet", "bet365"}


def test_to_arbitrage_input_ignores_unmatched_outcome_name():
    start = "2026-07-30T08:00:00.000Z"
    group = [
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Team Liquid", 1.65),
        SourceQuote("cs2", "Team Liquid", "Team Spirit", start, "fonbet", "Someone Else", 9.99),
    ]

    team_a, team_b, odds_by_outcome = to_arbitrage_input(group)
    assert len(odds_by_outcome[team_a]) == 1
    assert len(odds_by_outcome[team_b]) == 0


def test_split_by_market_separates_different_lines():
    start = "2026-07-30T08:00:00.000Z"
    group = [
        SourceQuote("football", "Arsenal", "Chelsea", start, "fonbet", "Тотал больше 2.5", 1.9, "total_2.5"),
        SourceQuote("football", "Arsenal", "Chelsea", start, "fonbet", "Тотал меньше 2.5", 1.95, "total_2.5"),
        SourceQuote("football", "Arsenal", "Chelsea", start, "marathon", "Тотал больше 3.5", 2.3, "total_3.5"),
        SourceQuote("football", "Arsenal", "Chelsea", start, "marathon", "Тотал меньше 3.5", 1.6, "total_3.5"),
    ]
    by_market = split_by_market(group)
    assert set(by_market.keys()) == {"total_2.5", "total_3.5"}
    assert len(by_market["total_2.5"]) == 2
    assert len(by_market["total_3.5"]) == 2


def test_to_arbitrage_input_groups_non_winner_market_by_literal_outcome_name():
    start = "2026-07-30T08:00:00.000Z"
    group = [
        SourceQuote("football", "Arsenal", "Chelsea", start, "fonbet", "Тотал больше 2.5", 1.9, "total_2.5"),
        SourceQuote("football", "Arsenal", "Chelsea", start, "marathon", "Тотал меньше 2.5", 2.1, "total_2.5"),
    ]
    team_a, team_b, odds_by_outcome = to_arbitrage_input(group)
    assert team_a == "Arsenal"
    assert team_b == "Chelsea"
    assert set(odds_by_outcome.keys()) == {"Тотал больше 2.5", "Тотал меньше 2.5"}
    assert odds_by_outcome["Тотал больше 2.5"][0].bookmaker == "fonbet"
    assert odds_by_outcome["Тотал меньше 2.5"][0].bookmaker == "marathon"
