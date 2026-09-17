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


# ---- _maybe_reattach_keyboard: throttled self-heal of the persistent bottom keyboard ----
# (Telegram clients have been seen dropping the reply keyboard on their own with no
# action on our side that would explain it -- see bot/core/monitor.py's _notify_group
# docstring for the notification-side self-heal this complements.)

import asyncio


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append({"chat_id": chat_id, "text": text, **kwargs})


def _run(coro):
    return asyncio.run(coro)


def test_maybe_reattach_keyboard_sends_it_when_never_attached_before(tmp_path):
    from bot.handlers.commands import _maybe_reattach_keyboard

    repo = _repo(tmp_path)
    repo.upsert_user(1)
    bot = _FakeBot()

    _run(_maybe_reattach_keyboard(bot, repo, repo.get_user(1)))

    assert len(bot.sent) == 1
    assert repo.get_user(1).keyboard_reattached_at is not None


def test_maybe_reattach_keyboard_is_throttled_within_the_cooldown(tmp_path):
    from bot.handlers.commands import _maybe_reattach_keyboard

    repo = _repo(tmp_path)
    repo.upsert_user(1)
    repo.set_keyboard_reattached_at(1, datetime.now(timezone.utc).isoformat())
    bot = _FakeBot()

    _run(_maybe_reattach_keyboard(bot, repo, repo.get_user(1)))

    assert bot.sent == []  # too soon since the last reattach -- no spam


def test_maybe_reattach_keyboard_fires_again_once_the_cooldown_has_passed(tmp_path):
    from bot.handlers.commands import _maybe_reattach_keyboard

    repo = _repo(tmp_path)
    repo.upsert_user(1)
    long_ago = (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat()
    repo.set_keyboard_reattached_at(1, long_ago)
    bot = _FakeBot()

    _run(_maybe_reattach_keyboard(bot, repo, repo.get_user(1)))

    assert len(bot.sent) == 1


# ---- _search_view: free daily vilki cap once trial/subscription has lapsed ----

def test_search_view_caps_lapsed_user_at_free_daily_limit(tmp_path):
    from bot.core import billing
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import LatestState, MatchSnapshot
    from bot.handlers.commands import _search_view

    repo = _repo(tmp_path)
    repo.upsert_user(1)
    past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    repo._conn.execute("UPDATE users SET trial_started_at = ? WHERE chat_id = ?", (past, 1))
    repo._conn.commit()

    def _match(i):
        best_odds = [OutcomeOdds(f"A{i}", "fonbet", 2.1), OutcomeOdds(f"B{i}", "olimpbet", 2.05)]
        arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0 + i)
        return MatchSnapshot("football", f"A{i}", f"B{i}", arb, f"2026-08-29T20:0{i}:00+00:00")

    state = LatestState()
    state.updated_at = 1.0
    state.matches = [_match(i) for i in range(billing.FREE_DAILY_VILKI_LIMIT + 2)]

    user = repo.get_user(1)
    text, _ = _search_view(user, state, 150, repo, frozenset())

    assert text.count("Расчётная разница") == billing.FREE_DAILY_VILKI_LIMIT
    assert "Бесплатный лимит на сегодня исчерпан" in text


def test_search_view_does_not_cap_a_user_with_active_access(tmp_path):
    from bot.core import billing
    from bot.core.arbitrage import ArbitrageResult, OutcomeOdds
    from bot.core.state import LatestState, MatchSnapshot
    from bot.handlers.commands import _search_view

    repo = _repo(tmp_path)
    repo.upsert_user(1)  # fresh -- still on trial

    def _match(i):
        best_odds = [OutcomeOdds(f"A{i}", "fonbet", 2.1), OutcomeOdds(f"B{i}", "olimpbet", 2.05)]
        arb = ArbitrageResult(best_odds=best_odds, arb_ratio=0.9, profit_pct=5.0 + i)
        return MatchSnapshot("football", f"A{i}", f"B{i}", arb, f"2026-08-29T20:0{i}:00+00:00")

    state = LatestState()
    state.updated_at = 1.0
    state.matches = [_match(i) for i in range(billing.FREE_DAILY_VILKI_LIMIT + 2)]

    user = repo.get_user(1)
    text, _ = _search_view(user, state, 150, repo, frozenset())

    assert text.count("Расчётная разница") == billing.FREE_DAILY_VILKI_LIMIT + 2
    assert "лимит" not in text.lower()
