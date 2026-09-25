import asyncio
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.providers.yookassa_api import YooKassaClient, is_paid


def _client_with(handler) -> YooKassaClient:
    c = YooKassaClient("1472342", "test_secret")
    c._client = httpx.AsyncClient(
        base_url="https://api.yookassa.ru/v3", auth=("1472342", "test_secret"), transport=httpx.MockTransport(handler)
    )
    return c


def test_create_sbp_payment_sends_sbp_method_amount_and_idempotence_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["idem"] = request.headers.get("Idempotence-Key")
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"id": "p1", "status": "pending", "confirmation": {"confirmation_url": "https://pay"}})

    client = _client_with(handler)
    payment = asyncio.run(client.create_sbp_payment(299, "Подписка", "https://t.me/bot", {"chat_id": "1", "plan_id": "7d"}))

    assert payment["confirmation"]["confirmation_url"] == "https://pay"
    assert seen["body"]["payment_method_data"] == {"type": "sbp"}
    assert seen["body"]["amount"] == {"value": "299.00", "currency": "RUB"}
    assert seen["body"]["metadata"] == {"chat_id": "1", "plan_id": "7d"}
    assert seen["idem"]
    assert seen["auth"].startswith("Basic ")


def test_is_paid_requires_succeeded_and_paid():
    assert is_paid({"status": "succeeded", "paid": True})
    assert not is_paid({"status": "pending", "paid": False})
    assert not is_paid({"status": "waiting_for_capture", "paid": True})


def test_subscription_view_shows_sbp_button_when_enabled():
    from bot.db.repository import Repository
    from bot.handlers.commands import _subscription_view
    import tempfile, os

    repo = Repository(os.path.join(tempfile.mkdtemp(), "t.sqlite3"))
    repo.upsert_user(1)
    text, kb = _subscription_view(repo.get_user(1), yookassa_enabled=True, yk_sbp_enabled=True)
    labels = [b.text for row in kb.inline_keyboard for b in row]
    assert "⚡ СБП" in labels
    _, kb2 = _subscription_view(repo.get_user(1), yookassa_enabled=True)
    assert "⚡ СБП" not in [b.text for row in kb2.inline_keyboard for b in row]
