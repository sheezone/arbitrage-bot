"""Prodamus (payform.ru) — СБП / card payments by hosted payment link, without a
Telegram-native payment provider.

Flow: the bot builds a payform URL (build_payment_url) and shows it as a button. The
user pays; Prodamus POSTs a form-urlencoded notification to `urlNotification`
(our /api/prodamus/webhook) with an HMAC-SHA256 signature in the `Sign` header.
verify_signature() reproduces Prodamus's own `Hmac` algorithm to check it, then the
route extends the subscription.

No network calls happen here -- the link is just a signed-by-Prodamus URL, and status
comes from the webhook (has_payment(f"prodamus:{order_id}") is the source of truth for
the "check payment" button). The signature algorithm mirrors Prodamus's PHP `Hmac`
class: every value stringified, dict keys sorted recursively, json_encode with
UNESCAPED_UNICODE|UNESCAPED_SLASHES, then hash_hmac('sha256', ..., key). It must be
verified against a real notification on first setup -- on mismatch the route logs the
computed vs received digest.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
from urllib.parse import urlencode


def build_payment_url(
    form_url: str,
    *,
    order_id: str,
    amount: float,
    product_name: str,
    notification_url: str,
    success_url: str = "",
    customer_extra: str = "",
    npd_income_type: str = "",
) -> str:
    """A payform link for a single line item. `amount` is roubles (Prodamus wants a
    decimal string). `npd_income_type` (e.g. "FROM_INDIVIDUAL") turns on Prodamus's
    auto-fiscalization to ФНС for self-employed sellers -- leave empty otherwise."""
    params = {
        "do": "pay",
        "order_id": order_id,
        "products[0][name]": product_name,
        "products[0][price]": f"{amount:.2f}",
        "products[0][quantity]": "1",
        "urlNotification": notification_url,
        "sys": "telegram-bot",
    }
    if success_url:
        params["urlSuccess"] = success_url
        params["urlReturn"] = success_url
    if customer_extra:
        params["customer_extra"] = customer_extra
    if npd_income_type:
        params["npd_income_type"] = npd_income_type
    return f"{form_url.rstrip('/')}/?{urlencode(params)}"


_KEY_RE = re.compile(r"^([^\[\]]+)((?:\[[^\[\]]*\])*)$")
_SUBKEY_RE = re.compile(r"\[([^\[\]]*)\]")


def parse_php_form(pairs: list[tuple[str, str]]) -> dict:
    """Reassemble PHP-style nested form fields ("products[0][name]") into nested
    dict/list structure, the shape Prodamus signs over."""
    root: dict = {}
    for raw_key, value in pairs:
        m = _KEY_RE.match(raw_key)
        if not m:
            root[raw_key] = value
            continue
        head, rest = m.group(1), m.group(2)
        path = [head] + _SUBKEY_RE.findall(rest)
        node = root
        for i, key in enumerate(path):
            last = i == len(path) - 1
            nxt = path[i + 1] if not last else None
            container_is_list = isinstance(node, list)
            idx = int(key) if container_is_list or key.isdigit() else key
            if last:
                if isinstance(node, list):
                    _list_set(node, int(key), value)
                else:
                    node[idx] = value
            else:
                child_should_be_list = nxt == "" or (nxt is not None and nxt.isdigit())
                if isinstance(node, list):
                    existing = node[int(key)] if int(key) < len(node) else None
                    if not isinstance(existing, (dict, list)):
                        existing = [] if child_should_be_list else {}
                        _list_set(node, int(key), existing)
                    node = existing
                else:
                    if not isinstance(node.get(idx), (dict, list)):
                        node[idx] = [] if child_should_be_list else {}
                    node = node[idx]
    return root


def _list_set(lst: list, idx: int, value) -> None:
    while len(lst) <= idx:
        lst.append(None)
    lst[idx] = value


def _normalise(value):
    if isinstance(value, dict):
        return {str(k): _normalise(value[k]) for k in sorted(value, key=str)}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    if isinstance(value, bool):
        return "1" if value else ""
    if value is None:
        return ""
    return str(value)


def compute_signature(data: dict, secret_key: str) -> str:
    payload = json.dumps(_normalise(data), ensure_ascii=False, separators=(",", ":"))
    return hmac.new(secret_key.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_signature(data: dict, secret_key: str, received_sign: str) -> bool:
    if not received_sign:
        return False
    payload = {k: v for k, v in data.items() if k.lower() not in ("sign", "signature")}
    return hmac.compare_digest(compute_signature(payload, secret_key), received_sign.strip().lower())


def is_paid(data: dict) -> bool:
    return str(data.get("payment_status", "")).lower() == "success"
