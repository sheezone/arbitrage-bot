import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.repository import Repository
from bot.handlers.commands import (
    LANG_BUTTON_TEXT,
    LANGUAGE_CHOICES,
    PROFILE_BUTTON_TEXT_EN,
    PROFILE_BUTTON_TEXT_RU,
    PROFILE_BUTTON_TEXT_TG,
    SEARCH_BUTTON_TEXT_EN,
    SEARCH_BUTTON_TEXT_RU,
    SEARCH_BUTTON_TEXT_TG,
    _dashboard_view,
    _language_menu_view,
    _main_menu_keyboard,
)


def _repo(tmp_path) -> Repository:
    return Repository(str(tmp_path / "test.sqlite3"))


def test_language_defaults_to_ru(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).language == "ru"


def test_new_user_has_not_chosen_language_yet(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).lang_chosen is False
    repo.set_lang_chosen(1)
    assert repo.get_user(1).lang_chosen is True


def test_set_language_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    for code in ("tg", "en", "ru"):
        repo.set_language(1, code)
        assert repo.get_user(1).language == code


def test_language_choices_are_ru_en_tg():
    assert list(LANGUAGE_CHOICES) == ["ru", "en", "tg"]


def test_main_menu_keyboard_is_just_search_and_profile(tmp_path):
    kb = _main_menu_keyboard("ru")
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert texts == [SEARCH_BUTTON_TEXT_RU, PROFILE_BUTTON_TEXT_RU]
    assert LANG_BUTTON_TEXT not in texts


def test_main_menu_keyboard_tajik_labels(tmp_path):
    kb = _main_menu_keyboard("tg")
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert texts == [SEARCH_BUTTON_TEXT_TG, PROFILE_BUTTON_TEXT_TG]


def test_language_menu_is_plain_buttons(tmp_path):
    # No "current" checkmark -- the menu is deleted the moment a choice is tapped.
    text, kb = _language_menu_view()
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    assert {b.callback_data for b in buttons} == {"lang:set:ru", "lang:set:en", "lang:set:tg"}
    assert not any(b.text.startswith("✅") for b in buttons)
    assert any("English" in b.text for b in buttons)


def test_main_menu_keyboard_english_labels(tmp_path):
    kb = _main_menu_keyboard("en")
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert texts == [SEARCH_BUTTON_TEXT_EN, PROFILE_BUTTON_TEXT_EN]


def test_dashboard_view_is_russian_by_default(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    text, _ = _dashboard_view(repo.get_user(1))
    assert "АРБИТРАЖНЫЙ БОТ" in text
    assert PROFILE_BUTTON_TEXT_RU in text


def test_dashboard_view_switches_to_tajik(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "tg")
    text, _ = _dashboard_view(repo.get_user(1))
    assert "БОТИ АРБИТРАЖӢ" in text
    assert PROFILE_BUTTON_TEXT_TG in text


def test_dashboard_view_switches_to_english(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "en")
    text, _ = _dashboard_view(repo.get_user(1))
    assert "ARBITRAGE BOT" in text
    assert PROFILE_BUTTON_TEXT_EN in text


def test_dashboard_view_locked_screen_english(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "en")
    past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    repo._conn.execute("UPDATE users SET trial_started_at = ? WHERE chat_id = ?", (past, 1))
    repo._conn.commit()
    text, keyboard = _dashboard_view(repo.get_user(1))
    assert "trial period is over" in text
    assert keyboard is None


def test_dashboard_view_locked_screen_is_translated_too(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "tg")
    # Force the trial to have already ended.
    user = repo.get_user(1)
    past = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    repo._conn.execute("UPDATE users SET trial_started_at = ? WHERE chat_id = ?", (past, 1))
    repo._conn.commit()

    text, keyboard = _dashboard_view(repo.get_user(1))
    assert "Давраи озмоишӣ ба охир расид" in text
    assert keyboard is None


def test_dashboard_view_admin_gets_unlimited_access_line_translated(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "tg")
    text, _ = _dashboard_view(repo.get_user(1), admin_chat_ids=frozenset({1}))
    assert "Дастрасии беохир" in text


def test_profile_view_has_a_language_button(tmp_path):
    from bot.handlers.commands import NAV_LANGUAGE, _profile_view

    repo = _repo(tmp_path)
    repo.upsert_user(1)
    _text, kb = _profile_view(repo.get_user(1))
    datas = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert NAV_LANGUAGE in datas
