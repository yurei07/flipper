"""Тесты Фазы C: CSV-экспорт и пересмотр залежавшихся MAYBE."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing, STATUS_ACTIVE
from flipper.app.pipeline import prune_stale_maybes


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _seed(ad_id, verdict, margin, age_days):
    with get_session() as s:
        s.add(Listing(ad_id=ad_id, title=f"item {ad_id}", price_cents=10000, status=STATUS_ACTIVE, description="opis"))
        s.add(Analysis(
            listing_id=ad_id, flip_type="repair", margin_eur=margin, margin_pct=margin,
            verdict=verdict, confidence=0.8,
            created_at=datetime.utcnow() - timedelta(days=age_days),
        ))
        s.commit()


def test_prune_stale_maybes_only_old_and_limited():
    _seed(1, "MAYBE", 40.0, 5)   # старый -> удалить
    _seed(2, "MAYBE", 30.0, 5)   # старый, но лимит
    _seed(3, "MAYBE", 20.0, 1)   # свежий -> не трогаем
    _seed(4, "TAKE", 90.0, 5)    # не MAYBE
    assert prune_stale_maybes(days=3, limit=1) == 1
    with get_session() as s:
        left = s.query(Analysis).all()
        assert {a.listing_id for a in left if a.verdict == "MAYBE"} == {2, 3}
        assert 4 in {a.listing_id for a in left}


def test_csv_export_shape():
    from fastapi.testclient import TestClient

    from flipper.app.api.routes import make_app
    from flipper.config import load_config

    cfg = load_config()
    client = TestClient(make_app())
    # make_app подключает рабочий файл БД — вносим тестовую строку и убираем её
    with get_session() as s:
        s.add(Listing(ad_id=987654321, title="item 987654321", price_cents=10000, status=STATUS_ACTIVE, description="opis"))
        s.add(Analysis(
            listing_id=987654321, flip_type="repair", margin_eur=50.0, margin_pct=50.0,
            verdict="TAKE", confidence=0.8,
        ))
        s.commit()
    try:
        r = client.get("/api/v1/export.csv", headers={"Authorization": f"Bearer {cfg.api.api_key}"})
        assert r.status_code == 200
        assert "text/csv" in r.headers["content-type"]
        lines = [ln for ln in r.text.splitlines() if ln]
        assert lines[0].startswith("дата,id,название")
        assert any("item 987654321" in ln and "TAKE" in ln for ln in lines[1:])
    finally:
        with get_session() as s:
            s.query(Analysis).filter_by(listing_id=987654321).delete()
            s.query(Listing).filter_by(ad_id=987654321).delete()
            s.commit()