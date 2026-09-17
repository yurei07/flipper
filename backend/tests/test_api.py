"""Тесты REST API §8: лента, деталка, действия, searches CRUD."""
from __future__ import annotations

import os

os.environ["FLIPPER_API_KEY"] = "test123"

import pytest
from fastapi.testclient import TestClient

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


@pytest.fixture
def api(_db):
    # Импорт main вызывает init_db(реальный файл) — поэтому переинициализируем
    # in-memory ПОСЛЕ импорта, чтобы тесты не трогали flipper.db
    from flipper.app.main import app

    init_db("sqlite:///:memory:")
    return TestClient(app)


@pytest.fixture
def headers():
    return {"Authorization": "Bearer test123"}


def _seed_listing(ad_id: int = 111, title: str = "Test Laptop", images=None, desc="some") -> None:
    with get_session() as s:
        s.add(
            Listing(
                ad_id=ad_id,
                title=title,
                price_cents=10000,
                description=desc,
                image_urls_json=images or [],
            )
        )
        s.commit()


def _seed_analysis(ad_id: int = 111, verdict: str = "TAKE", margin: float = 70.0) -> None:
    with get_session() as s:
        s.add(
            Analysis(
                listing_id=ad_id,
                verdict=verdict,
                margin_eur=margin,
                margin_pct=50.0,
                confidence=0.9,
                device_json={"type": "laptop"},
            )
        )
        s.commit()


# ---------- лента ----------

def test_listings_shape_dont_authes(api, headers):
    _seed_listing(); _seed_analysis()
    r = api.get("/api/v1/listings", headers=headers, params={"limit": 10})
    assert r.status_code == 200
    row = r.json()[0]
    # Ключи из §8
    for k in ("ad_id", "title", "price_cents", "image_url", "verdict",
              "margin_eur", "margin_pct", "confidence", "saved", "ignored"):
        assert k in row, f"нет ключа {k}"


def test_listings_filter_verdict_and_margin(api, headers):
    _seed_listing(111); _seed_analysis(111, "TAKE", 70.0)
    _seed_listing(222, "B-безопасный"); _seed_analysis(222, "SKIP", 5.0)
    r = api.get("/api/v1/listings", headers=headers, params={"verdict": "TAKE"})
    assert len(r.json()) == 1 and r.json()[0]["ad_id"] == 111
    r = api.get("/api/v1/listings", headers=headers, params={"min_margin": 50})
    assert len(r.json()) == 1


def test_listings_requires_auth(api):
    assert api.get("/api/v1/listings").status_code == 401


# ---------- деталка ----------

def test_listing_detail_with_analysis(api, headers):
    _seed_listing(111, images=["http://x/img.jpg"])
    _seed_analysis(111)
    r = api.get("/api/v1/listings/111", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["image_urls"] == ["http://x/img.jpg"]
    assert body["analysis"]["verdict"] == "TAKE"


def test_listing_detail_404(api, headers):
    assert api.get("/api/v1/listings/999", headers=headers).status_code == 404


# ---------- действия ----------

def test_actions_mark_saved_ignored_dismissed(api, headers):
    _seed_listing()
    _seed_analysis()
    # последнее действие побеждает (§8: flags отражают актуальное состояние)
    api.post("/api/v1/listings/111/saved", headers=headers)
    row = api.get("/api/v1/listings", headers=headers).json()[0]
    assert row["saved"] is True and row["ignored"] is False

    api.post("/api/v1/listings/111/ignored", headers=headers)
    row = api.get("/api/v1/listings", headers=headers).json()[0]
    assert row["saved"] is False and row["ignored"] is True

    api.post("/api/v1/listings/111/dismissed", headers=headers)
    row = api.get("/api/v1/listings", headers=headers).json()[0]
    assert row["saved"] is False and row["ignored"] is True  # dismissed тоже скрывает


def test_actions_404(api, headers):
    assert api.post("/api/v1/listings/999/saved", headers=headers).status_code == 404


# ---------- searches CRUD ----------

def test_searches_crud(api, headers):
    r = api.post("/api/v1/searches", headers=headers,
                 json={"query": "iPhone defekt", "max_price_eur": 100})
    assert r.status_code == 200
    sid = r.json()["id"]
    assert api.get("/api/v1/searches", headers=headers).json()[0]["query"] == "iPhone defekt"
    assert api.delete(f"/api/v1/searches/{sid}", headers=headers).status_code == 200
    assert api.get("/api/v1/searches", headers=headers).json() == []


def test_searches_404(api, headers):
    assert api.delete("/api/v1/searches/999", headers=headers).status_code == 404