"""Тесты Telegram-команд: /status /stats /pulse /help."""
from __future__ import annotations

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, CycleRun, Listing, STATUS_ACTIVE


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _seed():
    with get_session() as s:
        s.add(Listing(ad_id=1, title="item 1", price_cents=3000, status=STATUS_ACTIVE))
        s.add(Analysis(listing_id=1, verdict="TAKE", margin_eur=50, margin_pct=100))
        s.add(CycleRun(new_listings=3, analyses=1, outcome="ok"))
        s.commit()


def test_apply_updates_status_and_pulse(monkeypatch):
    from flipper.app.notify import bot

    _seed()
    replies = []
    monkeypatch.setattr(
        bot, "send_message", lambda text, **k: replies.append((k.get("chat_id"), text)) or True
    )
    monkeypatch.setattr(bot, "send_heartbeat_now", lambda: True)

    updates = [
        {"update_id": 1, "message": {"chat": {"id": 42}, "text": "/status"}},
        {"update_id": 2, "message": {"chat": {"id": 42}, "text": "/pulse"}},
        {"update_id": 3, "message": {"chat": {"id": 42}, "text": "просто текст"}},
    ]
    offset = bot.apply_updates(updates, 0)
    assert offset == 4
    assert len(replies) == 2
    assert replies[0][0] == 42
    assert "активных: <b>1</b>" in replies[0][1] and "TAKE=1" in replies[0][1]
    assert "пульс отправлен" in replies[1][1]


def test_help_replies_command_list(monkeypatch):
    from flipper.app.notify import bot

    replies = []
    monkeypatch.setattr(bot, "send_message", lambda text, **k: replies.append(text) or True)

    bot.handle_command("/help", 42)
    assert len(replies) == 1
    assert "/status" in replies[0] and "/pulse" in replies[0]


def test_recent_lists_deals(monkeypatch):
    from flipper.app.notify import bot

    _seed()  # listing 1: TAKE +50€, и CycleRun
    replies = []
    monkeypatch.setattr(bot, "send_message", lambda text, **k: replies.append(text) or True)

    bot.handle_command("/recent", 42)
    text = replies[0]
    assert "Последние лоты" in text
    assert "TAKE" in text and "<code>1</code>" in text


def test_deal_shows_full_verdict(monkeypatch):
    from flipper.app.notify import bot

    with get_session() as s:
        s.add(Listing(ad_id=7, title="iPhone 12 defekt", price_cents=15000,
                      status=STATUS_ACTIVE, location_city="Flensburg",
                      url="https://x/7"))
        s.add(Analysis(listing_id=7, verdict="TAKE", margin_eur=120, margin_pct=80,
                       flip_type="repair", parts_cost_eur=20, market_working_eur=300,
                       confidence=0.9, model_used="qwen3.8-27b",
                       device_json={"model": "iPhone 12 64GB"},
                       pros_ru_json=["лёгкий ремонт", "ездит"], cons_ru_json=["царапины"],
                       action_ru="брать сразу, ремонт дисплея"))
        s.commit()

    replies = []
    monkeypatch.setattr(bot, "send_message", lambda text, **k: replies.append(text) or True)

    bot.handle_command("/deal 7", 42)
    text = replies[0]
    assert "iPhone 12 64GB" in text
    assert "TAKE +120€" in text
    assert "рынок <b>300€</b>" in text
    assert "Плюсы" in text and "лёгкий ремонт" in text
    assert "Минусы" in text and "царапины" in text
    assert "брать сразу" in text


def test_deal_unknown_id(monkeypatch):
    from flipper.app.notify import bot

    replies = []
    monkeypatch.setattr(bot, "send_message", lambda text, **k: replies.append(text) or True)

    bot.handle_command("/deal 999999", 42)
    assert "нет в базе" in replies[0]


def test_deal_missing_argument(monkeypatch):
    from flipper.app.notify import bot

    replies = []
    monkeypatch.setattr(bot, "send_message", lambda text, **k: replies.append(text) or True)

    bot.handle_command("/deal", 42)
    assert "/deal" in replies[0] and "id" in replies[0]


def test_unknown_command_ignored(monkeypatch):
    from flipper.app.notify import bot

    sent = []
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: sent.append(a) or True)
    assert bot.apply_updates(
        [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "/whatever"}}], 0
    ) == 2
    assert sent == []