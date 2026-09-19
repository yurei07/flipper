"""Тесты «пульса» бота: сводка, периодичность, статус в settings."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, CycleRun, Listing, Setting


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def test_format_heartbeat_shows_stats():
    from flipper.app.notify.heartbeat import format_heartbeat

    text = format_heartbeat({"new": 2, "analyzed": 5, "takes": 1, "active": 10}, 180)
    assert "2" in text and "5" in text and "1" in text and "10" in text
    assert "3 ч" in text and "<b>" in text


def test_collect_stats_sums_period():
    from flipper.app.notify.heartbeat import collect_stats

    with get_session() as s:
        s.add(CycleRun(new_listings=3, analyses=1, outcome="ok"))
        s.add(CycleRun(new_listings=0, analyses=4, outcome="ok"))
        s.add(Analysis(listing_id=1, verdict="TAKE"))
        s.commit()

    st = collect_stats(datetime.utcnow() - timedelta(hours=1))
    assert st == {"new": 3, "analyzed": 5, "takes": 1, "active": 0}


def test_maybe_heartbeat_respects_interval(monkeypatch):
    from flipper.app.notify import heartbeat

    sent = []
    monkeypatch.setattr(heartbeat, "_telegram_ok", lambda: True)
    monkeypatch.setattr(
        heartbeat, "send_message", lambda t, **k: sent.append(t) or True
    )

    assert heartbeat.maybe_heartbeat(0) is False  # выключено
    assert sent == []

    assert heartbeat.maybe_heartbeat(180) is True  # первый пульс
    assert len(sent) == 1

    assert heartbeat.maybe_heartbeat(180) is False  # только что слали
    assert len(sent) == 1

    with get_session() as s:  # отмотали время -> снова пора
        row = s.get(Setting, "heartbeat_at")
        row.value = (datetime.utcnow() - timedelta(hours=4)).isoformat()
        s.commit()
    assert heartbeat.maybe_heartbeat(180) is True
    assert len(sent) == 2


def test_send_heartbeat_now_marks_sent(monkeypatch):
    from flipper.app.notify import heartbeat

    monkeypatch.setattr(heartbeat, "_telegram_ok", lambda: True)
    monkeypatch.setattr(heartbeat, "send_message", lambda t, **k: True)

    assert heartbeat.send_heartbeat_now() is True
    with get_session() as s:
        assert s.get(Setting, "heartbeat_at") is not None