"""Детальные страницы: добираем описание и помечаем удалённые (404=gone)."""
from __future__ import annotations

import logging

from sqlalchemy import select

from flipper.app.db import get_session
from flipper.app.models import Listing, STATUS_GONE, _now
from flipper.app.scraper.client import ScraperClient

log = logging.getLogger(__name__)


def enrich_new(client: ScraperClient) -> int:
    """Идём по объявлениям БЕЗ описания (новым) и добираем детальную страницу.

    Объявление удалено на сайте -> статус gone (к нему больше не листаем).
    Возвращает число обогащённых.
    """

    with get_session() as s:
        # Кандидаты: ещё активны и описание пустое (т.е. только что вставлены)
        rows = (
            s.execute(
                select(Listing)
                .where(Listing.status == "active", Listing.description == "")
                .limit(20)
            )
            .scalars()
            .all()
        )

    enriched = 0
    for listing in rows:
        ad = client.get_ad(listing.ad_id)
        with get_session() as s:
            obj = s.get(Listing, listing.ad_id)
            if obj is None:
                continue
            if ad is None:
                # 404 -> удалено/продано
                obj.status = STATUS_GONE
                log.info("ad %s удалён (gone)", obj.ad_id)
            else:
                obj.description = (ad.description or "")[:10000]
                if getattr(ad, "attributes", None):
                    obj.attributes_json = dict(ad.attributes)
                obj.last_seen_at = _now()
                enriched += 1
            s.commit()
    return enriched