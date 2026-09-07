import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.webapp.team_flags import get_team_flag


def test_known_russian_club_gets_russian_flag():
    assert get_team_flag("Спартак") == "🇷🇺"
    assert get_team_flag("Зенит") == "🇷🇺"


def test_known_spanish_club_gets_spanish_flag():
    assert get_team_flag("Реал Мадрид") == "🇪🇸"
    assert get_team_flag("Барселона") == "🇪🇸"


def test_english_club_gets_england_flag_not_generic_uk():
    flag = get_team_flag("Ливерпуль")
    assert flag is not None
    assert flag != "🇬🇧"


def test_national_team_gets_its_flag():
    assert get_team_flag("Бразилия") == "🇧🇷"


def test_unknown_team_returns_none():
    assert get_team_flag("Natus Vincere") is None
    assert get_team_flag("G2 Esports") is None


def test_match_is_case_insensitive():
    assert get_team_flag("СПАРТАК") == "🇷🇺"
