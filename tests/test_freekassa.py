import hashlib

from bot.providers import freekassa


def test_build_payment_url_has_core_params_and_valid_signature():
    url = freekassa.build_payment_url(
        "12345",
        "secret1",
        order_id="fk-42-30d-1700000000",
        amount=999.0,
    )
    assert url.startswith("https://pay.fk.money/?")
    assert "m=12345" in url
    assert "oa=999.00" in url
    assert "o=fk-42-30d-1700000000" in url
    assert "currency=RUB" in url

    # Outgoing pay-link signature uses secret word 1 + currency (a different formula
    # from the notification signature, which uses secret word 2 and no currency).
    expected_sign = hashlib.md5(
        "12345:999.00:secret1:RUB:fk-42-30d-1700000000".encode("utf-8")
    ).hexdigest()
    assert f"s={expected_sign}" in url


def test_build_payment_url_includes_email_when_given():
    url = freekassa.build_payment_url(
        "12345", "secret1", order_id="fk-1-7d-1", amount=299.0, email="user@example.com"
    )
    assert "em=user%40example.com" in url


def test_signature_round_trips():
    data = {
        "MERCHANT_ORDER_ID": "fk-42-30d-1700000000",
        "AMOUNT": "999.00",
    }
    sign = freekassa.compute_notification_signature("12345", "999.00", "secret2", "fk-42-30d-1700000000")
    assert freekassa.verify_signature({**data, "SIGN": sign}, "12345", "secret2") is True


def test_signature_rejects_tampered_amount():
    sign = freekassa.compute_notification_signature("12345", "299.00", "secret2", "fk-1-7d-1")
    tampered = {"MERCHANT_ORDER_ID": "fk-1-7d-1", "AMOUNT": "1.00", "SIGN": sign}
    assert freekassa.verify_signature(tampered, "12345", "secret2") is False


def test_signature_rejects_wrong_merchant():
    sign = freekassa.compute_notification_signature("12345", "299.00", "secret2", "fk-1-7d-1")
    data = {"MERCHANT_ORDER_ID": "fk-1-7d-1", "AMOUNT": "299.00", "SIGN": sign}
    assert freekassa.verify_signature(data, "99999", "secret2") is False


def test_signature_rejects_empty_sign():
    assert freekassa.verify_signature({"MERCHANT_ORDER_ID": "fk-1-7d-1", "AMOUNT": "299.00"}, "12345", "secret2") is False


def test_signature_is_case_insensitive():
    sign = freekassa.compute_notification_signature("12345", "299.00", "secret2", "fk-1-7d-1")
    data = {"MERCHANT_ORDER_ID": "fk-1-7d-1", "AMOUNT": "299.00", "SIGN": sign.upper()}
    assert freekassa.verify_signature(data, "12345", "secret2") is True
