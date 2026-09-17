"""Тесты FCM-логики (§9): payload титула, пороги, dismissed/ignored."""
from __future__ import annotations

import pytest

from flipper.app.db import init_db
from flipper.app.models import Analysis, DeviceToken, Listing, UserAction
from flipper.app.notify.fcm import _notification_payload, maybe_notify
from flipper.config import DealConfig, load_config


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _analysis(**over) -> Analysis:
    """Фабрика Analysis без записи в БД (для payload-теста)."""
    base = dict(
        listing_id=111,
        flip_type="repair",
        device_json={"type": "laptop", "brand": "Apple", "model": "MacBook Air 2017"},
        margin_eur=70.0,
        margin_pct=50.0,
        verdict="TAKE",
        action_ru="написать продавцу и предложить 110 евро",
    )
    base.update(over)
    return Analysis(**base)


# ---------- payload ----------

def test_payload_title_has_margin_and_model():
    p = _notification_payload(_analysis(margin_eur=70.0))
    assert p["title"] == "💰 +70€ | MacBook Air 2017"
    assert "kind" in p["data"] and p["data"]["ad_id"] == "111"
    assert len(p["body"]) <= 90  # action_ru обрезан до ~90 символов


def test_payload_model_fallback_to_type():
    a = _analysis(device_json={"type": "monitor", "model": ""})
    assert a.device_json["model"] or a.device_json["type"] in _notification_payload(a)["title"]


# ---------- maybe_notify: условия из §9 ----------

def test_notify_filters_by_verdict(_db):
    from flipper.app.notify import fcm

    cfg = load_config()
    deal = DealConfig(
        min_margin_eur=cfg.deal.min_margin_eur,
        min_margin_pct=cfg.deal.min_margin_pct,
        min_margin_eur_resale=cfg.deal.min_margin_eur_resale,
        min_margin_pct_resale=cfg.deal.min_margin_pct_resale,
        logistics_eur=cfg.deal.logistics_eur,
        notify_only_verdicts=["TAKE"],
    )

    # MAYBE — маржа есть, но вердикт не в списке -> НЕ пушим
    calls = []
    orig = fcm.send_deal_notification
    fcm.send_deal_notification = lambda a: calls.append(a) or True
    try:
        a = _analysis(verdict="MAYBE", margin_eur=90.0)
        with _db_session() as s:
            s.add(Listing(ad_id=111, title="t", status="active"))
            s.commit()
        assert maybe_notify(a, deal) is False
        assert calls == []

        # TAKE, маржа выше порога, активен, не отмёл -> пушим
        a = _analysis(verdict="TAKE", margin_eur=90.0)
        assert maybe_notify(a, deal) is True
        assert len(calls) == 1

        # TAKE, но объявление отмечено ignored -> НЕ пушим
        a = _analysis(verdict="TAKE", margin_eur=90.0)
        with _db_session() as s:
            s.add(UserAction(listing_id=111, action="ignored"))
            s.commit()
        assert maybe_notify(a, deal) is False
        assert len(calls) == 1
    finally:
        fcm.send_deal_notification = orig


def test_notify_skips_when_margin_below_threshold(_db):
    from flipper.app.notify import fcm

    deal = DealConfig(min_margin_eur=40, notify_only_verdicts=["TAKE"])
    calls = []
    orig = fcm.send_deal_notification
    fcm.send_deal_notification = lambda a: calls.append(a) or True
    try:
        with _db_session() as s:
            s.add(Listing(ad_id=222, title="t", status="active"))
            s.commit()
        a = _analysis(listing_id=222, verdict="TAKE", margin_eur=30.0)
        assert maybe_notify(a, deal) is False
        assert calls == []
    finally:
        fcm.send_deal_notification = orig


def _db_session():
    from flipper.app.db import get_session

    return get_session()