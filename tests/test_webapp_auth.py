import hashlib
import hmac
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.webapp.auth import validate_init_data

BOT_TOKEN = "123456:test-token"


def _make_init_data(user: dict, auth_date: int | None = None, bot_token: str = BOT_TOKEN, tamper: bool = False) -> str:
    fields = {
        "query_id": "AAEXample",
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if tamper:
        computed_hash = "0" * len(computed_hash)
    fields["hash"] = computed_hash
    return urlencode(fields)


def test_valid_init_data_returns_the_user():
    init_data = _make_init_data({"id": 941361300, "first_name": "Test"})
    user = validate_init_data(init_data, BOT_TOKEN)
    assert user is not None
    assert user["id"] == 941361300


def test_tampered_hash_is_rejected():
    init_data = _make_init_data({"id": 1}, tamper=True)
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_wrong_bot_token_is_rejected():
    init_data = _make_init_data({"id": 1}, bot_token="a-different-token")
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_expired_auth_date_is_rejected():
    old = int(time.time()) - 999999
    init_data = _make_init_data({"id": 1}, auth_date=old)
    assert validate_init_data(init_data, BOT_TOKEN, max_age_seconds=86400) is None


def test_missing_hash_is_rejected():
    assert validate_init_data("user=%7B%22id%22%3A1%7D&auth_date=123", BOT_TOKEN) is None


def test_empty_init_data_is_rejected():
    assert validate_init_data("", BOT_TOKEN) is None


def test_garbage_init_data_is_rejected():
    assert validate_init_data("not a valid query string %%%", BOT_TOKEN) is None


def test_missing_user_field_is_rejected():
    fields = {"auth_date": str(int(time.time()))}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    assert validate_init_data(urlencode(fields), BOT_TOKEN) is None
