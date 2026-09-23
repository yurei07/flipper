"""Тесты Vinted-источника: маппинг, фильтры, дедуп, рынок по лоту."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Listing, SOURCE_VINTED
from flipper.app.scraper.vinted import (
    VINTED_ID_OFFSET,
    VintedBlocked,
    VintedClient,
    _condition,
    _search_params,
    _to_listing,
    scan_vinted,
    vinted_ad_id,
)
from flipper.config import ScraperConfig, VintedConfig, VintedSearchConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _item(**kw):
    n = kw.get("n", 1)
    data = {
        "id": 10110000000 + n,
        "title": kw.get("title", "iPhone 13 128GB"),
        "price": kw.get("price", 120.0),
        "description": kw.get("description", ""),
        "url": "/items/10110000001-slug",
        "brand_title": kw.get("brand_title", "Apple"),
        "size_title": kw.get("size_title"),
        "image": kw.get("image", "https://img/v.jpg"),
        "item_box": {"accessibility_label": kw.get(
            "label", "iPhone, Zustand: Sehr gut, 120,00 €")},
    }
    return SimpleNamespace(**{k: v for k, v in data.items() if v is not None})


class _FakeScraper:
    """Подменяет vinted-scraper: выдаёт батчи по порядку вызовов."""

    def __init__(self, batches):
        self.batches = batches
        self.i = 0

    def search(self, params):
        batch = self.batches[min(self.i, len(self.batches) - 1)]
        self.i += 1
        return list(batch)


def _client(vin, batches, max_requests=10, hour=0):
    return VintedClient(
        vin, delay_range=(0.0, 0.0), max_requests=max_requests,
        max_requests_per_hour=hour, scraper=_FakeScraper(batches),
    )


def test_to_listing_namespaced_source_and_url():
    it = _item(n=7, title="iPhone 13", price=120.5)
    l = _to_listing(VintedConfig(), it)
    assert l.ad_id == VINTED_ID_OFFSET + it.id == vinted_ad_id(it.id)
    assert l.source == SOURCE_VINTED
    assert l.price_cents == 12050
    assert l.url == "https://www.vinted.de/items/10110000001-slug"
    assert l.image_urls_json == ["https://img/v.jpg"]
    assert "Zustand: Sehr gut" in l.description
    assert l.attributes_json["vinted_item_id"] == it.id


def test_to_listing_synthetic_description_when_empty():
    l = _to_listing(VintedConfig(), _item(title="MacBook Air", description=""))
    assert l.description  # не пусто — без этого analyze_pending пропустит лот
    assert "MacBook Air" in l.description


def test_condition_parse():
    assert _condition(_item(label="MacBook, Marke: Apple, Zustand: Gut, 94,00 €")) == "Gut"
    assert _condition(_item(label="no state here")) is None


def test_search_params_price_and_attributes():
    sc = VintedSearchConfig(
        query="iPhone", max_price_eur=200, catalog_ids=[5, 6],
        brand_ids=[13], status_ids=[2], order="price_low_to_high",
    )
    p = _search_params(sc, per_page=24)
    assert p["search_text"] == "iPhone"
    assert p["price_to"] == 200
    assert p["attribute_ids[catalog]"] == "5,6"
    assert p["attribute_ids[brand]"] == "13"
    assert p["attribute_ids[status]"] == "2"
    assert p["order"] == "price_low_to_high"
    assert not _search_params(VintedSearchConfig(query="x"), 24).get("price_to")


def test_scan_vinted_dedup_and_price_update():
    cfg = ScraperConfig(exclude_keywords=["fahrrad"], exclude_title_keywords=["hülle"])
    vin = VintedConfig(
        searches=[VintedSearchConfig(query="iPhone")],
        max_requests_per_cycle=10,
    )
    client = _client(vin, [[_item(n=1, price=100.0)]])
    assert scan_vinted(cfg, vin, client) == 1
    with get_session() as s:
        row = s.get(Listing, vinted_ad_id(10110000001))
        assert row is not None and row.price_cents == 10000
        assert row.source == SOURCE_VINTED

    # тот же лот снова -> 0 новых, цена обновилась (price-drop путь)
    client = _client(vin, [[_item(n=1, price=90.0)]])
    assert scan_vinted(cfg, vin, client) == 0
    with get_session() as s:
        row = s.get(Listing, vinted_ad_id(10110000001))
        assert row.price_cents == 9000


def test_scan_vinted_excludes_by_keywords():
    cfg = ScraperConfig(exclude_title_keywords=["hülle"])
    vin = VintedConfig(searches=[VintedSearchConfig(query="iPhone")], max_requests_per_cycle=10)
    client = _client(vin, [[_item(n=2, title="iPhone Schutzhülle"), _item(n=3)]])
    assert scan_vinted(cfg, vin, client) == 1


def test_scan_vinted_respects_cycle_budget():
    vin = VintedConfig(
        searches=[VintedSearchConfig(query=str(i)) for i in range(5)],
        max_requests_per_cycle=2,
    )
    batches = [[_item(n=10 + i)] for i in range(5)]
    client = _client(vin, batches)
    assert scan_vinted(ScraperConfig(), vin, client, max_requests=2) == 2


def test_vinted_client_blocked_on_budget():
    vin = VintedConfig(searches=[VintedSearchConfig(query="iPhone")], max_requests_per_cycle=1)
    client = _client(vin, [[], []], max_requests=1)
    client.search({})  # 1-й запрос — ок
    with pytest.raises(VintedBlocked):
        client.search({})  # 2-й превышает бюджет цикла


def test_fetch_vinted_market_samples_filters():
    from flipper.app.pricing.market import fetch_vinted_market_samples

    batches = [[
        _item(n=1, title="iPhone 13 gut", price=150.0),
        _item(n=2, title="iPhone 13 Hülle", price=10.0, size_title="L"),
        _item(n=3, title="iPhone 13 defekt", price=60.0),
        _item(n=4, title="iPhone 13", price=80.0),
    ]]
    client = _client(VintedConfig(), batches)
    prices = fetch_vinted_market_samples(client, "iphone 13", max_results=10)
    assert prices == [15000, 8000]