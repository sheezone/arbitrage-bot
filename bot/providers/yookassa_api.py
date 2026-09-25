"""ЮKassa REST API v3 (https://yookassa.ru/developers/api) -- used only for СБП.

Why not the Telegram-native invoice (YOOKASSA_PROVIDER_TOKEN)? Telegram's payment sheet
only takes bank cards; СБП needs a real ЮKassa payment with payment_method_data.type ==
"sbp", which hands back a confirmation_url that opens the bank app / NSPK QR page.

No webhook: same approach as cryptobot.py -- the payment's status is polled (a short
background poll right after creation, plus a "✅ Проверить оплату" button), so this
doesn't depend on the Mini App's cloudflared quick tunnel staying on one URL.

Auth: HTTP Basic shopId:secretKey (ЮKassa cabinet -> Интеграция -> Ключи API).
Every POST needs a unique Idempotence-Key so a network retry can't create a 2nd payment.
"""
from __future__ import annotations

import uuid

import httpx

BASE_URL = "https://api.yookassa.ru/v3"


class YooKassaError(Exception):
    pass


class YooKassaClient:
    def __init__(self, shop_id: str, secret_key: str, base_url: str = BASE_URL):
        self._client = httpx.AsyncClient(base_url=base_url, auth=(shop_id, secret_key), timeout=20.0)

    async def create_sbp_payment(
        self, amount_rub: float, description: str, return_url: str, metadata: dict[str, str]
    ) -> dict:
        """Returns the raw payment object -- `id` (poll it later) and
        `confirmation.confirmation_url` (the link to give the user)."""
        resp = await self._client.post(
            "/payments",
            headers={"Idempotence-Key": str(uuid.uuid4())},
            json={
                "amount": {"value": f"{amount_rub:.2f}", "currency": "RUB"},
                "payment_method_data": {"type": "sbp"},
                "confirmation": {"type": "redirect", "return_url": return_url},
                "capture": True,
                "description": description[:128],
                "metadata": metadata,
            },
        )
        if resp.status_code >= 400:
            raise YooKassaError(f"{resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def get_payment(self, payment_id: str) -> dict:
        resp = await self._client.get(f"/payments/{payment_id}")
        if resp.status_code >= 400:
            raise YooKassaError(f"{resp.status_code}: {resp.text[:500]}")
        return resp.json()

    async def close(self) -> None:
        await self._client.aclose()


def is_paid(payment: dict) -> bool:
    return payment.get("status") == "succeeded" and bool(payment.get("paid"))
