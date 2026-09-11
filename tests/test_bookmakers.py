from bot.core.bookmakers import filter_licensed, is_licensed
from bot.providers.models import SourceQuote


def test_rf_licensed_operators_are_licensed():
    for key in ("fonbet", "pari", "winline", "leon", "marathon", "olimpbet", "betcity", "baltbet", "zenit"):
        assert is_licensed(key), key


def test_melbet_is_licensed():
    # ФНС license since 2012 (ООО «МЕЛОФОН»), ЦУПИС/ЕРАИ member -- the RF brand runs on
    # sport.melbet.ru/mel.bet, a different legal entity from the blocked international
    # melbet.com. An earlier version of this list wrongly excluded it (fixed 2026-09-11).
    assert is_licensed("melbet")


def test_foreign_unlicensed_bookmakers_are_not_licensed():
    for key in ("bet365", "1xbet", "22bet", "pinnacle"):
        assert not is_licensed(key), key


def test_is_licensed_is_case_and_whitespace_insensitive():
    assert is_licensed(" MELBET ")
    assert is_licensed("Fonbet")


def test_filter_licensed_drops_only_the_unlicensed_quotes():
    quotes = [
        SourceQuote("football", "A", "B", "", "melbet", "A", 2.0),
        SourceQuote("football", "A", "B", "", "bet365", "B", 2.0),
    ]
    kept = filter_licensed(quotes)
    assert [q.bookmaker for q in kept] == ["melbet"]
