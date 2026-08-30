"""Validates Telegram Mini App `initData` per Telegram's documented algorithm
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

    secret_key = HMAC_SHA256(key="WebAppData", msg=<bot_token>)
    computed_hash = hex(HMAC_SHA256(key=secret_key, msg=<data_check_string>))

`data_check_string` is every field except `hash` itself, sorted by key, joined as
"key=value" with "\n". A request is only trusted if computed_hash matches the `hash`
field Telegram appended -- this is the ONLY thing standing between "anyone who can guess
a chat_id" and this app's data/settings API, so treat any change here with real care and
keep constant-time comparison (hmac.compare_digest, not ==)."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

MAX_INIT_DATA_AGE_SECONDS = 86400  # Telegram itself recommends treating stale initData as invalid


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = MAX_INIT_DATA_AGE_SECONDS) -> dict | None:
    """Returns the parsed `user` object (has at least "id") on success, None on any
    failure (malformed, tampered, expired, or a bare/empty initData -- e.g. a browser
    hitting the app outside Telegram, which sends none at all)."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if time.time() - auth_date > max_age_seconds:
        return None

    user_json = pairs.get("user")
    if not user_json:
        return None
    try:
        user = json.loads(user_json)
    except ValueError:
        return None
    return user if isinstance(user, dict) and "id" in user else None
