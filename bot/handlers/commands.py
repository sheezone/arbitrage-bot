"""The whole bot UI lives in a single message per chat that gets edited in place
(`_render`) rather than a growing feed of separate messages. /start is the only command
that (re)creates that message; every button press after that edits it, and free-text
answers (bankroll/threshold amounts) get deleted the instant they're read so the chat
stays down to one live message."""
from __future__ import annotations

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
    LabeledPrice,
    Message,
    PreCheckoutQuery,
    ReplyKeyboardRemove,
)

from bot.core import billing
from bot.core.arbitrage import calc_stakes
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
}

CATEGORIES: dict[str, list[str]] = {
    "esports": ["cs2", "dota2", "lol", "valorant"],
    "tennis": ["tennis"],
    "sports": ["basketball"],
}
CATEGORY_LABELS = {"esports": "🎮 Киберспорт", "tennis": "🎾 Теннис", "sports": "🏀 Баскетбол"}

NAV_DASHBOARD = "nav:dashboard"
NAV_SEARCH = "nav:search"
NAV_BANKROLL = "nav:bankroll"
NAV_THRESHOLD = "nav:threshold"
NAV_GAMES = "nav:games"
NAV_TOGGLE_ACTIVE = "nav:toggle_active"
NAV_HELP = "nav:help"
NAV_CANCEL = "nav:cancel"
NAV_SUBSCRIPTION = "nav:subscription"

View = tuple[str, InlineKeyboardMarkup]


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _dashboard_view(user: UserSettings, admin_chat_ids: frozenset[int] = frozenset()) -> View:
    now = datetime.now(timezone.utc)
    if not billing.has_access(user, now, admin_chat_ids):
        text = (
            "🎰 <b>АРБИТРАЖНЫЙ БОТ</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n\n"
            "⏳ Пробный период закончился.\n"
            "Оформите подписку, чтобы бот продолжил присылать уведомления о вилках."
        )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[_btn("💳 Подписка", NAV_SUBSCRIPTION)]])
        return text, keyboard

    status = "🟢 Активен" if user.is_active else "⏸️ На паузе"
    pause_label = "⏸️ Пауза" if user.is_active else "▶️ Возобновить"
    games = ", ".join(CATEGORY_LABELS[c] for c, gs in CATEGORIES.items() if set(gs) & set(user.watched_games))
    if billing.is_admin(user, admin_chat_ids):
        access_line = "♾️ Безлимитный доступ (админ)"
    else:
        left = billing.days_left(user, now)
        access_line = (
            f"⏳ Пробный период: осталось {left} дн."
            if billing.on_trial(user, now)
            else f"💳 Подписка активна: осталось {left} дн."
        )

    text = (
        "🎰 <b>АРБИТРАЖНЫЙ БОТ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Статус: {status}\n"
        f"{access_line}\n"
        f"💰 Банкролл: <b>{user.bankroll:.2f}</b>\n"
        f"📊 Порог прибыли: <b>{user.min_profit_pct:.2f}%</b>\n"
        f"🕹️ Отслеживаются: {games or '—'}\n\n"
        "Выберите действие ⬇️"
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [_btn("🔍 Искать сейчас", NAV_SEARCH)],
            [_btn("💰 Банкролл", NAV_BANKROLL), _btn("📊 Порог прибыли", NAV_THRESHOLD)],
            [_btn("🕹️ Игры", NAV_GAMES), _btn(pause_label, NAV_TOGGLE_ACTIVE)],
            [_btn("💳 Подписка", NAV_SUBSCRIPTION), _btn("ℹ️ Помощь", NAV_HELP)],
        ]
    )
    return text, keyboard


def _back_keyboard(extra: list[InlineKeyboardButton] | None = None) -> InlineKeyboardMarkup:
    rows = [extra] if extra else []
    rows.append([_btn("◀️ Назад", NAV_DASHBOARD)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _help_view() -> View:
    text = (
        "ℹ️ <b>КАК ЭТО РАБОТАЕТ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Слежу за коэффициентами киберспорта и тенниса у разных букмекеров "
        "и ищу вилки (arbitrage) — когда ставка на все исходы у разных БК "
        "даёт прибыль независимо от результата.\n\n"
        "Уведомления приходят автоматически. Кнопка «Искать сейчас» "
        "мгновенно показывает последний найденный результат."
    )
    return text, _back_keyboard()


def _games_view(user: UserSettings) -> View:
    selected = set(user.watched_games)
    rows = []
    for category, games in CATEGORIES.items():
        mark = "✅" if selected.issuperset(games) else "⬜"
        rows.append([_btn(f"{mark} {CATEGORY_LABELS[category]}", f"cat_toggle:{category}")])
    rows.append([_btn("◀️ Назад", NAV_DASHBOARD)])
    text = "🕹️ <b>ВЫБОР КАТЕГОРИЙ</b>\n━━━━━━━━━━━━━━━━━━━━\n\nОтметьте, что отслеживать:"
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
    rows.append([_btn("◀️ Назад", NAV_DASHBOARD)])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _input_prompt_view(label: str, example: str, error: str | None = None) -> View:
    text = f"{label}\nНапример: {example}"
    if error:
        text = f"⚠️ {error}\n\n{text}"
    return text, _back_keyboard([_btn("❌ Отмена", NAV_CANCEL)])


def _search_view(user: UserSettings, latest_state: LatestState) -> View:
    if latest_state.updated_at == 0:
        text = "🔍 <b>ПОИСК</b>\n━━━━━━━━━━━━━━━━━━━━\n\n⏳ Ещё идёт первая проверка, попробуйте через полминуты."
        return text, _back_keyboard([_btn("🔄 Обновить", NAV_SEARCH)])

    checked_at = datetime.fromtimestamp(latest_state.updated_at, tz=MOSCOW_TZ).strftime("%H:%M:%S МСК")
    matches = [
        m for m in latest_state.matches if m.game in user.watched_games and m.arb.profit_pct >= user.min_profit_pct
    ]

    lines = ["🔍 <b>ПОИСК</b>", "━━━━━━━━━━━━━━━━━━━━", ""]
    if not matches:
        lines.append(f"Сейчас подходящих вилок нет.\nДанные на {checked_at}.")
    else:
        lines.append(f"Найдено вилок: <b>{len(matches)}</b> (данные на {checked_at})\n")
        for m in matches:
            stakes = calc_stakes(user.bankroll, m.arb.best_odds)
            lines.append(f"<b>{GAME_LABELS.get(m.game, m.game.upper())}</b>: {m.team_a} vs {m.team_b}")
            lines.append(f"Прибыль: <b>{m.arb.profit_pct:.2f}%</b>")
            for outcome in m.arb.best_odds:
                lines.append(f"  {outcome.outcome_name}: {outcome.odds} @ {outcome.bookmaker}")
            lines.append("  Ставки: " + ", ".join(f"{k}: {v:.2f}" for k, v in stakes.items()))
            lines.append("")

    return "\n".join(lines), _back_keyboard([_btn("🔄 Обновить", NAV_SEARCH)])


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
) -> Router:
    @router.message(Command("start"))
    async def cmd_start(message: Message, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        is_new_user = repo.get_user(message.chat.id) is None
        repo.upsert_user(message.chat.id)
        user = repo.get_user(message.chat.id)

        # Drop any leftover custom reply-keyboard from an older version of this bot.
        cleanup = await message.answer("⏳", reply_markup=ReplyKeyboardRemove())
        try:
            await cleanup.delete()
        except Exception:
            pass

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

    @router.callback_query(F.data == NAV_DASHBOARD)
    async def on_nav_dashboard(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _render_dashboard(bot, repo, callback.message.chat.id, admin_chat_ids)
        await callback.answer()

    @router.callback_query(F.data == NAV_CANCEL)
    async def on_nav_cancel(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.clear()
        await _render_dashboard(bot, repo, callback.message.chat.id, admin_chat_ids)
        await callback.answer("Отменено")

    @router.callback_query(F.data == NAV_HELP)
    async def on_nav_help(callback: CallbackQuery, bot: Bot) -> None:
        text, keyboard = _help_view()
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_SUBSCRIPTION)
    async def on_nav_subscription(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _subscription_view(user, bool(yookassa_provider_token), admin_chat_ids)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("sub:"))
    async def on_sub_pay(callback: CallbackQuery, bot: Bot) -> None:
        _, plan_id, method = callback.data.split(":", 2)
        plan = billing.PLANS_BY_ID.get(plan_id)
        if plan is None:
            await callback.answer()
            return

        if method == "stars":
            currency, provider_token, amount = "XTR", "", plan.price_stars
        else:
            if not yookassa_provider_token:
                await callback.answer("Оплата картой пока не подключена", show_alert=True)
                return
            currency, provider_token, amount = "RUB", yookassa_provider_token, plan.price_rub * 100

        await bot.send_invoice(
            chat_id=callback.message.chat.id,
            title=f"Подписка на {plan.label}",
            description="Доступ к уведомлениям о вилках Арбитражного бота",
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
        amount = float(payment.total_amount) if provider == "stars" else payment.total_amount / 100
        repo.extend_subscription(message.chat.id, plan.days)
        repo.record_payment(
            message.chat.id, plan.id, provider, amount, payment.currency, payment.telegram_payment_charge_id
        )
        await message.answer(f"✅ Подписка продлена на {plan.label}. Спасибо!")
        await _render_dashboard(bot, repo, message.chat.id, admin_chat_ids)

    @router.callback_query(F.data == NAV_TOGGLE_ACTIVE)
    async def on_nav_toggle_active(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        repo.set_active(callback.message.chat.id, not user.is_active)
        await _render_dashboard(bot, repo, callback.message.chat.id, admin_chat_ids)
        await callback.answer("Пауза" if user.is_active else "Возобновлено")

    @router.callback_query(F.data == NAV_SEARCH)
    async def on_nav_search(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _search_view(user, latest_state)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_GAMES)
    async def on_nav_games(callback: CallbackQuery, bot: Bot) -> None:
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _games_view(user)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data.startswith("cat_toggle:"))
    async def on_category_toggle(callback: CallbackQuery, bot: Bot) -> None:
        category = callback.data.split(":", 1)[1]
        category_games = set(CATEGORIES[category])
        user = repo.get_user(callback.message.chat.id)
        selected = set(user.watched_games)
        if selected.issuperset(category_games):
            selected -= category_games
        else:
            selected |= category_games
        if not selected:
            await callback.answer("Нужно выбрать хотя бы одну категорию", show_alert=True)
            return
        repo.set_watched_games(callback.message.chat.id, sorted(selected))
        user = repo.get_user(callback.message.chat.id)
        text, keyboard = _games_view(user)
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

    @router.callback_query(F.data == NAV_BANKROLL)
    async def on_nav_bankroll(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_bankroll)
        text, keyboard = _input_prompt_view("💰 Введите новый банкролл числом:", "100")
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
        await _render_dashboard(bot, repo, message.chat.id, admin_chat_ids)

    @router.callback_query(F.data == NAV_THRESHOLD)
    async def on_nav_threshold(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
        await state.set_state(Settings.waiting_threshold)
        text, keyboard = _input_prompt_view("📊 Введите минимальный процент прибыли для уведомления:", "1.5")
        await _render(bot, repo, callback.message.chat.id, callback.message.message_id, text, keyboard)
        await callback.answer()

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
            await _render(bot, repo, message.chat.id, user.menu_message_id, text, keyboard)
            return

        repo.set_min_profit_pct(message.chat.id, pct)
        await state.clear()
        await _render_dashboard(bot, repo, message.chat.id, admin_chat_ids)

    return router
