"""Crediting logic shared by the button bot (handlers/commands.py) and the Mini App
(webapp/api.py), so a payment is extended/recorded exactly the same way no matter which
UI started it."""
from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot

from bot.core import billing
from bot.core.emoji import vi
from bot.db.repository import Repository
from bot.providers.yookassa_api import YooKassaClient
from bot.providers.yookassa_api import is_paid as yookassa_is_paid

logger = logging.getLogger(__name__)

YK_SBP_POLL_INTERVAL_SECONDS = 10
YK_SBP_POLL_TOTAL_SECONDS = 15 * 60


def credit_yookassa_sbp(repo: Repository, chat_id: int, payment: dict) -> billing.Plan | None:
    """Extend the subscription for a succeeded ЮKassa СБП payment. Returns the plan if the
    payment belongs to `chat_id` and is (now or already) credited, else None. Idempotent
    on the payment id, so the bot's poll, the Mini App's poll and the manual "Проверить
    оплату" button can all race safely."""
    meta = payment.get("metadata") or {}
    plan = billing.PLANS_BY_ID.get(str(meta.get("plan_id", "")))
    if plan is None or str(meta.get("chat_id", "")) != str(chat_id):
        logger.warning("ЮKassa СБП payment %s metadata mismatch: %r", payment.get("id"), meta)
        return None
    charge_key = f"yookassa_sbp:{payment['id']}"
    if repo.has_payment(charge_key):
        return plan
    amount = float((payment.get("amount") or {}).get("value") or plan.price_rub)
    repo.extend_subscription(chat_id, plan.days)
    repo.record_payment(chat_id, plan.id, "yookassa_sbp", amount, "RUB", charge_key)
    discount_used = max(0.0, float(plan.price_rub) - amount)
    if discount_used > 0:
        repo.consume_referral_balance(chat_id, discount_used)
    buyer = repo.get_user(chat_id)
    if buyer is not None and buyer.referred_by is not None:
        repo.credit_referral_balance(buyer.referred_by, billing.referral_commission_rub(amount, "RUB"))
    return plan


async def poll_yookassa_sbp(
    bot: Bot | None, repo: Repository, client: YooKassaClient, chat_id: int, payment_id: str
) -> None:
    """Background poll right after a СБП link is handed out: credits and messages the
    user as soon as it clears, so they don't have to press anything."""
    deadline = time.monotonic() + YK_SBP_POLL_TOTAL_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(YK_SBP_POLL_INTERVAL_SECONDS)
        if repo.has_payment(f"yookassa_sbp:{payment_id}"):
            return  # already credited elsewhere (button / Mini App poll)
        try:
            payment = await client.get_payment(payment_id)
        except Exception:
            logger.exception("ЮKassa СБП poll failed for payment %s", payment_id)
            continue
        if payment.get("status") == "canceled":
            return
        if yookassa_is_paid(payment):
            plan = credit_yookassa_sbp(repo, chat_id, payment)
            if plan is not None and bot is not None:
                try:
                    await bot.send_message(
                        chat_id, f"{vi('check_color')} Оплата по СБП получена, подписка продлена на {plan.label}. Спасибо!",
                        parse_mode="HTML",
                    )
                except Exception:
                    logger.exception("Failed to notify chat_id=%s about СБП payment", chat_id)
            return
