import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.core.flags import national_flag, with_flag


def test_national_teams_get_flags_including_age_and_gender_suffixes():
    assert national_flag("Италия") == "🇮🇹"
    assert national_flag("Бельгия") == "🇧🇪"
    assert national_flag("Бразилия U21") == "🇧🇷"
    assert national_flag("Россия (ж)") == "🇷🇺"
    assert national_flag("Германия до 19") == "🇩🇪"
    assert national_flag("Англия") == "🏴\U000E0067\U000E0062\U000E0065\U000E006E\U000E0067\U000E007F"


def test_clubs_never_get_a_national_flag():
    for club in ["Динамо Минск", "Реал Бразилиа", "Спартак", "Сидней", "Зенит СПб"]:
        assert national_flag(club) is None


def test_with_flag_prefixes_only_national_teams():
    assert with_flag("Турция") == "🇹🇷 Турция"
    assert with_flag("ЦСКА") == "ЦСКА"
