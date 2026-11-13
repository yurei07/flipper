"""Тесты детерминированных правил: перевод repair/resale, «оценочно», приоритет свежих."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flipper.app.analyzer.schema import AnalysisResult, Fault, FlipType, Verdict
from flipper.app.db import get_session, init_db
from flipper.app.models import STATUS_ACTIVE, Analysis, Listing
from flipper.app.pipeline import (
    _accessory_skip,
    _liquidity_class,
    _normalize_flip,
    _pending_split,
    recompute_verdict,
)
from flipper.config import DealConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _listing(title, description="", ad_id=1, first_seen=None, source="kleinanzeigen"):
    return Listing(
        ad_id=ad_id,
        title=title,
        price_cents=10000,
        status=STATUS_ACTIVE,
        description=description,
        first_seen_at=first_seen or datetime.utcnow(),
        source=source,
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


def test_pending_split_fair_across_sources_oldest_first():
    with get_session() as s:
        old_kk = _listing("A", "opis", ad_id=10, first_seen=datetime.utcnow() - timedelta(days=3))
        new_kk = _listing("B", "opis", ad_id=11, first_seen=datetime.utcnow())
        vin = _listing("C", "opis", ad_id=12, first_seen=datetime.utcnow() - timedelta(hours=1), source="vinted")
        s.add_all([old_kk, new_kk, vin])
        s.commit()
        pending = _pending_split(s, set(), limit=10, order_sources=["kleinanzeigen", "vinted"])
        ids = [p.ad_id for p in pending]
        assert ids[0] == 10  # первым — самый старый kleinanzeigen
        assert 12 in ids     # чередование дотащило и vinted
        assert len(ids) == 3


def test_accessory_skip_rules():
    assert _accessory_skip(_listing("Handyhülle iPhone 13 Silikon", ad_id=20)) is not None
    assert _accessory_skip(_listing("USB-C Kabel 2m NEU", ad_id=21)) is not None
    assert _accessory_skip(_listing("Case für iPad Schutzhülle", ad_id=22)) is not None
    assert _accessory_skip(_listing("iPhone 13 mit Originalhülle", ad_id=23)) is None
    assert _accessory_skip(_listing("MacBook Air 13 M1", ad_id=24)) is None
    assert _accessory_skip(_listing("iPhone XS 64GB defekt", ad_id=25)) is None


def test_accessory_skip_multilingual_vinted():
    for title, ad_id in [
        ("iPhone 13 hoesje", 30),
        ("hoesjes iphone 12", 31),
        ("Châssis iPhone 13 bleu", 32),
        ("Batterie iPhone 13 officiel 87%", 33),
        ("Imoshion telefoonhoesje Google Pixel pro", 34),
        ("Nieuw TPU hoesje Samsung Galaxy Watch 5", 35),
        ("Jeux xbox 360 final fantasy", 36),
    ]:
        assert _accessory_skip(_listing(title, ad_id=ad_id)) is not None


def test_accessory_skip_does_not_hit_real_devices():
    assert _accessory_skip(_listing("2x Canon AF35M Analogkamera Set – Defekt", ad_id=40)) is None
    assert _accessory_skip(_listing("Spielekonsole Nintendo Switch", ad_id=41)) is None
    assert _accessory_skip(_listing("Canon EOS 600D mit Objektiv", ad_id=42)) is None


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


def _res_priced(device_type, price, market, brand="", model="", specs=""):
    deal = {
        "price_ask_eur": price, "market_working_eur": market, "repair_cost_eur": 0,
        "margin_eur": 0, "margin_pct": 0, "resale_speed_hint_ru": "", "verdict": "SKIP",
        "confidence": 0.8, "pros_ru": [], "cons_ru": [], "action_ru": "",
    }
    return AnalysisResult.model_validate(
        {
            "flip_type": "resale",
            "device": {"type": device_type, "brand": brand, "model": model, "specs_hint": specs},
            "deal": deal,
            "flag_reasons_ru": [],
        }
    )


def test_liquidity_class_by_type_and_markers():
    assert _liquidity_class(_res_priced("gpu", 100, 200)) == "hot"
    assert _liquidity_class(_res_priced("console", 100, 200)) == "hot"
    assert _liquidity_class(_res_priced("unknown", 100, 200, brand="NVIDIA", model="RTX 3070")) == "hot"
    assert _liquidity_class(_res_priced("printer", 100, 200)) == "slow"
    assert _liquidity_class(_res_priced("unknown", 100, 200, model="Kaffeevollautomat")) == "slow"
    assert _liquidity_class(_res_priced("unknown", 100, 200, model="Irgendwas")) == "normal"


def test_liquidity_hot_lowers_take_threshold():
    # маржа 40€ при цене 100: обычная ниша -> MAYBE, ходовое железо -> TAKE
    cfg = DealConfig()
    hot = _res_priced("gpu", 100, 145)
    normal = _res_priced("unknown", 100, 145)
    assert recompute_verdict(hot, cfg)[2] == Verdict.TAKE
    assert recompute_verdict(normal, cfg)[2] == Verdict.MAYBE


def test_liquidity_slow_raises_take_threshold():
    # маржа 55€ при цене 100: обычная ниша -> TAKE, неликвид -> MAYBE
    cfg = DealConfig()
    normal = _res_priced("unknown", 100, 160)
    slow = _res_priced("printer", 100, 160)
    assert recompute_verdict(normal, cfg)[2] == Verdict.TAKE
    assert recompute_verdict(slow, cfg)[2] == Verdict.MAYBE


def test_liquidity_can_be_disabled():
    cfg = DealConfig(liquidity_enabled=False)
    hot = _res_priced("gpu", 100, 145)
    assert recompute_verdict(hot, cfg)[2] == Verdict.MAYBE