"""Второй источник — Vinted: клиент с бюджетами + скан в те же listings.

Зеркало kleinanzeigen-сканера: свои паузы и лимиты (у Vinted свой анти-бот),
дедуп по id, обновление цены для price-drop. Анализ/маржи/уведомления общие.
"""
from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime
from pathlib import Path

from vinted_scraper import VintedScraper

from flipper.app.db import get_session
from flipper.app.models import Listing, STATUS_ACTIVE, SOURCE_VINTED
from flipper.app.scraper.client import _HourlyLimiter
from flipper.config import VintedConfig, VintedSearchConfig

log = logging.getLogger(__name__)

# Vinted item id ~10-11 цифр; сдвиг гарантирует отсутствие коллизий с KK (ad_id).
VINTED_ID_OFFSET = 1_000_000_000_000
_VINTED_RATE_FILE = Path.home() / ".config" / "flipper" / "rate_limit_vinted.json"

# Локализованные метки состояния в item_box.accessibility_label
_CONDITION_RE = re.compile(
    r"(?:zustand|condition|état|kondition|stato)\s*:\s*([^,]+)", re.IGNORECASE
)


class VintedBlocked(RuntimeError):
    """Vinted упёрся в капчу/лимит — тот же cooldown что у KK."""
    pass


def vinted_ad_id(item_id: int) -> int:
    """Namespaced ad_id, уникальный на фоне Kleinanzeigen."""
    return VINTED_ID_OFFSET + int(item_id)


def _cents(price: float) -> int:
    return int(round(price * 100))


def _condition(item) -> str | None:
    """Состояние из accessibility_label («Zustand: Sehr gut»). None если нечего."""
    box = getattr(item, "item_box", None) or {}
    label = box.get("accessibility_label") or ""
    m = _CONDITION_RE.search(label)
    return m.group(1).strip() if m else None


def _synthetic_description(item, condition: str | None) -> str:
    """Описание для анализа, даже если у лота на Vinted пустой текст.

    Склеиваем из фактов, которые уже есть в поиске (бренд/модель/состояние),
    чтобы `analyze_pending` и LLM не пропускали лот из-за пустого описания.
    """
    parts = [getattr(item, "title", None) or ""]
    if getattr(item, "brand_title", None):
        parts.append(f"Marke: {item.brand_title}")
    if condition:
        parts.append(f"Zustand: {condition}")
    if getattr(item, "size_title", None):
        parts.append(f"Größe: {item.size_title}")
    return " | ".join(p for p in parts if p)[:10000] or "Vinted"


def _item_url(cfg: VintedConfig, item) -> str:
    rel = getattr(item, "url", None)
    if not rel and getattr(item, "path", None):
        rel = item.path
    if not rel:
        return cfg.domain
    if rel.startswith("http"):
        return rel
    return f"{cfg.domain}{rel if rel.startswith('/') else '/' + rel}"


def _to_listing(cfg: VintedConfig, item) -> Listing:
    """VintedItem -> наша модель. Цена = цена продавца (то, что платим мы)."""
    photos = [p.url for p in (getattr(item, "photos", None) or []) if p.url]
    if not photos and getattr(item, "image", None):
        photos = [item.image]
    condition = _condition(item)
    return Listing(
        ad_id=vinted_ad_id(item.id or 0),
        source=SOURCE_VINTED,
        title=(item.title or "").strip(),
        price_cents=_cents(item.price or 0.0),
        description=_synthetic_description(item, condition),
        url=_item_url(cfg, item),
        category=None,
        attributes_json={
            "vinted_item_id": item.id,
            "brand_title": getattr(item, "brand_title", None),
            "size_title": getattr(item, "size_title", None),
            "condition": condition,
            "total_item_price": getattr(item, "total_item_price", None),
            "favourite_count": getattr(item, "favourite_count", None),
            "view_count": getattr(item, "view_count", None),
        },
        image_urls_json=photos[:8],
        location_city=None,  # Vinted не отдаёт город в поиске; везёт по всей DE
        seller_type="PRIVATE",
        status=STATUS_ACTIVE,
    )


class VintedClient:
    """Обёртка над vinted-scraper: политичные паузы + бюджет + часовой лимит.

    Анти-бан отдельный от KK — у Vinted свои данные rate-limit (файл свой).
    """

    def __init__(
        self,
        cfg: VintedConfig,
        delay_range: tuple[float, float] = (1.5, 3.5),
        max_requests: int = 8,
        max_requests_per_hour: int = 0,
        scraper=None,
    ) -> None:
        self.cfg = cfg
        # scraper можно подменить в тестах (без сети) — живьём реальный клиент
        self._scraper = scraper if scraper is not None else VintedScraper(baseurl=cfg.domain)
        self._delay_range = delay_range
        self._requests = 0
        self.max_requests = max_requests
        self._hourly = _HourlyLimiter(_VINTED_RATE_FILE, max_per_hour=max_requests_per_hour)

    @property
    def requests_used(self) -> int:
        return self._requests

    def _tick(self) -> None:
        self._requests += 1
        if self._requests > self.max_requests:
            raise VintedBlocked(
                f"vinted: бюджет {self.max_requests} запросов за цикл исчерпан"
            )
        self._hourly.check_and_add()

    def _pause(self) -> None:
        time.sleep(random.uniform(*self._delay_range))

    def search(self, params: dict):
        """Поиск лотов. VintedBlocked при блоке; пустой список при сбое."""
        self._pause()
        self._tick()
        try:
            return self._scraper.search(params=params) or []
        except Exception as e:  # 429/капча/смена API -> даём циклу взять паузу
            log.warning("vinted поиск %r упал: %s", params.get("search_text"), e)
            raise VintedBlocked(str(e)) from e


def _search_params(sc: VintedSearchConfig, per_page: int) -> dict:
    params: dict = {
        "search_text": sc.query,
        "order": sc.order or "newest_first",
        "per_page": per_page,
    }
    if sc.max_price_eur is not None:
        params["price_to"] = sc.max_price_eur
    if sc.catalog_ids:
        params["attribute_ids[catalog]"] = ",".join(map(str, sc.catalog_ids))
    if sc.brand_ids:
        params["attribute_ids[brand]"] = ",".join(map(str, sc.brand_ids))
    if sc.status_ids:
        params["attribute_ids[status]"] = ",".join(map(str, sc.status_ids))
    return params


def scan_vinted(
    cfg_scraper, vin: VintedConfig, client: VintedClient, max_requests: int | None = None
) -> int:
    """Один проход по Vinted-поискам. Возвращает число НОВЫХ лотов."""
    new_count = 0
    seen_this_pass: set[int] = set()
    budget = min(vin.max_requests_per_cycle, max_requests or vin.max_requests_per_cycle)
    with get_session() as s:
        ordered = [sc for sc in vin.searches if sc.query]
        random.shuffle(ordered)

        for sc in ordered:
            if client.requests_used >= budget:
                break
            items = client.search(_search_params(sc, vin.per_page))
            for item in items:
                if not item.id:
                    continue
                if _excluded(
                    item.title or "",
                    item.description or "",
                    cfg_scraper.exclude_keywords,
                    cfg_scraper.exclude_title_keywords,
                ):
                    continue
                ad_id = vinted_ad_id(item.id)
                if ad_id in seen_this_pass:
                    continue
                existing = s.get(Listing, ad_id)
                if existing is not None:
                    if existing.status == STATUS_ACTIVE:
                        existing.last_seen_at = datetime.utcnow()
                        if item.price:
                            existing.price_cents = _cents(item.price)
                    continue
                seen_this_pass.add(ad_id)
                s.add(_to_listing(vin, item))
                new_count += 1
        s.commit()
    return new_count


def _excluded(title: str, description: str, keywords: list[str], title_keywords: list[str] | None) -> bool:
    """Стоп-слова из конфига (тот же фильтр, что в KK-скане)."""
    low = (title + " " + (description or "")).lower()
    if any(k in low for k in keywords):
        return True
    return any(k in title.lower() for k in (title_keywords or []))