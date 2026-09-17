"""Тесты anti-ban: cooldown, бюджет запросов, install-id."""
from __future__ import annotations

import pytest

from flipper.app.cooldown import clear_cooldown, get_cooldown, in_cooldown, set_cooldown
from flipper.app.db import init_db


@pytest.fixture
def _db():
    init_db("sqlite:///:memory:")
    yield
    clear_cooldown()


# ---------- cooldown ----------

def test_no_cooldown_by_default(_db):
    assert in_cooldown() is False


def test_set_get_cooldown(_db):
    until = set_cooldown(minutes=60)
    assert in_cooldown() is True
    assert get_cooldown() == until


def test_clear_cooldown(_db):
    set_cooldown(minutes=1)
    clear_cooldown()
    assert in_cooldown() is False


# ---------- бюджет запросов → ScraperBlocked ----------

def test_budget_exceeded_raises_blocked(_db):
    from flipper.app.scraper.client import ScraperBlocked, ScraperClient

    client = ScraperClient(delay_range=(0, 0), max_requests=2)
    # имитируем исчерпание бюджета через приватный тикер
    client._tick()
    client._tick()
    with pytest.raises(ScraperBlocked):
        client._tick()
    assert client.requests_used == 3


def test_empty_streak_raises_blocked(_db):
    from flipper.app.scraper.client import ScraperBlocked, ScraperClient

    client = ScraperClient(delay_range=(0, 0), max_requests=100)
    client._watch_empty(0)
    client._watch_empty(0)
    with pytest.raises(ScraperBlocked):
        client._watch_empty(0)  # третий пустой подряд


def test_install_id_is_stable(_db):
    from flipper.app.scraper.client import _load_or_create_install_id

    a = _load_or_create_install_id()
    b = _load_or_create_install_id()
    assert a == b  # один и тот же «смартфон» между запусками
    assert len(a) > 10