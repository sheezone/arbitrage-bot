"""FreeKassa (freekassa.ru / pay.freekassa.ru) -- an aggregator payment gateway offering
SBP/card/crypto without requiring the seller to have an ИП/self-employed registration
(unlike Prodamus, which needs НПД status for its auto-fiscalization). Same shape as
prodamus.py: build a signed pay-page link, then a POST notification (IPN) with its own
signature lands on our webhook once the payment clears.

Flow: build_payment_url() makes a link to FreeKassa's hosted pay page (the SCI --
"Simple Custom Integration" -- scheme: GET params, no API key needed to *start* a
payment). FreeKassa POSTs form-urlencoded fields to the merchant's configured
"notification URL" (set once in the FreeKassa dashboard, not per-payment) once a payment
succeeds; verify_signature() reproduces their MD5 scheme to check it's genuinely from
FreeKassa before crediting anyone.

Two different secret words are used for two different signatures (this is FreeKassa's
own split, not ours): SECRET_WORD_1 signs the outgoing pay link, SECRET_WORD_2 signs the
incoming IPN notification -- mixing them up silently breaks verification, so config.py
keeps them as two separate values, mirroring the "SECRET WORD 1/2" fields the FreeKassa
merchant dashboard itself shows.

Must be verified against one real test notification on first setup -- FreeKassa's docs
are the primary source here (not fully executable without a live merchant account to
test against), so on a signature mismatch the webhook route logs the computed vs
received digest for a quick diff, same as prodamus.py's route does.
"""
from __future__ import annotations

import hashlib
from urllib.parse import urlencode

PAY_URL = "https://pay.freekassa.ru/"


def build_payment_url(
    merchant_id: str,
    secret_word_1: str,
    *,
    order_id: str,
    amount: float,
    currency: str = "RUB",
    email: str = "",
) -> str:
    """A FreeKassa SCI pay-page link. `amount` is roubles (FreeKassa wants a plain
    decimal string, 2dp). Signature per FreeKassa docs:
    md5(f"{merchant_id}:{amount}:{secret_word_1}:{currency}:{order_id}")."""
    amount_str = f"{amount:.2f}"
    sign = hashlib.md5(
        f"{merchant_id}:{amount_str}:{secret_word_1}:{currency}:{order_id}".encode("utf-8")
    ).hexdigest()
    params = {
        "m": merchant_id,
        "oa": amount_str,
        "currency": currency,
        "o": order_id,
        "s": sign,
    }
    if email:
        params["em"] = email
    return f"{PAY_URL}?{urlencode(params)}"


def compute_notification_signature(merchant_id: str, amount: str, secret_word_2: str, order_id: str) -> str:
    """Per FreeKassa docs: md5(f"{merchant_id}:{amount}:{secret_word_2}:{merchant_order_id}")."""
    return hashlib.md5(f"{merchant_id}:{amount}:{secret_word_2}:{order_id}".encode("utf-8")).hexdigest()


def verify_signature(data: dict, merchant_id: str, secret_word_2: str) -> bool:
    received = str(data.get("SIGN") or data.get("sign") or "")
    if not received:
        return False
    amount = str(data.get("AMOUNT") or data.get("amount") or "")
    order_id = str(data.get("MERCHANT_ORDER_ID") or data.get("merchant_order_id") or "")
    expected = compute_notification_signature(merchant_id, amount, secret_word_2, order_id)
    return expected.lower() == received.strip().lower()
