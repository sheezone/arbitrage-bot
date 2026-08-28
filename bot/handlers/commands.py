"""The whole bot UI lives in a single message per chat that gets edited in place
(`_render`) rather than a growing feed of separate messages. /start is the only command
that (re)creates that message; every button press after that edits it, and free-text
answers (bankroll/threshold amounts) get deleted the instant they're read so the chat
stays down to one live message.

Two intentional exceptions: a one-time welcome note for new users (see `is_new_user`
below), and the small permanent line every /start sends to (re)attach the persistent
bottom reply keyboard -- a `ReplyKeyboardMarkup` can't share a message with the
dashboard's inline keyboard, has to be re-sent (not just once ever) so anyone whose
client cached an older button layout picks up the current one, and is never deleted:
confirmed live that deleting that carrier message -- immediately or after a delay --
makes the keyboard itself disappear on at least one client.

Free-text answers include bankroll/threshold amounts and the "🧮 Калькулятор" input
(bankroll + two odds, space-separated in one message)."""
from __future__ import annotations

import html
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    KeyboardButton,
    LabeledPrice,
    LinkPreviewOptions,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)

from bot.core import billing
from bot.core.arbitrage import OutcomeOdds, calc_arbitrage, calc_stakes
from bot.core.monitor import (
    BOOKMAKER_URLS,
    GAME_EMOJI,
    format_match_start,
    format_odds_lines,
    format_stakes_lines,
    user_allows_arb,
    within_time_horizon,
)
from bot.core.state import LatestState
from bot.db.repository import Repository, UserSettings
from bot.handlers.states import Settings
from bot.providers.cryptobot import CryptoPayClient, CryptoPayError

router = Router()
logger = logging.getLogger(__name__)

MOSCOW_TZ = timezone(timedelta(hours=3))
_ASSETS = Path(__file__).resolve().parent.parent / "assets"
BANNER_PATH = _ASSETS / "banner.png"
BANNER_REFERRAL_PATH = _ASSETS / "banner_referral.png"
BANNER_STATUS_ACTIVE_PATH = _ASSETS / "banner_status_active.png"
BANNER_STATUS_PAUSED_PATH = _ASSETS / "banner_status_paused.png"
BANNER_SEARCH_PATH = _ASSETS / "banner_search.png"
BANNER_SUBSCRIPTION_PATH = _ASSETS / "banner_subscription.png"
BANNER_THRESHOLD_PATH = _ASSETS / "banner_threshold.png"
BANNER_HELP_PATH = _ASSETS / "banner_help.png"
BANNER_STATS_PATH = _ASSETS / "banner_stats.png"
BANNER_BOOKMAKERS_PATH = _ASSETS / "banner_bookmakers.png"
BANNER_SETTINGS_PATH = _ASSETS / "banner_settings.png"

GAME_LABELS = {
    "cs2": "CS2",
    "dota2": "Dota 2",
    "lol": "LoL",
    "valorant": "Valorant",
    "tennis": "Теннис",
    "basketball": "Баскетбол",
    "football": "Футбол (тотал голов)",
    "hockey": "Хоккей (тотал шайб)",
    "boxing": "Бокс (тотал раундов)",
    "mma": "MMA (тотал раундов)",
    "volleyball": "Волейбол",
}

MENU_BUTTON_TEXT = "☰ Меню"
SEARCH_BUTTON_TEXT = "🔍 Поиск вилок"
PROFILE_BUTTON_TEXT = "👤 Мой профиль"
HELP_BUTTON_TEXT = "ℹ️ Помощь"

# The dashboard message itself carries no inline keyboard (see _dashboard_view) -- this
# compact row is the only bottom-of-chat surface. What to search for (bankroll,
# threshold, games, time horizon) lives one level down inside "🔍 Поиск вилок" itself
# (see _search_view), right next to the results it controls; account-level things
# (pause, subscription, help) live inside "👤 Мой профиль" (see _profile_view).
MAIN_MENU_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=SEARCH_BUTTON_TEXT), KeyboardButton(text=PROFILE_BUTTON_TEXT), KeyboardButton(text=MENU_BUTTON_TEXT)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

TIME_HORIZONS = {1: "До 24 часов", 2: "Более 24 часов"}

NAV_DASHBOARD = "nav:dashboard"
NAV_PROFILE = "nav:profile"
NAV_SEARCH = "nav:search"
NAV_CANCEL = "nav:cancel"
NAV_BANKROLL = "nav:bankroll"
NAV_THRESHOLD = "nav:threshold"
NAV_HORIZON = "nav:horizon"
NAV_CALCULATOR = "nav:calculator"
NAV_SETTINGS = "nav:settings"
NAV_SUPPORT = "nav:support"
NAV_TOGGLE_ACTIVE = "nav:toggle_active"
NAV_TOGGLE_MUTED = "nav:toggle_muted"
NAV_SUBSCRIPTION = "nav:subscription"
NAV_HELP = "nav:help"
NAV_REFERRAL = "nav:referral"

View = tuple[str, InlineKeyboardMarkup | None]


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _dashboard_view(user: UserSettings, admin_chat_ids: frozenset[int] = frozenset()) -> View:
    now = datetime.now(timezone.utc)
    if not billing.has_access(user, now, admin_chat_ids):
        text = (
            "🎰 <b>АРБИТРАЖНЫЙ БОТ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Пробный период закончился.\n"
            "Оформите подписку, чтобы бот продолжил присылать уведомления о вилках "
            f"(кнопка «{PROFILE_BUTTON_TEXT}» снизу → «💳 Подписка»)."
        )
        return text, None

    status = "🟢 Активен" if user.is_active else "⏸️ На паузе"
    if billing.is_admin(user, admin_chat_ids):
        access_line = "♾️ Безлимитный доступ"
    else:
        left = billing.days_left(user, now)
        access_line = (
            f"⏳ Пробный период · осталось {left} дн."
            if billing.on_trial(user, now)
            else f"💳 Подписка активна · осталось {left} дн."
        )

    text = (
        "🎰 <b>АРБИТРАЖНЫЙ БОТ</b>\n\n"
        f"{status}  ·  {access_line}\n\n"
        f"Настройки — «{PROFILE_BUTTON_TEXT}» снизу\n"
        "⬇️ Управление — кнопками снизу"
    )
    return text, None


def _back_keyboard(extra: list[InlineKeyboardButton] | None = None, target: str = NAV_PROFILE) -> InlineKeyboardMarkup:
    rows = [extra] if extra else []
    rows.append([_btn("◀️ Назад", target)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _profile_view(user: UserSettings, admin_chat_ids: frozenset[int] = frozenset()) -> View:
    now = datetime.now(timezone.utc)
    status = "🟢 Активен" if user.is_active else "⏸️ На паузе"
    if user.is_active and user.muted:
        status += " (🔕 без звука)"
    pause_label = "⏸️ Поставить на паузу" if user.is_active else "▶️ Возобновить"
    mute_label = "🔔 Включить звук" if user.muted else "🔕 Тихий режим"
    if billing.is_admin(user, admin_chat_ids):
        access_line = "♾️ Безлимитный доступ (админ)"
    else:
        left = billing.days_left(user, now)
        access_line = (
            f"⏳ Пробный период · осталось {left} дн."
            if billing.on_trial(user, now)
            else f"💳 Подписка активна · осталось {left} дн."
            if left > 0
            else "Доступ истёк"
        )

    text = f"👤 <b>МОЙ ПРОФИЛЬ</b>\n━━━━━━━━━━━━━━━━━━━━\n\nСтатус: {status}\n{access_line}"
    rows = [
        [_btn(pause_label, NAV_TOGGLE_ACTIVE), _btn(mute_label, NAV_TOGGLE_MUTED)],
        [_btn("💳 Подписка", NAV_SUBSCRIPTION)],
        [_btn("📊 Статистика", NAV_STATS)],
        [_btn("🤝 Партнёрская программа", NAV_REFERRAL)],
        [_btn("ℹ️ Помощь", NAV_HELP)],
        [_btn("◀️ Назад", NAV_DASHBOARD)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


NAV_STATS = "nav:stats"


def _stats_view(repo: Repository) -> View:
    s = repo.get_opportunity_stats()
    text = (
        "📊 <b>СТАТИСТИКА ВИЛОК</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Сегодня:</b>\n"
        f"🔎 Найдено вилок: <b>{s['today_count']}</b>\n"
        f"📈 Средняя прибыль: <b>{s['today_avg_profit']:.2f}%</b>\n"
        f"🚀 Лучшая прибыль: <b>{s['today_best_profit']:.2f}%</b>\n\n"
        "<b>За всё время:</b>\n"
        f"🔎 Найдено вилок: <b>{s['alltime_count']}</b>\n"
        f"📈 Средняя прибыль: <b>{s['alltime_avg_profit']:.2f}%</b>"
    )
    rows = [
        [_btn("🔄 Обновить", NAV_STATS)],
        [_btn("◀️ Назад", NAV_PROFILE)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _status_banner(user: UserSettings) -> Path:
    return BANNER_STATUS_ACTIVE_PATH if user.is_active else BANNER_STATUS_PAUSED_PATH


def _referral_view(user: UserSettings, repo: Repository, bot_username: str) -> View:
    referrals = repo.count_referrals(user.chat_id)
    link = f"https://t.me/{bot_username}?start={user.chat_id}" if bot_username else "—"
    text = (
        "🤝 <b>ПАРТНЁРСКАЯ ПРОГРАММА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Приглашайте друзей — получайте {billing.REFERRAL_COMMISSION_PCT:.0%} с каждой их "
        "оплаты подписки в виде скидки на свою следующую покупку (не вывод деньгами).\n\n"
        f"👥 Приглашено: <b>{referrals}</b>\n"
        f"💰 Баланс скидки: <b>{user.referral_balance_rub:.2f}₽</b>\n\n"
        f"🔗 Ваша ссылка:\n{link}"
    )
    rows = [
        [_btn("🔄 Обновить", NAV_REFERRAL)],
        [_btn("◀️ Назад", NAV_PROFILE)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _help_view() -> View:
    text = (
        "ℹ️ <b>КАК ЭТО РАБОТАЕТ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Слежу за коэффициентами у разных букмекеров одновременно и ищу "
        "вилки (arbitrage) — ситуацию, когда ставка на все исходы события "
        "у разных БК даёт прибыль <b>независимо от результата</b>.\n\n"
        "<b>Пример:</b> БК1 даёт 2.10 на победу команды А, БК2 даёт 2.05 на "
        "победу команды Б. Разделив банкролл в нужной пропорции между "
        "двумя ставками, вы получаете плюс при любом исходе.\n\n"
        "<b>Что отслеживается:</b>\n"
        "🎮 CS2, Dota 2, LoL, Valorant — победитель матча\n"
        "🎾 Теннис — победитель матча\n"
        "🏀 Баскетбол — победитель матча\n"
        "🏐 Волейбол — победитель матча\n"
        "⚽ Футбол, 🏒 Хоккей, 🥊 Бокс, 🥋 MMA — тотал (голов/шайб/раундов, "
        "Больше/Меньше), а не победитель: в этих видах возможна ничья, а "
        "тотал — это всегда ровно два исхода\n\n"
        "<b>Источники:</b> OddsPapi, Fonbet, PARI, Marathon, Baltbet, "
        "The Odds API, Zenit, Melbet, Leon, SureBet — чем больше источников, тем больше шанс "
        "найти расхождение в котировках. Отслеживаются все виды спорта сразу.\n\n"
        "<b>Настройки внутри «🔍 Поиск вилок»:</b>\n"
        "💰 Банкролл — сумма, под которую бот рассчитывает точные ставки "
        "на каждый исход\n"
        "📊 Порог прибыли — минимальный % прибыли, при котором придёт "
        "уведомление\n"
        "📅 Период — показывать вилки только на матчи, которые начнутся в течение "
        "24 часов или позже\n\n"
        "<b>Внутри «👤 Мой профиль»:</b> пауза или тихий режим (без звука) для уведомлений, подписка и "
        f"партнёрская программа ({billing.REFERRAL_COMMISSION_PCT:.0%} с оплат "
        "приглашённых — в виде скидки на свою подписку).\n\n"
        "Уведомления приходят автоматически, как только находится "
        "вилка. Кнопка «🔍 Поиск вилок» мгновенно "
        "показывает последний найденный результат без нового опроса "
        "источников.\n\n"
        f"Первые {billing.TRIAL_DAYS} дн. бесплатно (пробный период), дальше — "
        "платная подписка, раздел «👤 Мой профиль» → «💳 Подписка»."
    )
    return text, _back_keyboard([_btn("✉️ Написать менеджеру", NAV_SUPPORT)], target=NAV_DASHBOARD)


def _support_prompt_view(error: str | None = None) -> View:
    text = (
        "✉️ <b>НАПИСАТЬ МЕНЕДЖЕРУ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Опишите вопрос одним сообщением — менеджер ответит прямо в этом чате.\n\n"
        "Обычно отвечаем в течение часа."
    )
    if error:
        text = f"⚠️ {error}\n\n{text}"
    return text, _back_keyboard([_btn("❌ Отмена", NAV_CANCEL)], target=NAV_HELP)


def _horizon_view(user: UserSettings) -> View:
    selected = set(user.time_horizons)
    rows = []
    for days, label in TIME_HORIZONS.items():
        mark = "✅" if days in selected else "⬜"
        rows.append([_btn(f"{mark} {label}", f"horizon:{days}")])
    rows.append([_btn("◀️ Назад", NAV_SETTINGS)])
    text = (
        "📅 <b>ПЕРИОД ПОИСКА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Когда начинается матч. Можно отметить оба, чтобы видеть всё:"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


NAV_BOOKMAKERS = "nav:bookmakers"
NAV_NOOP = "nav:noop"
_ALL_BOOKMAKER_KEYS = sorted(BOOKMAKER_URLS.keys())

# Grouped for the toggle screen only (BOOKMAKER_URLS/user_allows_arb stay flat) -- direct
# sources are scraped/reached by this codebase's own provider modules, the rest only ever
# show up via the SureBet aggregator (see bot/providers/surebet.py). Purely cosmetic
# section headers; any key not listed here (there shouldn't be any) falls back into
# "Другие" so a newly added bookmaker never silently disappears from the screen.
_DIRECT_BOOKMAKERS = {"fonbet", "pari", "marathon", "baltbet", "zenit", "melbet", "leon", "olimpbet"}
_AGGREGATOR_BOOKMAKERS = {"winline", "betcity", "betboom", "ligastavok", "bet365", "1xbet", "pinnacle"}


def _selected_bookmakers(user: UserSettings) -> set[str]:
    """Empty allowed_bookmakers means "no restriction" (see user_allows_arb) -- resolve
    that to the full set here so the toggle screen always shows concrete checkmarks."""
    return set(user.allowed_bookmakers) if user.allowed_bookmakers else set(_ALL_BOOKMAKER_KEYS)


def _bookmaker_rows(keys: list[str], selected: set[str]) -> list[list[InlineKeyboardButton]]:
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for key in keys:
        mark = "✅" if key in selected else "⬜"
        row.append(_btn(f"{mark} {key.upper()}", f"bk:{key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _bookmakers_view(user: UserSettings) -> View:
    selected = _selected_bookmakers(user)
    direct = sorted(k for k in _ALL_BOOKMAKER_KEYS if k in _DIRECT_BOOKMAKERS)
    aggregator = sorted(k for k in _ALL_BOOKMAKER_KEYS if k in _AGGREGATOR_BOOKMAKERS)
    other = sorted(k for k in _ALL_BOOKMAKER_KEYS if k not in _DIRECT_BOOKMAKERS and k not in _AGGREGATOR_BOOKMAKERS)

    rows: list[list[InlineKeyboardButton]] = []
    if direct:
        rows.append([_btn("— Прямые источники —", NAV_NOOP)])
        rows.extend(_bookmaker_rows(direct, selected))
    if aggregator:
        rows.append([_btn("— Через SureBet —", NAV_NOOP)])
        rows.extend(_bookmaker_rows(aggregator, selected))
    if other:
        rows.append([_btn("— Другие —", NAV_NOOP)])
        rows.extend(_bookmaker_rows(other, selected))
    rows.append([_btn("◀️ Назад", NAV_SETTINGS)])
    text = (
        "🏦 <b>МОИ БУКМЕКЕРЫ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Вилки с отключёнными букмекерами не показываются и не присылаются. "
        "Отметьте только те конторы, где у вас есть аккаунт."
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


NAV_SUB_METHOD_PREFIX = "sub_method:"


def _subscription_status_line(user: UserSettings, admin_chat_ids: frozenset[int]) -> str:
    now = datetime.now(timezone.utc)
    if billing.is_admin(user, admin_chat_ids):
        return "♾️ Безлимитный доступ (админ)"
    left = billing.days_left(user, now)
    return f"Осталось дней доступа: <b>{left}</b>" if left > 0 else "Доступ истёк"


def _subscription_view(
    user: UserSettings, yookassa_enabled: bool, crypto_enabled: bool = False, admin_chat_ids: frozenset[int] = frozenset()
) -> View:
    """Top level: pick a payment method first (each has its own submenu of the 3 plans --
    see _subscription_method_view) rather than one screen listing every plan x every
    method at once, which got cramped once crypto joined Stars/card."""
    status = _subscription_status_line(user, admin_chat_ids)
    text = f"💳 <b>ПОДПИСКА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{status}\n\nВыберите способ оплаты:"
    rows = [[_btn("⭐ Telegram Stars", f"{NAV_SUB_METHOD_PREFIX}stars")]]
    if yookassa_enabled:
        rows.append([_btn("💳 Банковская карта", f"{NAV_SUB_METHOD_PREFIX}rub")])
    if crypto_enabled:
        rows.append([_btn("💎 Криптовалюта (USDT)", f"{NAV_SUB_METHOD_PREFIX}crypto")])
    rows.append([_btn("◀️ Назад", NAV_PROFILE)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


_METHOD_LABELS = {"stars": "⭐ TELEGRAM STARS", "rub": "💳 БАНКОВСКАЯ КАРТА", "crypto": "💎 КРИПТОВАЛЮТА (USDT)"}


def _subscription_method_view(user: UserSettings, method: str, admin_chat_ids: frozenset[int]) -> View:
    status = _subscription_status_line(user, admin_chat_ids)
    label = _METHOD_LABELS.get(method, method.upper())
    text = f"{label}\n━━━━━━━━━━━━━━━━━━━━\n\n{status}\n\nВыберите тариф:"
    rows = []
    for plan in billing.PLANS:
        if method == "stars":
            price = f"{plan.price_stars} ⭐"
        elif method == "rub":
            price = f"{plan.price_rub}₽"
        else:
            price = f"{plan.price_usdt:g} USDT"
        rows.append([_btn(f"{plan.label} — {price}", f"sub:{plan.id}:{method}")])
    rows.append([_btn("◀️ Назад", NAV_SUBSCRIPTION)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _input_prompt_view(label: str, example: str, error: str | None = None) -> View:
    text = f"{label}\nНапример: {example}"
    if error:
        text = f"⚠️ {error}\n\n{text}"
    return text, _back_keyboard([_btn("❌ Отмена", NAV_CANCEL)], target=NAV_SEARCH)


BANKROLL_PRESETS = [500, 1000, 5000, 10000]
NAV_BANKROLL_PRESET_PREFIX = "bankroll_preset:"


def _bankroll_prompt_view(error: str | None = None) -> View:
    text, _keyboard = _input_prompt_view("💰 Введите новый банкролл числом:", "100", error=error)
    preset_row = [_btn(str(p), f"{NAV_BANKROLL_PRESET_PREFIX}{p}") for p in BANKROLL_PRESETS]
    keyboard = InlineKeyboardMarkup(inline_keyboard=[preset_row, [_btn("❌ Отмена", NAV_CANCEL)]])
    return text, keyboard


_CALCULATOR_HEADER = "🧮 <b>КАЛЬКУЛЯТОР ВИЛКИ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"


def _calculator_step_view(label: str, example: str, step: str, total_steps: int = 3, error: str | None = None) -> View:
    """Step N/3 of the calculator's bankroll -> odds A -> odds B flow -- one number per
    message rather than the old single "1000 2.10 2.05" line, so there's nothing to
    mis-order and each answer validates (and can be corrected) on its own."""
    text = f"{_CALCULATOR_HEADER}Шаг {step}/{total_steps}\n{label}\nНапример: {example}"
    if error:
        text = f"⚠️ {error}\n\n{text}"
    return text, _back_keyboard([_btn("❌ Отмена", NAV_CANCEL)], target=NAV_SEARCH)


def _calculator_bankroll_prompt(error: str | None = None) -> View:
    return _calculator_step_view("💰 Введите банкролл (сумму на вилку):", "1000", step="1", error=error)


def _calculator_odds_a_prompt(error: str | None = None) -> View:
    return _calculator_step_view("📈 Введите коэффициент на первый исход:", "2.10", step="2", error=error)


def _calculator_odds_b_prompt(error: str | None = None) -> View:
    return _calculator_step_view("📉 Введите коэффициент на второй исход:", "2.05", step="3", error=error)


def _calculator_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🧮 Ещё раз", NAV_CALCULATOR)],
            [_btn("◀️ Назад", NAV_SEARCH)],
        ]
    )


def _calculator_result_view(bankroll: float, odds_a: float, odds_b: float) -> View:
    outcome_a, outcome_b = "Исход 1", "Исход 2"
    odds_by_outcome = {
        outcome_a: [OutcomeOdds(outcome_a, "", odds_a)],
        outcome_b: [OutcomeOdds(outcome_b, "", odds_b)],
    }
    arb = calc_arbitrage(odds_by_outcome)
    stakes = calc_stakes(bankroll, arb.best_odds)

    odds_lines = [f"📈 <b>{outcome_a}</b>: {odds_a}", f"📉 <b>{outcome_b}</b>: {odds_b}"]

    lines = [
        _CALCULATOR_HEADER.rstrip(),
        "",
        f"💰 Банкролл: <b>{bankroll:.2f}</b>",
        "<blockquote>" + "\n".join(odds_lines) + "</blockquote>",
    ]
    if arb.is_arbitrage:
        profit_amount = bankroll * arb.profit_pct / 100
        lines.append(f"🚀 Прибыль: <b>{arb.profit_pct:.2f}%</b>")
        lines.append(f"💸 Гарантированный выигрыш: <b>{profit_amount:.2f}</b>")
        lines.append("")
        lines.append("💵 <b>Ставки:</b>")
        stake_lines = [
            f"▫️ На {outcome_a}: <b>{stakes[outcome_a]:.2f}</b>",
            f"▫️ На {outcome_b}: <b>{stakes[outcome_b]:.2f}</b>",
        ]
        lines.append("<blockquote>" + "\n".join(stake_lines) + "</blockquote>")
    else:
        lines.append("")
        lines.append(f"⚠️ Это не вилка — при таких коэффициентах убыток <b>{-arb.profit_pct:.2f}%</b>")

    return "\n".join(lines), _calculator_keyboard()


def _search_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔄 Обновить", NAV_SEARCH)],
            [_btn("⚙️ Настройки поиска", NAV_SETTINGS), _btn("🧮 Калькулятор", NAV_CALCULATOR)],
            [_btn("◀️ Назад", NAV_DASHBOARD)],
        ]
    )


def _settings_view() -> View:
    text = "⚙️ <b>НАСТРОЙКИ ПОИСКА</b>\n━━━━━━━━━━━━━━━━━━━━\n\nЧто и как показывать в «🔍 Поиск вилок»."
    rows = [
        [_btn("💰 Банкролл", NAV_BANKROLL), _btn("📊 Порог прибыли", NAV_THRESHOLD)],
        [_btn("📅 Период", NAV_HORIZON), _btn("🏦 Мои букмекеры", NAV_BOOKMAKERS)],
        [_btn("◀️ Назад", NAV_SEARCH)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


# Telegram caps plain-text messages at 4096 chars; leaving headroom below that (rather
# than hitting the limit exactly) avoids a hard-to-predict off-by-a-few-entities failure.
# Confirmed live: with enough simultaneous matches (each now several lines with the
# blockquote/profit-amount additions), the unbounded listing blew past this and every
# "Обновить" press failed with "message is too long", silently breaking the screen.
_SEARCH_TEXT_BUDGET = 3500


def _next_update_note(latest_state: LatestState, poll_interval_seconds: int) -> str:
    """A rough estimate, not a promise -- a cycle that triggers the high-profit recheck
    (see monitor.py) or hits a slow source can run well past poll_interval_seconds, so this
    is phrased as "~" and never shown as counting down to zero, just "скоро" once due."""
    elapsed = time.time() - latest_state.updated_at
    remaining = poll_interval_seconds - elapsed
    return f"~{max(1, round(remaining))} сек" if remaining > 3 else "скоро"


def _search_view(user: UserSettings, latest_state: LatestState, poll_interval_seconds: int = 150) -> View:
    if latest_state.updated_at == 0:
        text = "🔍 <b>ПОИСК ВИЛОК</b>\n━━━━━━━━━━━━━━━━━━━━\n\n⏳ Ещё идёт первая проверка, попробуйте через полминуты."
        return text, _search_keyboard()

    checked_at = datetime.fromtimestamp(latest_state.updated_at, tz=MOSCOW_TZ).strftime("%H:%M:%S МСК")
    next_update = _next_update_note(latest_state, poll_interval_seconds)
    now = datetime.now(timezone.utc)
    matches = [
        m
        for m in latest_state.matches
        if m.arb.profit_pct >= user.min_profit_pct
        and within_time_horizon(m.start_time_utc, user.time_horizons, now)
        and user_allows_arb(user.allowed_bookmakers, m.arb.best_odds)
    ]
    matches.sort(key=lambda m: m.arb.profit_pct, reverse=True)

    header = ["🔍 <b>ПОИСК ВИЛОК</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not matches:
        header.append(f"Сейчас подходящих вилок нет.\nДанные на {checked_at} · след. проверка {next_update}.")
        return "\n".join(header), _search_keyboard()

    header.append(f"Найдено вилок: <b>{len(matches)}</b> (данные на {checked_at} · след. проверка {next_update})\n")

    blocks = []
    for m in matches:
        stakes = calc_stakes(user.bankroll, m.arb.best_odds)
        emoji = GAME_EMOJI.get(m.game, "🏆")
        block = [
            f"{emoji} <b>{GAME_LABELS.get(m.game, m.game.upper())}</b>",
            f"⚔️ <b>{html.escape(m.team_a)}</b> vs <b>{html.escape(m.team_b)}</b>",
        ]
        match_time = format_match_start(m.start_time_utc)
        if match_time:
            block.append(f"🕒 {match_time}")
        block.append(f"🚀 Прибыль: <b>{m.arb.profit_pct:.2f}%</b>")
        profit_amount = user.bankroll * m.arb.profit_pct / 100
        block.append(f"💸 Возможный выигрыш: <b>{profit_amount:.2f}</b>")
        block.append("")
        quote_lines = format_odds_lines(m.arb.best_odds) + ["", "💵 <b>Ставки:</b>"] + format_stakes_lines(stakes)
        block.append("<blockquote>" + "\n".join(quote_lines) + "</blockquote>")
        block.append("")
        blocks.append("\n".join(block))

    lines = list(header)
    shown = 0
    budget = _SEARCH_TEXT_BUDGET - sum(len(b) for b in header)
    for block in blocks:
        if shown > 0 and len(block) > budget:
            break
        lines.append(block)
        budget -= len(block)
        shown += 1

    if shown < len(matches):
        lines.append(f"…и ещё {len(matches) - shown} вилок (показаны лучшие по проценту прибыли).")

    return "\n".join(lines), _search_keyboard()


async def _render(
    bot: Bot,
    repo: Repository,
    chat_id: int,
    message_id: int | None,
    text: str,
    keyboard,
    *,
    photo_path: Path | None = None,
) -> None:
    """Edit the tracked menu message in place; only send a new one if editing is
    impossible (first run, message type mismatch between photo/text, or the old
    message is gone/too old to edit). Photo screens use edit_message_media (not
    edit_message_caption) since different photo screens (dashboard/referral/profile
    status) each ship their own banner file -- a caption-only edit would leave a
    stale photo from whichever screen was shown before.

    A photo caption caps out at 1024 chars (vs. 4096 for plain text) -- the search
    screen's listing can blow well past that once several matches are found, so a
    caption too long for the banner silently falls back to a plain text render rather
    than raising and leaving the screen stuck."""
    if photo_path is not None and len(text) > 1024:
        photo_path = None
    if message_id:
        try:
            if photo_path is not None:
                media = InputMediaPhoto(media=FSInputFile(photo_path), caption=text, parse_mode="HTML")
                await bot.edit_message_media(chat_id=chat_id, message_id=message_id, media=media, reply_markup=keyboard)
            else:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode="HTML",
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception:
                pass

    if photo_path is not None:
        sent = await bot.send_photo(
            chat_id, FSInputFile(photo_path), caption=text, reply_markup=keyboard, parse_mode="HTML"
        )
    else:
        sent = await bot.send_message(
            chat_id, text, reply_markup=keyboard, parse_mode="HTML",
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    repo.set_menu_message_id(chat_id, sent.message_id)


async def _render_dashboard(
    bot: Bot, repo: Repository, chat_id: int, admin_chat_ids: frozenset[int] = frozenset()
) -> None:
    user = repo.get_user(chat_id)
    text, keyboard = _dashboard_view(user, admin_chat_ids)
    await _render(bot, repo, chat_id, user.menu_message_id, text, keyboard, photo_path=BANNER_PATH)


def register_handlers(
    repo: Repository,
    latest_state: LatestState,
    yookassa_provider_token: str = "",
    admin_chat_ids: frozenset[int] = frozenset(),
    bot_username: str = "",
    poll_interval_seconds: int = 150,
    crypto_pay_client: CryptoPayClient | None = None,
) -> Router:
    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        is_new_user = repo.get_user(message.chat.id) is None

        referred_by = None
        if is_new_user:
            # Deep-link referral payload: t.me/<bot>?start=<referrer_chat_id>. Self-
            # referral and a payload pointing at a chat_id that isn't a real user are
            # both silently ignored rather than guessed at.
            parts = (message.text or "").split(maxsplit=1)
            if len(parts) == 2 and parts[1].strip().lstrip("-").isdigit():
                candidate = int(parts[1].strip())
                if candidate != message.chat.id and repo.get_user(candidate) is not None:
                    referred_by = candidate
        repo.upsert_user(message.chat.id, referred_by=referred_by)
        user = repo.get_user(message.chat.id)

        # Re-attach the persistent bottom menu on every /start, not just for new users --
        # otherwise anyone whose client cached an older keyboard layout (e.g. before a
        # button was added) never sees the update. This carrier message is deliberately
        # NOT deleted: confirmed live that deleting it -- immediately or after a delay --
        # makes the reply keyboard itself disappear on at least one client, contrary to
        # the usual "keyboard survives its carrier message" behavior.
        await message.answer("👇 Кнопки снизу — быстрый доступ к разделам.", reply_markup=MAIN_MENU_KEYBOARD)

        if is_new_user:
            # A one-off exception to the single-message UI: a permanent welcome note
            # explaining the trial/subscription policy, sent once per user, left in the
            # chat as a standing reference rather than folded into the dashboard panel.
            trial_days = billing.REFERRED_TRIAL_DAYS if referred_by is not None else billing.TRIAL_DAYS
            trial_note = " (по реферальной ссылке — дольше обычного)" if referred_by is not None else ""
            await message.answer(
                "👋 <b>Добро пожаловать в Арбитражный бот!</b>\n\n"
                f"Бот работает в тестовом режиме {trial_days} дн.{trial_note} — полный "
                "бесплатный доступ ко всем функциям. После этого понадобится оформить "
                "подписку (кнопка «💳 Подписка» на главном экране), чтобы продолжать "
                "получать уведомления о найденных вилках.",
                parse_mode="HTML",
            )

        # /start always plants a fresh message at the bottom of the chat rather than
        # editing a possibly-scrolled-away-from old one.
        if user.menu_message_id:
            try:
                await bot.delete_message(message.chat.id, user.menu_message_id)
            except Exception:
                pass
            repo.set_menu_message_id(message.chat.id, None)

        text, keyboard = _dashboard_view(user, admin_chat_ids)
        sent = await message.answer_photo(
            FSInputFile(BANNER_PATH), caption=text, reply_markup=keyboard, parse_mode="HTML"
        )
        repo.set_menu_message_id(message.chat.id, sent.message_id)

        try:
            await message.delete()
        except Exception:
            pass

    async def _dismiss(message: Message) -> None:
        try:
            await message.delete()
        except Exception:
            pass

    @router.message(F.text == MENU_BUTTON_TEXT)
    async def on_menu_button(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _dismiss(message)
        await _render_dashboard(bot, repo, message.chat.id, admin_chat_ids)

    @router.message(F.text == SEARCH_BUTTON_TEXT)
    async def on_search_button(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _dismiss(message)
        user = repo.get_user(message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=BANNER_SEARCH_PATH)

    @router.message(F.text == PROFILE_BUTTON_TEXT)
    async def on_profile_button(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _dismiss(message)
        user = repo.get_user(message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=_status_banner(user))

    @router.callback_query(F.data == NAV_PROFILE)
    async def on_nav_profile(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=_status_banner(user),
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_SETTINGS)
    async def on_nav_settings(callback: CallbackQuery, bot: Bot) -> None:
        text, keyboard = _settings_view()
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SETTINGS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_NOOP)
    async def on_nav_noop(callback: CallbackQuery) -> None:
        await callback.answer()

    @router.callback_query(F.data == NAV_HORIZON)
    async def on_nav_horizon(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _horizon_view(user)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SETTINGS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("horizon:"))
    async def on_horizon_toggle(callback: CallbackQuery, bot: Bot) -> None:
        days = int(callback.data.split(":", 1)[1])
        user = repo.get_user(callback.message.chat.id)
        # Drop any leftover values from before the 1/3/30/90-day options were replaced by
        # just these two buckets, so a stale value never blocks toggling a real one.
        selected = set(user.time_horizons) & set(TIME_HORIZONS)
        if days in selected:
            if len(selected) == 1:
                await callback.answer("Нужно оставить хотя бы один вариант", show_alert=True)
                return
            selected.discard(days)
        else:
            selected.add(days)
        repo.set_time_horizons(callback.message.chat.id, sorted(selected))
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _horizon_view(user)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SETTINGS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_STATS)
    async def on_nav_stats(callback: CallbackQuery, bot: Bot) -> None:
        text, keyboard = _stats_view(repo)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_STATS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_BOOKMAKERS)
    async def on_nav_bookmakers(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _bookmakers_view(user)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_BOOKMAKERS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith("bk:"))
    async def on_bookmaker_toggle(callback: CallbackQuery, bot: Bot) -> None:
        key = callback.data.split(":", 1)[1]
        user = repo.get_user(callback.message.chat.id)
        selected = _selected_bookmakers(user)
        if key in selected:
            if len(selected) == 1:
                await callback.answer("Нужно оставить хотя бы одного букмекера", show_alert=True)
                return
            selected.discard(key)
        else:
            selected.add(key)
        # Selecting everything back is stored as empty (== "no restriction") rather than
        # the full explicit list -- functionally identical, but also covers any bookmaker
        # key added to BOOKMAKER_URLS later without silently excluding it for existing users.
        to_store = [] if selected == set(_ALL_BOOKMAKER_KEYS) else sorted(selected)
        repo.set_allowed_bookmakers(callback.message.chat.id, to_store)
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _bookmakers_view(user)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_BOOKMAKERS_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_TOGGLE_ACTIVE)
    async def on_nav_toggle_active(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        repo.set_active(callback.message.chat.id, not user.is_active)
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=_status_banner(user),
        )
        await callback.answer("Пауза" if not user.is_active else "Возобновлено")

    @router.callback_query(F.data == NAV_TOGGLE_MUTED)
    async def on_nav_toggle_muted(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        repo.set_muted(callback.message.chat.id, not user.muted)
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=_status_banner(user),
        )
        await callback.answer("Тихий режим включён" if user.muted else "Звук уведомлений включён")

    @router.callback_query(F.data == NAV_SUBSCRIPTION)
    async def on_nav_subscription(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _subscription_view(
            user, bool(yookassa_provider_token), crypto_pay_client is not None, admin_chat_ids
        )
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SUBSCRIPTION_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data.startswith(NAV_SUB_METHOD_PREFIX))
    async def on_sub_method(callback: CallbackQuery, bot: Bot) -> None:
        method = callback.data[len(NAV_SUB_METHOD_PREFIX):]
        if method == "rub" and not yookassa_provider_token:
            await callback.answer("Оплата картой пока не подключена", show_alert=True)
            return
        if method == "crypto" and crypto_pay_client is None:
            await callback.answer("Оплата криптой пока не подключена", show_alert=True)
            return
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _subscription_method_view(user, method, admin_chat_ids)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SUBSCRIPTION_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_REFERRAL)
    async def on_nav_referral(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _referral_view(user, repo, bot_username)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_REFERRAL_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_HELP)
    async def on_nav_help(callback: CallbackQuery, bot: Bot) -> None:
        text, keyboard = _help_view()
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_HELP_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_SUPPORT)
    async def on_nav_support(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        if not admin_chat_ids:
            await callback.answer("Поддержка временно недоступна", show_alert=True)
            return
        await state.set_state(Settings.waiting_support_message)
        text, keyboard = _support_prompt_view()
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_BANKROLL)
    async def on_nav_bankroll(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_bankroll)
        text, keyboard = _bankroll_prompt_view()
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith(NAV_BANKROLL_PRESET_PREFIX))
    async def on_bankroll_preset(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        amount = float(callback.data[len(NAV_BANKROLL_PRESET_PREFIX):])
        repo.set_bankroll(callback.message.chat.id, amount)
        await state.clear()
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SEARCH_PATH,
        )
        await callback.answer(f"Банкролл: {amount:.0f}")

    @router.callback_query(F.data == NAV_THRESHOLD)
    async def on_nav_threshold(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_threshold)
        text, keyboard = _input_prompt_view("📊 Введите минимальный процент прибыли для уведомления:", "1.5")
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_THRESHOLD_PATH,
        )
        await callback.answer()

    @router.callback_query(F.data == NAV_CALCULATOR)
    async def on_nav_calculator(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_calc_bankroll)
        text, keyboard = _calculator_bankroll_prompt()
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_DASHBOARD)
    async def on_nav_dashboard(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _render_dashboard(bot, repo, callback.message.chat.id, admin_chat_ids)
        await callback.answer()

    @router.callback_query(F.data == NAV_CANCEL)
    async def on_nav_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SEARCH_PATH,
        )
        await callback.answer("Отменено")

    @router.callback_query(F.data.startswith("sub:"))
    async def on_sub_pay(callback: CallbackQuery, bot: Bot) -> None:
        _, plan_id, method = callback.data.split(":", 2)
        plan = billing.PLANS_BY_ID.get(plan_id)
        if plan is None:
            await callback.answer()
            return

        if method == "crypto":
            await on_sub_pay_crypto(callback, plan)
            return

        if method == "stars":
            currency, provider_token, original_amount = "XTR", "", float(plan.price_stars)
        else:
            if not yookassa_provider_token:
                await callback.answer("Оплата картой пока не подключена", show_alert=True)
                return
            currency, provider_token, original_amount = "RUB", yookassa_provider_token, float(plan.price_rub)

        user = repo.get_user(callback.message.chat.id)
        discounted, discount_used = billing.referral_discount(original_amount, currency, user.referral_balance_rub)
        amount = round(discounted) if currency == "XTR" else round(discounted * 100)
        description = "Доступ к уведомлениям о вилках Арбитражного бота"
        if discount_used > 0:
            description += f" (скидка за рефералов: -{discount_used:.0f})"

        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Подписка на {plan.label}",
            description=description,
            payload=plan.id,
            provider_token=provider_token,
            currency=currency,
            prices=[LabeledPrice(label=plan.label, amount=amount)],
        )
        await callback.answer()

    async def on_sub_pay_crypto(callback: CallbackQuery, plan: billing.Plan) -> None:
        if crypto_pay_client is None:
            await callback.answer("Оплата криптой пока не подключена", show_alert=True)
            return

        user = repo.get_user(callback.message.chat.id)
        discounted, discount_used = billing.referral_discount(
            plan.price_usdt, "USDT", user.referral_balance_rub
        )
        description = f"Подписка на {plan.label} — Арбитражный бот"
        if discount_used > 0:
            description += f" (скидка за рефералов: -{discount_used:.2f} USDT)"

        try:
            invoice = await crypto_pay_client.create_invoice(
                asset="USDT",
                amount=discounted,
                description=description,
                payload=f"{callback.message.chat.id}:{plan.id}",
            )
        except Exception:
            logger.exception("Failed to create CryptoBot invoice for chat_id=%s", callback.message.chat.id)
            await callback.answer("Не удалось создать счёт, попробуйте позже", show_alert=True)
            return

        text = (
            f"💎 <b>ОПЛАТА КРИПТОЙ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Тариф: <b>{plan.label}</b>\nСумма: <b>{discounted:g} USDT</b>\n\n"
            "Оплатите по кнопке ниже, затем нажмите «✅ Проверить оплату»."
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="💎 Оплатить", url=invoice["bot_invoice_url"])],
                [_btn("✅ Проверить оплату", f"crypto_check:{invoice['invoice_id']}")],
                [_btn("◀️ Назад", NAV_SUBSCRIPTION)],
            ]
        )
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("crypto_check:"))
    async def on_crypto_check(callback: CallbackQuery, bot: Bot) -> None:
        if crypto_pay_client is None:
            await callback.answer()
            return
        invoice_id = int(callback.data.split(":", 1)[1])

        try:
            invoice = await crypto_pay_client.get_invoice(invoice_id)
        except Exception:
            logger.exception("Failed to check CryptoBot invoice_id=%s", invoice_id)
            await callback.answer("Не удалось проверить оплату, попробуйте ещё раз", show_alert=True)
            return

        if invoice is None or invoice.get("status") != "paid":
            await callback.answer("Пока не оплачено — оплатите и нажмите ещё раз", show_alert=True)
            return

        payload = invoice.get("payload") or ""
        payload_chat_id, _, plan_id = payload.partition(":")
        plan = billing.PLANS_BY_ID.get(plan_id)
        if not payload_chat_id.isdigit() or int(payload_chat_id) != callback.message.chat.id or plan is None:
            logger.warning("CryptoBot invoice_id=%s payload mismatch: %r", invoice_id, payload)
            await callback.answer("Не удалось сопоставить платёж", show_alert=True)
            return

        # A repeated tap of "Проверить оплату" after it already succeeded once must not
        # extend the subscription twice -- record_payment's INSERT OR IGNORE on the unique
        # charge id is what makes that safe, so check it *before* extending, not after.
        already_credited = repo.has_payment(f"cryptobot:{invoice_id}")
        if not already_credited:
            repo.extend_subscription(callback.message.chat.id, plan.days)
            repo.record_payment(
                callback.message.chat.id, plan.id, "cryptobot", float(invoice["amount"]), "USDT", f"cryptobot:{invoice_id}"
            )
            discount_used = max(0.0, plan.price_usdt - float(invoice["amount"]))
            if discount_used > 0:
                repo.consume_referral_balance(
                    callback.message.chat.id, billing.to_rub_equivalent(discount_used, "USDT")
                )
            buyer = repo.get_user(callback.message.chat.id)
            if buyer.referred_by is not None:
                commission_rub = billing.referral_commission_rub(float(invoice["amount"]), "USDT")
                repo.credit_referral_balance(buyer.referred_by, commission_rub)

        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _subscription_view(
            user, bool(yookassa_provider_token), crypto_pay_client is not None, admin_chat_ids
        )
        text = "✅ <b>Оплата получена, подписка продлена!</b>\n\n" + text
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SUBSCRIPTION_PATH,
        )
        await callback.answer("Оплачено!")

    @router.pre_checkout_query()
    async def on_pre_checkout(query: PreCheckoutQuery) -> None:
        await query.answer(ok=True)

    @router.message(F.successful_payment)
    async def on_successful_payment(message: Message, bot: Bot) -> None:
        payment = message.successful_payment
        plan = billing.PLANS_BY_ID.get(payment.invoice_payload)
        if plan is None:
            return
        provider = "stars" if payment.currency == "XTR" else "yookassa"
        charged = float(payment.total_amount) if provider == "stars" else payment.total_amount / 100
        repo.extend_subscription(message.chat.id, plan.days)
        repo.record_payment(
            message.chat.id, plan.id, provider, charged, payment.currency, payment.telegram_payment_charge_id
        )

        # Referral bookkeeping: the discount this buyer just spent comes out of their own
        # balance; a fresh commission on the amount they actually paid goes to whoever
        # referred them (if anyone did).
        original = float(plan.price_stars) if provider == "stars" else float(plan.price_rub)
        discount_used = max(0.0, original - charged)
        if discount_used > 0:
            repo.consume_referral_balance(message.chat.id, billing.to_rub_equivalent(discount_used, payment.currency))

        buyer = repo.get_user(message.chat.id)
        if buyer.referred_by is not None:
            commission_rub = billing.referral_commission_rub(charged, payment.currency)
            repo.credit_referral_balance(buyer.referred_by, commission_rub)

        await message.answer(f"✅ Подписка продлена на {plan.label}. Спасибо!")
        await _render_dashboard(bot, repo, message.chat.id, admin_chat_ids)

    @router.callback_query(F.data == NAV_SEARCH)
    async def on_nav_search(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(
            bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard,
            photo_path=BANNER_SEARCH_PATH,
        )
        await callback.answer()

    @router.message(Settings.waiting_bankroll)
    async def on_bankroll_value(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = message.text or ""
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        try:
            amount = float(raw.replace(",", "."))
            if amount <= 0:
                raise ValueError
        except ValueError:
            text, keyboard = _bankroll_prompt_view(error="Нужно число больше нуля")
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        repo.set_bankroll(message.chat.id, amount)
        await state.clear()
        user = repo.get_user(message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=BANNER_SEARCH_PATH)

    @router.message(Settings.waiting_threshold)
    async def on_threshold_value(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = message.text or ""
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        try:
            pct = float(raw.replace(",", "."))
            if pct < 0:
                raise ValueError
        except ValueError:
            text, keyboard = _input_prompt_view(
                "📊 Введите минимальный процент прибыли для уведомления:", "1.5", error="Нужно неотрицательное число"
            )
            await _render(
                bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=BANNER_THRESHOLD_PATH
            )
            return

        repo.set_min_profit_pct(message.chat.id, pct)
        await state.clear()
        user = repo.get_user(message.chat.id)
        text, keyboard = _search_view(user, latest_state, poll_interval_seconds)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=BANNER_SEARCH_PATH)

    @router.message(Settings.waiting_calc_bankroll)
    async def on_calc_bankroll_value(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = message.text or ""
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        try:
            bankroll = float(raw.replace(",", "."))
            if bankroll <= 0:
                raise ValueError
        except ValueError:
            text, keyboard = _calculator_bankroll_prompt(error="Нужно число больше нуля")
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        await state.update_data(calc_bankroll=bankroll)
        await state.set_state(Settings.waiting_calc_odds_a)
        text, keyboard = _calculator_odds_a_prompt()
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    @router.message(Settings.waiting_calc_odds_a)
    async def on_calc_odds_a_value(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = message.text or ""
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        try:
            odds_a = float(raw.replace(",", "."))
            if odds_a <= 1:
                raise ValueError
        except ValueError:
            text, keyboard = _calculator_odds_a_prompt(error="Коэффициент должен быть больше 1")
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        await state.update_data(calc_odds_a=odds_a)
        await state.set_state(Settings.waiting_calc_odds_b)
        text, keyboard = _calculator_odds_b_prompt()
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    @router.message(Settings.waiting_calc_odds_b)
    async def on_calc_odds_b_value(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = message.text or ""
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        try:
            odds_b = float(raw.replace(",", "."))
            if odds_b <= 1:
                raise ValueError
        except ValueError:
            text, keyboard = _calculator_odds_b_prompt(error="Коэффициент должен быть больше 1")
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        data = await state.get_data()
        bankroll = data["calc_bankroll"]
        odds_a = data["calc_odds_a"]

        await state.clear()
        text, keyboard = _calculator_result_view(bankroll, odds_a, odds_b)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    @router.message(Settings.waiting_support_message)
    async def on_support_message(message: Message, state: FSMContext, bot: Bot) -> None:
        raw = (message.text or "").strip()
        try:
            await message.delete()
        except Exception:
            pass

        user = repo.get_user(message.chat.id)
        if not raw:
            text, keyboard = _support_prompt_view(error="Сообщение не может быть пустым")
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        who = f"@{message.from_user.username}" if message.from_user and message.from_user.username else "без username"
        header = f"✉️ <b>Новое сообщение от пользователя</b>\nchat_id: <code>{message.chat.id}</code> ({html.escape(who)})\n\n"
        forward_text = header + html.escape(raw) + "\n\n<i>Ответьте на это сообщение, чтобы отправить ответ пользователю.</i>"

        sent_to_any = False
        for admin_chat_id in admin_chat_ids:
            try:
                sent = await bot.send_message(admin_chat_id, forward_text, parse_mode="HTML")
                repo.record_support_message(admin_chat_id, sent.message_id, message.chat.id)
                sent_to_any = True
            except Exception:
                logger.exception("Failed to forward support message to admin_chat_id=%s", admin_chat_id)

        await state.clear()
        if sent_to_any:
            text, keyboard = _help_view()
            text = "✅ Сообщение отправлено менеджеру. Ответ придёт в этот чат.\n\n" + text
        else:
            text, keyboard = _help_view()
            text = "⚠️ Не удалось отправить сообщение менеджеру, попробуйте позже.\n\n" + text
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard, photo_path=BANNER_HELP_PATH)

    @router.message(F.reply_to_message, F.chat.id.in_(admin_chat_ids))
    async def on_support_reply(message: Message, bot: Bot) -> None:
        """An admin's plain Telegram reply to a forwarded support message (see
        on_support_message above) -- relayed back to that user as a message from the bot
        itself, not from the admin's personal account."""
        user_chat_id = repo.get_support_message_user(message.chat.id, message.reply_to_message.message_id)
        if user_chat_id is None:
            return  # a reply to something else entirely -- not ours to handle

        reply_text = message.text or message.caption
        if not reply_text:
            await message.reply("⚠️ Можно ответить только текстом.")
            return

        try:
            await bot.send_message(user_chat_id, f"✉️ <b>Ответ от менеджера:</b>\n\n{html.escape(reply_text)}", parse_mode="HTML")
            await message.reply("✅ Отправлено пользователю.")
        except Exception:
            logger.exception("Failed to relay support reply to user chat_id=%s", user_chat_id)
            await message.reply("⚠️ Не удалось отправить пользователю (возможно, заблокировал бота).")

    return router
