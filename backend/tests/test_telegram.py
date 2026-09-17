"""Тесты Telegram-уведомлений (§9): формат, отправка, гейтинг."""
from __future__ import annotations

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing
from flipper.config import DealConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _listing(ad_id=111, price_cents=7000, city="Flensburg", url="https://k.la/x"):
    with get_session() as s:
        s.add(
            Listing(
                ad_id=ad_id,
                title="t",
                status="active",
                price_cents=price_cents,
                location_city=city,
                url=url,
            )
        )
        s.commit()


def _analysis(**over) -> Analysis:
    base = dict(
        listing_id=111,
        flip_type="repair",
        device_json={"type": "laptop", "model": "MacBook Air 2017"},
        margin_eur=70.0,
        margin_pct=50.0,
        verdict="TAKE",
        market_working_eur=250.0,
        parts_cost_eur=30.0,
        action_ru="написать продавцу",
    )
    base.update(over)
    return Analysis(**base)


def test_format_contains_key_fields():
    from flipper.app.notify import telegram

    _listing()
    text = telegram.format_deal(_analysis())
    assert "TAKE" in text and "MacBook Air 2017" in text
    assert "70€" in text and "250€" in text
    assert "Flensburg" in text
    assert "https://k.la/x" in text


def test_send_message_noop_when_unconfigured(monkeypatch):
    from flipper.app.notify import telegram

    monkeypatch.setattr(telegram, "_configured", lambda: None)
    assert telegram.send_message("hi") is False


def test_send_message_posts_to_bot_api(monkeypatch):
    from flipper.app.notify import telegram

    monkeypatch.setattr(telegram, "_configured", lambda: ("TOKEN", "42"))
    captured = {}

    class _Resp:
        def raise_for_status(self):
            pass

    def _post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        return _Resp()

    monkeypatch.setattr(telegram.httpx, "post", _post)
    assert telegram.send_message("привет") is True
    assert captured["url"].endswith("/botTOKEN/sendMessage")
    assert captured["json"]["chat_id"] == "42"
    assert captured["json"]["text"] == "привет"


def test_notify_telegram_gates_by_verdict(monkeypatch):
    from flipper.app.notify import telegram

    _listing()
    monkeypatch.setattr(telegram, "_configured", lambda: ("TOKEN", "42"))
    sent = []
    monkeypatch.setattr(telegram, "send_message", lambda t: sent.append(t) or True)
    deal = DealConfig(min_margin_eur=40, notify_only_verdicts=["TAKE"])

    assert telegram.notify_telegram(_analysis(verdict="MAYBE", margin_eur=90.0), deal) is False
    assert sent == []

    assert telegram.notify_telegram(_analysis(verdict="TAKE", margin_eur=90.0), deal) is True
    assert len(sent) == 1


def test_notify_telegram_noop_when_unconfigured(monkeypatch):
    from flipper.app.notify import telegram

    _listing()
    monkeypatch.setattr(telegram, "_configured", lambda: None)
    deal = DealConfig(min_margin_eur=40, notify_only_verdicts=["TAKE"])
    assert telegram.notify_telegram(_analysis(), deal) is False
