"""Тесты детерминированных правил: перевод repair/resale, «оценочно», приоритет свежих."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flipper.app.analyzer.schema import AnalysisResult, Fault, FlipType, Verdict
from flipper.app.db import get_session, init_db
from flipper.app.models import STATUS_ACTIVE, Analysis, Listing
from flipper.app.pipeline import _normalize_flip, _pending_query


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _listing(title, description="", ad_id=1, first_seen=None):
    return Listing(
        ad_id=ad_id,
        title=title,
        price_cents=10000,
        status=STATUS_ACTIVE,
        description=description,
        first_seen_at=first_seen or datetime.utcnow(),
    )


def _res(flip_type="repair", fault=None, repairability=None, repair_cost=0.0):
    deal = {
        "price_ask_eur": 100, "market_working_eur": 250, "repair_cost_eur": repair_cost,
        "margin_eur": 0, "margin_pct": 0, "resale_speed_hint_ru": "", "verdict": "TAKE",
        "confidence": 0.8, "pros_ru": [], "cons_ru": [], "action_ru": "брать",
    }
    return AnalysisResult.model_validate(
        {
            "flip_type": flip_type,
            "device": {"type": "phone", "brand": "Apple", "model": "iPhone 11"},
            "fault": fault,
            "repairability": repairability,
            "deal": deal,
            "flag_reasons_ru": [],
        }
    )


def test_defect_in_title_forces_repair():
    res = _res(flip_type="resale")
    _normalize_flip(res, _listing("iPhone defekt zum Basteln", ad_id=1))
    assert res.flip_type == FlipType.repair
    assert res.repairability is not None


def test_fault_present_forces_repair_even_if_model_says_resale():
    res = _res(flip_type="resale")
    res.fault = Fault.model_validate(
        {"summary_ru": "залита плата", "water_damage": True, "locked": False}
    )
    _normalize_flip(res, _listing("iPhone 11 top Zustand", ad_id=2))
    assert res.flip_type == FlipType.repair
    assert res.flip_type == FlipType.repair


def test_no_defect_switches_resale_back_to_resale():
    res = _res(flip_type="repair")
    _normalize_flip(res, _listing("MacBook Air top Zustand", "funktioniert einwandfrei", ad_id=3))
    assert res.flip_type == FlipType.resale
    assert res.fault is None
    assert res.repairability is None


def test_flip_kept_for_real_repair():
    res = _res(flip_type="repair", repair_cost=40.0)
    _normalize_flip(res, _listing("MacBook defekt Display", ad_id=4))
    assert res.flip_type == FlipType.repair


def test_analyze_pending_orders_newest_first():
    with get_session() as s:
        old = _listing("A", "opis", ad_id=10, first_seen=datetime.utcnow() - timedelta(days=3))
        new = _listing("B", "opis", ad_id=11, first_seen=datetime.utcnow())
        mid = _listing("C", "opis", ad_id=12, first_seen=datetime.utcnow() - timedelta(days=1))
        s.add_all([old, mid, new])
        s.commit()
        pending = _pending_query(s, set(), limit=10)
        assert [p.ad_id for p in pending] == [11, 12, 10]


def test_estimated_analysis_column_defaults_false():
    with get_session() as s:
        s.add(
            Analysis(
                listing_id="33",
                flip_type=FlipType.resale.value,
                margin_eur=10.0,
                margin_pct=10.0,
                verdict=Verdict.TAKE.value,
                confidence=0.8,
            )
        )
        s.commit()
        a = s.query(Analysis).filter_by(listing_id="33").one()
        assert a.market_estimated is False