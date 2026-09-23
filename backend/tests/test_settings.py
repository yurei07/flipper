"""Тесты настроек W2: стоп-слова в БД, per-source поиски, дефолт категории Vinted."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Listing, Search, SOURCE_KLEINANZEIGEN, SOURCE_VINTED
from flipper.app.scraper.vinted import _db_vinted_searches, _search_params, scan_vinted, vinted_ad_id
from flipper.app.scraper.search import _db_searches
from flipper.app.settings import (
    effective_excludes,
    get_setting,
    update_settings,
    vinted_default_catalogs_effective,
    vinted_enabled_effective,
)
from flipper.config import ScraperConfig, VintedConfig, VintedSearchConfig


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _item(n=1, title="iPhone 13 128GB", price=120.0, desc=""):
    return SimpleNamespace(
        id=10110000000 + n, title=title, price=price, description=desc,
        url="/items/x", brand_title="Apple", size_title=None, image=None,
        item_box={"accessibility_label": f"{title}, Zustand: Gut, {price:.2f} €"},
    )


class _Fake:
    def __init__(self, items): self.items = items
    def search(self, params): return list(self.items)


# ---------- настройки в БД ----------

def test_setting_roundtrip_and_validation():
    update_settings({"exclude_keywords": ["сумка", "чехол"]})
    assert get_setting("exclude_keywords") == ["сумка", "чехол"]
    update_settings({"exclude_keywords": None})  # None удаляет ключ
    assert get_setting("exclude_keywords") is None


def test_effective_excludes_fallback_config():
    cfg = ScraperConfig(exclude_keywords=["a"], exclude_title_keywords=["hülle"])
    assert effective_excludes(cfg) == (["a"], ["hülle"])


def test_effective_excludes_db_overrides_config():
    cfg = ScraperConfig(exclude_keywords=["a"], exclude_title_keywords=["hülle"])
    update_settings({"exclude_keywords": ["сумка"]})
    assert effective_excludes(cfg) == (["сумка"], ["hülle"])


def test_vinted_effective_enabled_and_catalogs():
    vin = VintedConfig(enabled=True, default_catalog_ids=[2994])
    assert vinted_enabled_effective(vin) is True
    assert vinted_default_catalogs_effective(vin) == [2994]
    update_settings({"vinted_enabled": False, "vinted_default_catalog_ids": []})
    assert vinted_enabled_effective(vin) is False
    assert vinted_default_catalogs_effective(vin) == []


def test_search_params_default_catalogs_when_search_has_none():
    sc = VintedSearchConfig(query="iPhone", max_price_eur=200)
    p = _search_params(sc, per_page=24, default_catalogs=[2994])
    assert p["attribute_ids[catalog]"] == "2994"
    # собственные категории поиска перекрывают дефолт
    sc2 = VintedSearchConfig(query="iPhone", catalog_ids=[123])
    p2 = _search_params(sc2, 24, default_catalogs=[2994])
    assert p2["attribute_ids[catalog]"] == "123"


def test_scan_vinted_uses_db_searches_and_default_catalog():
    vin = VintedConfig(default_catalog_ids=[2994], max_requests_per_cycle=10)
    with get_session() as s:
        s.add(Search(source=SOURCE_VINTED, query="MacBook", filters_json={
            "max_price_eur": 300, "catalog_ids": [],
        }, active=True))
        s.commit()
    seen = []
    fake = _Fake([_item(n=1)])
    fake.search = lambda params: (seen.append(params) or [_item(n=1)])
    from flipper.app.scraper.vinted import VintedClient
    client = VintedClient(vin, delay_range=(0, 0), max_requests=10, scraper=fake)
    assert scan_vinted(ScraperConfig(), vin, client) == 1
    assert seen[0]["search_text"] == "MacBook"
    assert seen[0]["attribute_ids[catalog]"] == "2994"


def test_db_vinted_searches_filters_source():
    vin = VintedConfig(searches=[VintedSearchConfig(query="from-config")])
    with get_session() as s:
        s.add(Search(source=SOURCE_VINTED, query="from-db", filters_json={}, active=True))
        s.add(Search(source=SOURCE_KLEINANZEIGEN, query="kk", filters_json={}, active=True))
        s.commit()
    with get_session() as s:
        rows = _db_vinted_searches(s, vin)
        # активная строка БД с source=vinted перекрывает config.yaml
        assert [r.query for r in rows] == ["from-db"]


def test_kleinanzeigen_db_searches_filters_source():
    cfg = ScraperConfig(searches=[])
    with get_session() as s:
        s.add(Search(source=SOURCE_VINTED, query="vinted-search", filters_json={}, active=True))
        s.add(Search(source=SOURCE_KLEINANZEIGEN, query="kk-search",
                     filters_json={"max_price_eur": 50}, active=True))
        s.commit()
    with get_session() as s:
        rows = _db_searches(s, cfg)
        assert [r.query for r in rows] == ["kk-search"]
        assert rows[0].max_price_eur == 50