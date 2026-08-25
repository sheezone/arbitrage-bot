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
makes the keyboard itself disappear on at least one client."""
from __future__ import annotations

import html
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
    KeyboardButton,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardMarkup,
)

from bot.core import billing
from bot.core.arbitrage import calc_stakes
from bot.core.monitor import GAME_EMOJI, format_match_start, format_odds_lines, format_stakes_lines, within_time_horizon
from bot.core.state import LatestState
from bot.db.repository import Repository, UserSettings
from bot.handlers.states import Settings

router = Router()

MOSCOW_TZ = timezone(timedelta(hours=3))
BANNER_PATH = Path(__file__).resolve().parent.parent / "assets" / "banner.png"

GAME_LABELS = {
    "cs2": "CS2",
    "dota2": "Dota 2",
    "lol": "LoL",
    "valorant": "Valorant",
    "tennis": "Теннис",
    "basketball": "Баскетбол",
    "football": "Футбол (тотал голов)",
    "hockey": "Хоккей (тотал шайб)",
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
NAV_HORIZON = "nav:horizon"
NAV_TOGGLE_ACTIVE = "nav:toggle_active"
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
    pause_label = "⏸️ Поставить на паузу" if user.is_active else "▶️ Возобновить"
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
        [_btn(pause_label, NAV_TOGGLE_ACTIVE)],
        [_btn("💳 Подписка", NAV_SUBSCRIPTION)],
        [_btn("🤝 Партнёрская программа", NAV_REFERRAL)],
        [_btn("ℹ️ Помощь", NAV_HELP)],
        [_btn("◀️ Назад", NAV_DASHBOARD)],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


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
        "⚽ Футбол, 🏒 Хоккей — тотал голов/шайб (Больше/Меньше), а не "
        "исход 1X2: в этих видах спорта возможна ничья, а тотал — это "
        "всегда ровно два исхода\n\n"
        "<b>Источники:</b> OddsPapi, Fonbet, PARI, Marathon, Baltbet, "
        "The Odds API, Zenit, Melbet, Leon, SureBet — чем больше источников, тем больше шанс "
        "найти расхождение в котировках. Отслеживаются все виды спорта сразу.\n\n"
        "<b>Настройки внутри «🔍 Поиск вилок»:</b>\n"
        "💰 Банкролл — сумма, под которую бот рассчитывает точные ставки "
        "на каждый исход\n"
        "📅 Период — показывать вилки только на матчи, которые начнутся в течение "
        "24 часов или позже\n\n"
        "<b>Внутри «👤 Мой профиль»:</b> пауза уведомлений, подписка и "
        f"партнёрская программа ({billing.REFERRAL_COMMISSION_PCT:.0%} с оплат "
        "приглашённых — в виде скидки на свою подписку).\n\n"
        "Уведомления приходят автоматически, как только находится "
        "вилка. Кнопка «🔍 Поиск вилок» мгновенно "
        "показывает последний найденный результат без нового опроса "
        "источников.\n\n"
        f"Первые {billing.TRIAL_DAYS} дн. бесплатно (пробный период), дальше — "
        "платная подписка, раздел «👤 Мой профиль» → «💳 Подписка»."
    )
    return text, _back_keyboard(target=NAV_DASHBOARD)


def _horizon_view(user: UserSettings) -> View:
    selected = set(user.time_horizons)
    rows = []
    for days, label in TIME_HORIZONS.items():
        mark = "✅" if days in selected else "⬜"
        rows.append([_btn(f"{mark} {label}", f"horizon:{days}")])
    rows.append([_btn("◀️ Назад", NAV_SEARCH)])
    text = (
        "📅 <b>ПЕРИОД ПОИСКА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        "Когда начинается матч. Можно отметить оба, чтобы видеть всё:"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _subscription_view(user: UserSettings, yookassa_enabled: bool, admin_chat_ids: frozenset[int] = frozenset()) -> View:
    now = datetime.now(timezone.utc)
    if billing.is_admin(user, admin_chat_ids):
        status = "♾️ Безлимитный доступ (админ)"
    else:
        left = billing.days_left(user, now)
        status = f"Осталось дней доступа: <b>{left}</b>" if left > 0 else "Доступ истёк"

    text = f"💳 <b>ПОДПИСКА</b>\n━━━━━━━━━━━━━━━━━━━━\n\n{status}\n\nВыберите тариф:"
    rows = []
    for plan in billing.PLANS:
        row = [_btn(f"{plan.label} — {plan.price_stars} ⭐", f"sub:{plan.id}:stars")]
        if yookassa_enabled:
            row.append(_btn(f"{plan.label} — {plan.price_rub}₽", f"sub:{plan.id}:rub"))
        rows.append(row)
    rows.append([_btn("◀️ Назад", NAV_PROFILE)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _input_prompt_view(label: str, example: str, error: str | None = None) -> View:
    text = f"{label}\nНапример: {example}"
    if error:
        text = f"⚠️ {error}\n\n{text}"
    return text, _back_keyboard([_btn("❌ Отмена", NAV_CANCEL)], target=NAV_SEARCH)


def _search_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔄 Обновить", NAV_SEARCH)],
            [_btn("💰 Банкролл", NAV_BANKROLL), _btn("📅 Период", NAV_HORIZON)],
            [_btn("◀️ Назад", NAV_DASHBOARD)],
        ]
    )


def _search_view(user: UserSettings, latest_state: LatestState) -> View:
    if latest_state.updated_at == 0:
        text = "🔍 <b>ПОИСК ВИЛОК</b>\n━━━━━━━━━━━━━━━━━━━━\n\n⏳ Ещё идёт первая проверка, попробуйте через полминуты."
        return text, _search_keyboard()

    checked_at = datetime.fromtimestamp(latest_state.updated_at, tz=MOSCOW_TZ).strftime("%H:%M:%S МСК")
    now = datetime.now(timezone.utc)
    matches = [m for m in latest_state.matches if within_time_horizon(m.start_time_utc, user.time_horizons, now)]

    lines = ["🔍 <b>ПОИСК ВИЛОК</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not matches:
        lines.append(f"Сейчас подходящих вилок нет.\nДанные на {checked_at}.")
    else:
        lines.append(f"Найдено вилок: <b>{len(matches)}</b> (данные на {checked_at})\n")
        for m in matches:
            stakes = calc_stakes(user.bankroll, m.arb.best_odds)
            emoji = GAME_EMOJI.get(m.game, "🏆")
            lines.append(f"{emoji} <b>{GAME_LABELS.get(m.game, m.game.upper())}</b>")
            lines.append(f"⚔️ <b>{html.escape(m.team_a)}</b> vs <b>{html.escape(m.team_b)}</b>")
            match_time = format_match_start(m.start_time_utc)
            if match_time:
                lines.append(f"🕒 {match_time}")
            lines.append(f"🚀 Прибыль: <b>{m.arb.profit_pct:.2f}%</b>")
            lines.append("")
            quote_lines = format_odds_lines(m.arb.best_odds) + ["", "💵 <b>Ставки:</b>"] + format_stakes_lines(stakes)
            lines.append("<blockquote>" + "\n".join(quote_lines) + "</blockquote>")
            lines.append("")

    return "\n".join(lines), _search_keyboard()


async def _render(
    bot: Bot, repo: Repository, chat_id: int, message_id: int | None, text: str, keyboard, *, photo: bool = False
) -> None:
    """Edit the tracked menu message in place; only send a new one if editing is
    impossible (first run, message type mismatch between photo/text, or the old
    message is gone/too old to edit)."""
    if message_id:
        try:
            if photo:
                await bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id, caption=text, reply_markup=keyboard, parse_mode="HTML"
                )
            else:
                await bot.edit_message_text(
                    chat_id=chat_id, message_id=message_id, text=text, reply_markup=keyboard, parse_mode="HTML"
                )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            try:
                await bot.delete_message(chat_id, message_id)
            except Exception:
                pass

    if photo:
        sent = await bot.send_photo(
            chat_id, FSInputFile(BANNER_PATH), caption=text, reply_markup=keyboard, parse_mode="HTML"
        )
    else:
        sent = await bot.send_message(chat_id, text, reply_markup=keyboard, parse_mode="HTML")
    repo.set_menu_message_id(chat_id, sent.message_id)


async def _render_dashboard(
    bot: Bot, repo: Repository, chat_id: int, admin_chat_ids: frozenset[int] = frozenset()
) -> None:
    user = repo.get_user(chat_id)
    text, keyboard = _dashboard_view(user, admin_chat_ids)
    await _render(bot, repo, chat_id, user.menu_message_id, text, keyboard, photo=True)


def register_handlers(
    repo: Repository,
    latest_state: LatestState,
    yookassa_provider_token: str = "",
    admin_chat_ids: frozenset[int] = frozenset(),
    bot_username: str = "",
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
            await message.answer(
                "👋 <b>Добро пожаловать в Арбитражный бот!</b>\n\n"
                f"Бот работает в тестовом режиме {billing.TRIAL_DAYS} дн. — полный "
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
        text, keyboard = _search_view(user, latest_state)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    @router.message(F.text == PROFILE_BUTTON_TEXT)
    async def on_profile_button(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _dismiss(message)
        user = repo.get_user(message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    @router.callback_query(F.data == NAV_PROFILE)
    async def on_nav_profile(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_HORIZON)
    async def on_nav_horizon(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _horizon_view(user)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
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
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_TOGGLE_ACTIVE)
    async def on_nav_toggle_active(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        repo.set_active(callback.message.chat.id, not user.is_active)
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _profile_view(user, admin_chat_ids)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer("Пауза" if not user.is_active else "Возобновлено")

    @router.callback_query(F.data == NAV_SUBSCRIPTION)
    async def on_nav_subscription(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _subscription_view(user, bool(yookassa_provider_token), admin_chat_ids)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_REFERRAL)
    async def on_nav_referral(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _referral_view(user, repo, bot_username)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_HELP)
    async def on_nav_help(callback: CallbackQuery, bot: Bot) -> None:
        text, keyboard = _help_view()
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_BANKROLL)
    async def on_nav_bankroll(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_bankroll)
        text, keyboard = _input_prompt_view("💰 Введите новый банкролл числом:", "100")
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
        text, keyboard = _search_view(user, latest_state)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer("Отменено")

    @router.callback_query(F.data.startswith("sub:"))
    async def on_sub_pay(callback: CallbackQuery, bot: Bot) -> None:
        _, plan_id, method = callback.data.split(":", 2)
        plan = billing.PLANS_BY_ID.get(plan_id)
        if plan is None:
            await callback.answer()
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
        text, keyboard = _search_view(user, latest_state)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
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
            text, keyboard = _input_prompt_view(
                "💰 Введите новый банкролл числом:", "100", error="Нужно число больше нуля"
            )
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        repo.set_bankroll(message.chat.id, amount)
        await state.clear()
        user = repo.get_user(message.chat.id)
        text, keyboard = _search_view(user, latest_state)
        await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)

    return router
