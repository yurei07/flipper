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


def test_unknown_command_ignored(monkeypatch):
    from flipper.app.notify import bot

    sent = []
    monkeypatch.setattr(bot, "send_message", lambda *a, **k: sent.append(a) or True)
    assert bot.apply_updates(
        [{"update_id": 1, "message": {"chat": {"id": 1}, "text": "/whatever"}}], 0
    ) == 2
    assert sent == []