"""Цикл сканирования поисков: листинги -> дедуп -> запись в БД."""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime

from flipper.app.db import get_session
from flipper.app.models import Listing, Search, STATUS_ACTIVE, _now
from flipper.app.settings import effective_excludes
from flipper.config import ScraperConfig, SearchConfig
from flipper.app.scraper.client import ScraperClient, ScraperBlocked
from sqlalchemy import select

log = logging.getLogger(__name__)


def _cents(price: float) -> int:
    """Евро -> центы (храним целыми, чтобы не плавать в float)."""
    return int(round(price * 100))


def _clean_title(title: str) -> str:
    return title.strip()


def _excluded(title: str, description: str, keywords: list[str], title_keywords: list[str] | None = None) -> bool:
    """Фильтр стоп-слов из конфига (правило §4).

    keywords — ищем в заголовке и описании; title_keywords — только в заголовке
    (аксессуары: чехлы/плёнки, чтобы не путать с техникой).
    """
    low = (title + " " + (description or "")).lower()
    if any(k in low for k in keywords):
        return True
    low_title = title.lower()
    return any(k in low_title for k in (title_keywords or []))


def _to_listing(ad) -> Listing:
    """Конвертация сырого объекта monkrel в нашу модель."""
    return Listing(
        ad_id=int(ad.id),
        title=_clean_title(ad.title),
        price_cents=_cents(ad.price or 0.0),
        description=(ad.description or "")[:10000],  # не тащим гигантский текст
        url=ad.url or "",
        category=str(ad.category_id) if getattr(ad, "category_id", None) else None,
        attributes_json=dict(getattr(ad, "attributes", {}) or {}),
        image_urls_json=[i for i in (getattr(ad, "images", None) or [])],
        location_city=getattr(ad, "city", None),
        seller_type=getattr(ad, "poster_type", None),
        status=STATUS_ACTIVE,
    )


def _db_searches(s, cfg: ScraperConfig) -> list[SearchConfig]:
    """Поиски из БД (добавленные через приложение), иначе дефолт из конфига."""
    from flipper.app.models import SOURCE_KLEINANZEIGEN

    rows = s.execute(
        select(Search).where(
            Search.active == 1, Search.source == SOURCE_KLEINANZEIGEN
        )
    ).scalars().all()
    if not rows:
        return cfg.searches
    return [
        SearchConfig(
            query=r.query,
            max_price_eur=(r.filters_json or {}).get("max_price_eur"),
            radius_km=(r.filters_json or {}).get("radius_km"),
            location=(r.filters_json or {}).get("location"),
            sort_price=(r.filters_json or {}).get("sort_price"),
            category_ids=[],
        )
        for r in rows
    ]


def scan_one(cfg: ScraperConfig, client: ScraperClient) -> int:
    """Один проход по всем поискам. Возвращает число НОВЫХ объявлений."""
    new_count = 0
    # Локальный сет: одно объявление может попасть в несколько поисков за проход
    seen_this_pass: set[int] = set()
    excl_keywords, excl_title_keywords = effective_excludes(cfg)
    with get_session() as s:
        ordered = _db_searches(s, cfg) or cfg.searches
        # Ротация ниш: за цикл берём подмножество (searches_per_cycle), со
        # сдвигом по времени — за пару циклов охватываем все, но не бомбим API.
        n = cfg.searches_per_cycle
        if n and n < len(ordered):
            step = int(time.time()) // (max(cfg.poll_interval_min, 1) * 60)
            offset = (step * n) % len(ordered)
            searches = [ordered[(offset + k) % len(ordered)] for k in range(n)]
        else:
            searches = list(ordered)
        random.shuffle(searches)  # порядок каждый раз разный (анти-паттерн)

        for sc in searches:
            try:
                ads = client.search(
                    q=sc.query,
                    max_price=sc.max_price_eur,
                    radius_km=sc.radius_km,
                    location=sc.location,
                    sort_price=sc.sort_price,
                    pages=cfg.pages_per_search,
                    category_id=sc.category_ids[0] if sc.category_ids else None,
                )
            except ScraperBlocked:
                raise  # даём хендлеру уйти в cooldown
            except Exception as e:  # один поиск упал — идём дальше, не уроним проход
                log.warning("поиск %r упал: %s", sc.query, e)
                continue

            for ad in ads:
                if _excluded(
                    ad.title,
                    ad.description or "",
                    excl_keywords,
                    excl_title_keywords,
                ):
                    continue
                ad_id = int(ad.id)
                if ad_id in seen_this_pass:
                    continue  # уже обработан в этом проходе (другой поиск)
                # Дедуп: ad_id уже в БД -> просто обновляем время последнего визита
                existing = s.get(Listing, ad_id)
                if existing is not None:
                    if existing.status == STATUS_ACTIVE:
                        existing.last_seen_at = datetime.utcnow()
                        # Живём ценой «сейчас», а не ценой первого входа:
                        # падение цены = новый шанс (price-drop алерты в notify/).
                        # Цена None (торг «VB») ценой не считается.
                        if ad.price:
                            existing.price_cents = _cents(ad.price)
                    continue
                seen_this_pass.add(ad_id)
                s.add(_to_listing(ad))
                new_count += 1
        s.commit()
    return new_count