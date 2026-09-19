"""Тест: повторное появление лота в скане обновляет его текущую цену."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Listing, STATUS_ACTIVE
from flipper.config import ScraperConfig, SearchConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _ad(ad_id=1, price=40.0):
    return SimpleNamespace(
        id=ad_id,
        title=f"item {ad_id}",
        price=price,
        description="defekt",
        url=f"https://www.kleinanzeigen.de/s-anzeige/{ad_id}",
        category_id=173,
        attributes={},
        images=[],
        city="Flensburg",
        poster_type="private",
    )


class _FakeClient:
    def __init__(self, ads):
        self.ads = ads

    def search(self, **kwargs):
        return self.ads


def _cfg() -> ScraperConfig:
    return ScraperConfig(
        searches=[
            SearchConfig(query="defekt", max_price_eur=100, radius_km=15, location="Flensburg")
        ],
        pages_per_search=1,
    )


def test_scan_refreshes_price_of_existing_listing():
    from flipper.app.scraper.search import scan_one

    with get_session() as s:
        s.add(
            Listing(
                ad_id=1,
                title="item 1",
                price_cents=5000,  # первый вход: 50€
                status=STATUS_ACTIVE,
            )
        )
        s.commit()

    new = scan_one(_cfg(), _FakeClient([_ad(price=40.0)]))
    assert new == 0  # не новое
    with get_session() as s:
        assert s.get(Listing, 1).price_cents == 4000  # цена «сейчас»


def test_scan_new_listing_uses_price():
    from flipper.app.scraper.search import scan_one

    new = scan_one(_cfg(), _FakeClient([_ad(price=33.0)]))
    assert new == 1
    with get_session() as s:
        assert s.get(Listing, 1).price_cents == 3300