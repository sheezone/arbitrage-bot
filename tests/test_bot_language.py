import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.repository import Repository
from bot.handlers.commands import (
    LANG_TOGGLE_TO_RU_TEXT,
    LANG_TOGGLE_TO_TG_TEXT,
    PROFILE_BUTTON_TEXT_RU,
    PROFILE_BUTTON_TEXT_TG,
    SEARCH_BUTTON_TEXT_RU,
    SEARCH_BUTTON_TEXT_TG,
    _dashboard_view,
    _main_menu_keyboard,
)


def _repo(tmp_path) -> Repository:
    return Repository(str(tmp_path / "test.sqlite3"))


def test_language_defaults_to_ru(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    assert repo.get_user(1).language == "ru"


def test_set_language_round_trips(tmp_path):
    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_language(1, "tg")
    assert repo.get_user(1).language == "tg"


def test_main_menu_keyboard_shows_russian_labels_and_offers_tajik(tmp_path):
    kb = _main_menu_keyboard("ru")
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert SEARCH_BUTTON_TEXT_RU in texts
    assert PROFILE_BUTTON_TEXT_RU in texts
    assert LANG_TOGGLE_TO_TG_TEXT in texts  # offers to switch TO Tajik


def test_main_menu_keyboard_shows_tajik_labels_and_offers_russian(tmp_path):
    kb = _main_menu_keyboard("tg")
    texts = [btn.text for row in kb.keyboard for btn in row]
    assert SEARCH_BUTTON_TEXT_TG in texts
    assert PROFILE_BUTTON_TEXT_TG in texts
    assert LANG_TOGGLE_TO_RU_TEXT in texts  # offers to switch TO Russian


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
