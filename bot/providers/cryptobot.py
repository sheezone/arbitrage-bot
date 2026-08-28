"""@CryptoBot's Crypto Pay API (https://help.crypt.bot/crypto-pay-api) -- lets this bot
accept crypto (USDT here) without holding any wallet/private key itself: money lands on
the app's @CryptoBot balance, withdrawn manually from there whenever.

No webhook server here (this bot only long-polls Telegram, no public HTTP endpoint to
receive one) -- instead, an invoice's status is checked on demand when the user taps
"✅ Проверить оплату" in the subscription flow (see bot/handlers/commands.py). Simpler
than standing up a webhook receiver, at the cost of needing the user to click a button
after paying rather than being notified the instant it clears.

Auth: `Crypto-Pay-API-Token` header, obtained by messaging @CryptoBot -> Crypto Pay ->
Create App. Sandbox/testnet exists at a different base URL (testnet-pay.crypt.bot) with
its own separate token, useful for testing before going live -- not used here, but
CryptoPayClient(base_url=...) can point at it."""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://pay.crypt.bot/api"


class CryptoPayError(Exception):
    pass


def _format_amount(amount: float) -> str:
    """Crypto Pay API rejects trailing zeros/a trailing decimal point in `amount`
    (confirmed against its docs) -- %.8f always has both, so strip them back off."""
    return f"{amount:.8f}".rstrip("0").rstrip(".")


class CryptoPayClient:
    def __init__(self, api_token: str, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(
            base_url=base_url, headers={"Crypto-Pay-API-Token": api_token}, timeout=20.0
        )

    async def create_invoice(
        self, asset: str, amount: float, description: str, payload: str, expires_in: int = 3600
    ) -> dict:
        """Returns the raw `result` object -- notably `invoice_id` (int, used to poll
        status later) and `bot_invoice_url` (the link to hand the user to pay)."""
        resp = await self._client.post(
            "/createInvoice",
            json={
                "asset": asset,
                "amount": _format_amount(amount),
                "description": description[:1024],
                "payload": payload[:4096],
                "expires_in": expires_in,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data))
        return data["result"]

    async def get_invoice(self, invoice_id: int) -> dict | None:
        """Returns the raw invoice item (status is "active"/"paid"/"expired", plus the
        `payload` this code set when creating it), or None if not found at all (shouldn't
        normally happen for one this code just created)."""
        resp = await self._client.get("/getInvoices", params={"invoice_ids": str(invoice_id)})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise CryptoPayError(str(data))
        items = data["result"].get("items") or []
        return items[0] if items else None

    async def close(self) -> None:
        await self._client.aclose()
