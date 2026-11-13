"""Тесты портфеля флиппера: CRUD сделок, экономика (профит/ROI), связка с лотом."""
from __future__ import annotations

import os

os.environ["FLIPPER_API_KEY"] = "test123"

import pytest
from fastapi.testclient import TestClient

from flipper.app.db import get_session, init_db
from flipper.app.models import Listing


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


@pytest.fixture
def api(_db):
    from flipper.app.main import app

    init_db("sqlite:///:memory:")
    return TestClient(app)


@pytest.fixture
def headers():
    return {"Authorization": "Bearer test123"}


def _seed_listing(ad_id: int = 111, title: str = "ThinkPad T480") -> None:
    with get_session() as s:
        s.add(Listing(ad_id=ad_id, title=title, price_cents=15000, description="x"))
        s.commit()


def test_flips_requires_auth(api):
    assert api.get("/api/v1/flips").status_code == 401


def test_create_from_listing_pulls_title(api, headers):
    _seed_listing(111, "ThinkPad T480")
    r = api.post("/api/v1/flips", headers=headers, json={"listing_id": 111, "buy_price_eur": 150})
    assert r.status_code == 200
    f = r.json()
    assert f["title"] == "ThinkPad T480"
    assert f["status"] == "stock"
    assert f["invested_eur"] == 150
    assert f["profit_eur"] is None  # ещё не продано

    # Деталка лота теперь знает про флип
    d = api.get("/api/v1/listings/111", headers=headers).json()
    assert d["flip"] is not None and d["flip"]["id"] == f["id"]


def test_create_from_missing_listing_404(api, headers):
    r = api.post("/api/v1/flips", headers=headers, json={"listing_id": 999, "buy_price_eur": 1})
    assert r.status_code == 404


def test_manual_create_and_econ_after_sell(api, headers):
    r = api.post(
        "/api/v1/flips",
        headers=headers,
        json={"title": "RTX 3070", "buy_price_eur": 100, "extras_eur": 20},
    )
    flip_id = r.json()["id"]

    # помечаем проданным: профит = продажа - комиссия - (покупка + расходы)
    r = api.put(
        f"/api/v1/flips/{flip_id}",
        headers=headers,
        json={"status": "sold", "sale_price_eur": 200, "sell_fees_eur": 10},
    )
    assert r.status_code == 200
    f = r.json()
    assert f["invested_eur"] == 120
    assert f["revenue_eur"] == 190
    assert f["profit_eur"] == 70
    assert f["roi_pct"] == pytest.approx(58.3, abs=0.1)
    assert f["sold_date"] is not None  # проставилась автоматически


def test_update_buy_price(api, headers):
    flip_id = api.post(
        "/api/v1/flips", headers=headers, json={"title": "x", "buy_price_eur": 10}
    ).json()["id"]
    r = api.put(f"/api/v1/flips/{flip_id}", headers=headers, json={"buy_price_eur": 30, "extras_eur": 5})
    assert r.json()["invested_eur"] == 35


def test_status_filter_and_summary(api, headers):
    a = api.post("/api/v1/flips", headers=headers, json={"title": "a", "buy_price_eur": 100}).json()["id"]
    api.post("/api/v1/flips", headers=headers, json={"title": "b", "buy_price_eur": 50})
    api.put(
        f"/api/v1/flips/{a}",
        headers=headers,
        json={"status": "sold", "sale_price_eur": 250, "sell_fees_eur": 0},
    )

    stock = api.get("/api/v1/flips", headers=headers, params={"status": "stock"}).json()
    assert [f["title"] for f in stock] == ["b"]

    s = api.get("/api/v1/flips/summary", headers=headers).json()
    assert s["total"] == 2
    assert s["stock_count"] == 1 and s["sold_count"] == 1
    assert s["stock_invested_eur"] == 50
    assert s["invested_eur"] == 100
    assert s["revenue_eur"] == 250
    assert s["profit_eur"] == 150
    assert s["roi_pct"] == pytest.approx(150.0, abs=0.1)
    assert s["best_profit_eur"] == 150


def test_bad_status_rejected(api, headers):
    flip_id = api.post("/api/v1/flips", headers=headers, json={"title": "x", "buy_price_eur": 1}).json()["id"]
    r = api.put(f"/api/v1/flips/{flip_id}", headers=headers, json={"status": "banana"})
    assert r.status_code == 422


def test_delete_flip(api, headers):
    flip_id = api.post("/api/v1/flips", headers=headers, json={"title": "x", "buy_price_eur": 1}).json()["id"]
    assert api.delete(f"/api/v1/flips/{flip_id}", headers=headers).status_code == 200
    assert api.get("/api/v1/flips", headers=headers).json() == []
    assert api.delete(f"/api/v1/flips/{flip_id}", headers=headers).status_code == 404


def test_flip_404s(api, headers):
    assert api.put("/api/v1/flips/999", headers=headers, json={"buy_price_eur": 1}).status_code == 404
    assert api.delete("/api/v1/flips/999", headers=headers).status_code == 404
