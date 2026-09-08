from bot.providers import prodamus


def test_build_payment_url_has_core_params():
    url = prodamus.build_payment_url(
        "https://shop.payform.ru",
        order_id="sbp-42-30d-1700000000",
        amount=999.0,
        product_name="Подписка на 30 дней",
        notification_url="https://x.example/api/prodamus/webhook",
        success_url="https://t.me/Lineyka111_bot",
        customer_extra="chat_id=42",
        npd_income_type="FROM_INDIVIDUAL",
    )
    assert url.startswith("https://shop.payform.ru/?")
    assert "do=pay" in url
    assert "order_id=sbp-42-30d-1700000000" in url
    assert "products%5B0%5D%5Bprice%5D=999.00" in url
    assert "urlNotification=https%3A%2F%2Fx.example%2Fapi%2Fprodamus%2Fwebhook" in url
    assert "npd_income_type=FROM_INDIVIDUAL" in url


def test_parse_php_form_rebuilds_nested_products():
    pairs = [
        ("order_num", "sbp-42-30d-1700000000"),
        ("sum", "999.00"),
        ("payment_status", "success"),
        ("products[0][name]", "Подписка на 30 дней"),
        ("products[0][price]", "999.00"),
        ("products[0][quantity]", "1"),
    ]
    data = prodamus.parse_php_form(pairs)
    assert data["order_num"] == "sbp-42-30d-1700000000"
    assert data["payment_status"] == "success"
    assert data["products"] == [
        {"name": "Подписка на 30 дней", "price": "999.00", "quantity": "1"}
    ]


def test_signature_round_trips():
    key = "secret-key-123"
    data = {
        "order_num": "sbp-42-30d-1700000000",
        "sum": "999.00",
        "payment_status": "success",
        "products": [{"name": "Подписка", "price": "999.00", "quantity": "1"}],
    }
    sign = prodamus.compute_signature(data, key)
    assert prodamus.verify_signature({**data, "sign": sign}, key, sign) is True
    assert prodamus.verify_signature(data, key, sign) is True


def test_signature_rejects_tampered_amount():
    key = "secret-key-123"
    data = {"order_num": "sbp-1-7d-1", "sum": "299.00", "payment_status": "success"}
    sign = prodamus.compute_signature(data, key)
    tampered = {**data, "sum": "1.00"}
    assert prodamus.verify_signature(tampered, key, sign) is False


def test_signature_rejects_empty_sign():
    assert prodamus.verify_signature({"a": "1"}, "k", "") is False


def test_is_paid():
    assert prodamus.is_paid({"payment_status": "success"}) is True
    assert prodamus.is_paid({"payment_status": "pending"}) is False
    assert prodamus.is_paid({}) is False
