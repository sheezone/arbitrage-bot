import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.db.repository import UserSettings
from bot.handlers.commands import NAV_SUB_METHOD_PREFIX, _subscription_method_view, _subscription_view


def _user() -> UserSettings:
    return UserSettings(
        chat_id=1,
        bankroll=100.0,
        min_profit_pct=1.0,
        watched_games=["cs2"],
        is_active=True,
        menu_message_id=None,
        trial_started_at="2026-01-01T00:00:00+00:00",
        subscription_expires_at=None,
        time_horizons=[1],
        referred_by=None,
        referral_balance_rub=0.0,
        expiry_reminder_sent_for=None,
        allowed_bookmakers=[],
        muted=False,
    )


def test_top_level_view_only_offers_methods_not_plans():
    text, keyboard = _subscription_view(_user(), yookassa_enabled=True, crypto_enabled=True)
    labels = [b.text for row in keyboard.inline_keyboard for b in row]
    assert any("Stars" in label for label in labels)
    assert any("карта" in label for label in labels)
    assert any("Крипто" in label for label in labels)
    assert not any("дней" in label for label in labels)  # plan labels like "7 дней" shouldn't appear yet


def test_top_level_view_hides_disabled_methods():
    text, keyboard = _subscription_view(_user(), yookassa_enabled=False, crypto_enabled=False)
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert f"{NAV_SUB_METHOD_PREFIX}rub" not in data
    assert f"{NAV_SUB_METHOD_PREFIX}crypto" not in data
    assert f"{NAV_SUB_METHOD_PREFIX}stars" in data


def test_method_view_lists_all_plans_for_that_method():
    text, keyboard = _subscription_method_view(_user(), "crypto", admin_chat_ids=frozenset())
    data = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert "sub:7d:crypto" in data
    assert "sub:30d:crypto" in data
    assert "sub:360d:crypto" in data
