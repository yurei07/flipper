"""Тесты слежения за ценой: повторные алерты при снижении цены на уведомлённых."""
from __future__ import annotations

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing, STATUS_ACTIVE
from flipper.config import DealConfig, NotifyConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _deal() -> DealConfig:
    return DealConfig(min_margin_eur=40, min_margin_pct=35, logistics_eur=5)


def _notify() -> NotifyConfig:
    return NotifyConfig(telegram_bot_token="tok", telegram_chat_id="1", price_drop_pct=15)


def _listing(ad_id: int, price_cents: int, notified: int | None) -> None:
    with get_session() as s:
        s.add(
            Listing(
                ad_id=ad_id,
                title=f"item {ad_id}",
                price_cents=price_cents,
                last_notified_price_cents=notified,
                status=STATUS_ACTIVE,
                location_city="Flensburg",
                url=f"https://www.kleinanzeigen.de/s-anzeige/{ad_id}",
            )
        )
        s.commit()


def _analysis(ad_id: int, verdict: str = "TAKE", market: float = 100.0, parts: float = 10.0) -> None:
    with get_session() as s:
        s.add(
            Analysis(
                listing_id=ad_id,
                flip_type="repair",
                device_json={"model": f"M{ad_id}"},
                parts_cost_eur=parts,
                market_working_eur=market,
                margin_eur=50,
                margin_pct=100,
                verdict=verdict,
            )
        )
        s.commit()


def test_no_telegram_token_noop():
    from flipper.app.notify import pricedrop

    _listing(1, 3000, 5000)
    _analysis(1)
    assert pricedrop.check_price_drops(_deal(), NotifyConfig()) == 0


def test_no_drop_no_alert(monkeypatch):
    from flipper.app.notify import pricedrop

    sent = []
    monkeypatch.setattr(pricedrop, "send_message", lambda *a, **k: sent.append(k) or True)
    _listing(1, 4000, 4000)
    _analysis(1)
    assert pricedrop.check_price_drops(_deal(), _notify()) == 0
    assert sent == []


def test_drop_below_threshold_no_alert(monkeypatch):
    from flipper.app.notify import pricedrop

    sent = []
    monkeypatch.setattr(pricedrop, "send_message", lambda *a, **k: sent.append(k) or True)
    _listing(1, 3500, 4000)  # −12.5% < 15%
    _analysis(1)
    assert pricedrop.check_price_drops(_deal(), _notify()) == 0
    assert sent == []


def test_drop_triggers_alert_and_updates_price(monkeypatch):
    from flipper.app.notify import pricedrop

    sent = []
    monkeypatch.setattr(pricedrop, "send_message", lambda *a, **k: sent.append(a) or True)
    _listing(1, 4000, 5000)  # −20% ≥ 15%
    _analysis(1)
    assert pricedrop.check_price_drops(_deal(), _notify()) == 1
    assert len(sent) == 1
    text = sent[0][0]
    assert "Цена упала" in text and "50€" in text  # 50 → 40
    with get_session() as s:
        row = s.get(Listing, 1)
        assert row.last_notified_price_cents == 4000  # следующий алерт от новой цены


def test_skip_never_realerted(monkeypatch):
    from flipper.app.notify import pricedrop

    sent = []
    monkeypatch.setattr(pricedrop, "send_message", lambda *a, **k: sent.append(k) or True)
    _listing(1, 2000, 5000)
    _analysis(1, verdict="SKIP")
    assert pricedrop.check_price_drops(_deal(), _notify()) == 0
    assert sent == []


def test_maybe_upgrades_to_take_and_sends(monkeypatch):
    from flipper.app.notify import pricedrop

    sent = []
    monkeypatch.setattr(pricedrop, "send_message", lambda *a, **k: sent.append(a[0]) or True)
    _listing(1, 3000, 5000)  # рынок 100, вложения 10 → новая маржа 60€ — TAKE
    _analysis(1, verdict="MAYBE")
    assert pricedrop.check_price_drops(_deal(), _notify()) == 1
    assert "TAKE" in sent[0] and "Цена упала" in sent[0]