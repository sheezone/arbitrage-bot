"""Token-budget alert for the Anthropic gateway key (owner 2026-10-07: "скидывай
админам уведомление когда осталось 5 млн токенов"). The reseller exposes no balance
API -- every GET on the gateway 405s, only POST /v1/messages works -- so the bot tracks
its own cumulative token spend against a budget the owner told us (100,000,000 for the
key set 2026-10-07) and nudges the admins once when that's close to empty.

This counts what THIS process has spent since the counter started, not the key's real
server-side balance: swapping in a fresh key (a new top-up) doesn't reset it on its own
-- call bot.db.repository.Repository.reset_ai_token_usage() when that happens.
"""
from __future__ import annotations

import logging

from bot.db.repository import Repository

logger = logging.getLogger(__name__)

TOKEN_BUDGET = 100_000_000
ALERT_REMAINING = 5_000_000
ALERT_KEY = f"token_budget_{ALERT_REMAINING}"
USAGE_FIELDS = ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")


def _usage_tokens(response) -> int:
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return sum(getattr(usage, f, 0) or 0 for f in USAGE_FIELDS)


async def record(repo: Repository, response, bot=None, admin_chat_ids: frozenset[int] = frozenset()) -> None:
    n = _usage_tokens(response)
    if not n:
        return
    total = repo.add_ai_token_usage(n)
    remaining = TOKEN_BUDGET - total
    if remaining > ALERT_REMAINING or bot is None or repo.alert_sent(ALERT_KEY):
        return
    repo.mark_alert_sent(ALERT_KEY)
    text = (f"⚠️ На ключе ИИ осталось примерно {max(0, remaining):,} токенов из "
            f"{TOKEN_BUDGET:,} (меньше 5 млн) — скоро понадобится пополнение.").replace(",", " ")
    for admin_id in admin_chat_ids:
        try:
            await bot.send_message(admin_id, text)
        except Exception:
            logger.exception("Token-budget alert failed for admin %s", admin_id)
