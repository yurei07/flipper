"""Тесты Web Push: VAPID-ключи, подписка/отписка, гейт notify_push (TAKE/MAYBE/SKIP)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from flipper.app.analyzer.schema import FlipType, Verdict
from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing, PushSubscription, STATUS_ACTIVE
from flipper.app.notify.push import get_vapid_keys, notify_push
from flipper.config import load_config


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _subscription(send_maybe=False):
    with get_session() as s:
        s.add(
            PushSubscription(
                endpoint=f"https://push.example/{send_maybe}",
                p256dh="x" * 44,
                auth="y" * 22,
                send_maybe=send_maybe,
            )
        )
        s.commit()


def _analysis(verdict=Verdict.TAKE, flip=FlipType.resale, margin=50.0):
    with get_session() as s:
        s.add(
            Listing(ad_id=77, title="iPhone 11 top", price_cents=18000, status=STATUS_ACTIVE, description="opis")
        )
        s.add(
            Analysis(
                listing_id=77,
                flip_type=flip.value,
                parts_cost_eur=0.0,
                market_working_eur=260.0,
                margin_eur=margin,
                margin_pct=44.0,
                verdict=verdict.value,
                confidence=0.9,
            )
        )
        s.commit()
    with get_session() as s:
        return s.query(Analysis).filter_by(listing_id=77).one()


def test_vapid_keys_are_persistent(tmp_path, monkeypatch):
    import flipper.app.notify.push as p

    monkeypatch.setattr(p, "_vapid_file", lambda: tmp_path / "vapid.json")
    priv1, pub1 = get_vapid_keys()
    priv2, pub2 = get_vapid_keys()
    assert priv1 == priv2 and pub1 == pub2
    assert len(priv1) > 30 and len(pub1) > 80


def test_subscribe_unsubscribe_circles():
    from fastapi.testclient import TestClient

    from flipper.app.api.routes import make_app

    cfg = load_config()
    client = TestClient(make_app())
    key = {"Authorization": f"Bearer {cfg.api.api_key}"}
    body = {
        "endpoint": "https://push.example/1",
        "p256dh": "z" * 44,
        "auth": "w" * 22,
        "send_maybe": True,
    }
    r = client.post("/api/v1/push/subscribe", json=body, headers=key)
    assert r.status_code == 200
    with get_session() as s:
        assert s.query(PushSubscription).count() == 1
    r = client.post("/api/v1/push/subscribe", json={**body, "send_maybe": False}, headers=key)
    assert r.status_code == 200
    with get_session() as s:
        assert s.query(PushSubscription).one().send_maybe is False
    r = client.delete(f"/api/v1/push/unsubscribe?endpoint={body['endpoint']}", headers=key)
    assert r.status_code == 200
    with get_session() as s:
        assert s.query(PushSubscription).count() == 0


def test_push_keys_endpoint_auth_required():
    from fastapi.testclient import TestClient

    from flipper.app.api.routes import make_app

    client = TestClient(make_app())
    assert client.get("/api/v1/push/keys").status_code == 401


@pytest.mark.parametrize(
    "sub_maybes,verdict,margin,expected_calls",
    [
        ([False, True], Verdict.TAKE, 50.0, 2),   # TAKE — всем
        ([False, True], Verdict.MAYBE, 50.0, 1),  # MAYBE — только с send_maybe
        ([False], Verdict.MAYBE, 50.0, 0),        # MAYBE — подписки send_maybe нет
        ([False, True], Verdict.SKIP, 50.0, 0),   # SKIP — никому
        ([False, True], Verdict.TAKE, 5.0, 0),    # TAKE, но маржа ниже порога
    ],
)
def test_notify_push_gate(sub_maybes, verdict, margin, expected_calls, monkeypatch):
    import pywebpush

    for m in sub_maybes:
        _subscription(m)
    a = _analysis(verdict=verdict, margin=margin)
    calls = []

    def fake_webpush(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=201)

    monkeypatch.setattr(pywebpush, "webpush", fake_webpush)
    deal = load_config().deal
    notify_push(a, deal)
    assert len(calls) == expected_calls
    if expected_calls:
        payload = json.loads(calls[0]["data"])
        assert "TAKE" in payload["title"] or "MAYBE" in payload["title"]