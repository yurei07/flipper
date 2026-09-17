"""Рыночная цена: не доверяем выдумке модели (§7).

Берём нормализованный model_key из анализа -> ищем РАБОЧИЕ аналоги на k'a ->
пишем в price_samples -> медиана последних >= 3 -> market x factor.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from flipper.app.db import get_session
from flipper.app.models import Listing, PriceSample
from flipper.app.scraper.client import ScraperClient
from flipper.config import DealConfig

log = logging.getLogger(__name__)

# Слова, означающие нерабочее состояние — их выкидываем из выборки рынка
_NOT_WORKING = (
    "defekt|kaputt|bastler|repair|nonfunctional|wasserschaden|ohne funktion"
    "|ersatzteil|as-is|nur zur abholung"
)

# Пороги разумной цены рабочей техники в этой нише
_PRICE_MIN_EUR = 10
_PRICE_MAX_EUR = 1500


def model_key(device: dict) -> str:
    """Нормализованный ключ модели из device_json.

    Пример: {"brand":"Apple","model":"MacBook Air 13 Mid 2013"} ->
    "macbook-air-13-mid-2013". Станет и search query, и ключом кэша.
    """
    brand = str(device.get("brand", "")).strip()
    model = str(device.get("model", "")).strip()
    joined = " ".join(x for x in (brand, model) if x)
    return re.sub(r"[^a-z0-9]+", "-", joined.lower()).strip("-")


def search_query(key: str) -> str:
    """Рабочий query для поиска на k'a: ключ без бренда, нижний регистр."""
    return key.replace("-", " ").strip() or ""


def fetch_market_samples(
    client: ScraperClient,
    key: str,
    max_results: int = 15,
) -> list[int]:
    """Ищет рабочие объявления по модели. Возвращает список цен в центах.

    Фильтры: title/desc не содержит признаков нерабочего, приватные, цена 10-1500 EUR.
    """
    q = search_query(key)
    if not q:
        return []
    try:
        ads = client.search(q=q, pages=1, sort_price="ASCENDING")
    except Exception as e:
        log.warning("рыночный поиск %r упал: %s", q, e)
        return []
    prices: list[int] = []
    for ad in ads:
        if _NOT_WORKING_REGEX_INTERNAL.search(
            f"{ad.title} {ad.description or ''}"
        ):
            continue  # нерабочее — не аналог
        if getattr(ad, "poster_type", None) != "PRIVATE":
            continue
        p = int(round((ad.price or 0) * 100))
        if _PRICE_MIN_EUR * 100 <= p <= _PRICE_MAX_EUR * 100:
            prices.append(p)
        if len(prices) >= max_results:
            break
    return prices


# Компилируем один regex на весь модуль, чтобы не строить его каждый вызов
_NOT_WORKING_REGEX_INTERNAL = re.compile(_NOT_WORKING, re.IGNORECASE)


def _cache_hit(sess: Session, key: str, days: int) -> list[int]:
    """Цены из кэша price_samples, свежие за N дней."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        sess.execute(
            select(PriceSample.price_cents).where(
                PriceSample.model_key == key,
                PriceSample.created_at >= cutoff,
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


def market_price_eur(
    key: str,
    client: ScraperClient,
    deal_cfg: DealConfig,
) -> tuple[float | None, bool]:
    """Итоговая рыночная цена в EUR и флаг estimate=True (если мало сэмплов).

    Возвращает (None, False) если ничего не известно (тогда используем оценку модели).
    """
    with get_session() as s:
        cached = _cache_hit(s, key, deal_cfg.price_cache_days)
        if cached:
            samples = cached
            log.info("кэш %s: %d сэмплов", key, len(samples))
        else:
            samples = fetch_market_samples(client, key)
            if not samples:
                return None, False
            # кладём свежие цены в price_samples
            for cents in samples:
                s.add(
                    PriceSample(
                        model_key=key,
                        price_cents=cents,
                        source_search=key,
                    )
                )
            s.commit()

    median = _median(samples)
    if len(samples) < deal_cfg.min_price_samples:
        # мало данных: верим модели, но хотя бы возвращаем оценку с флагом
        return median * deal_cfg.market_factor, True

    return median * deal_cfg.market_factor, False


def _median(values: list[int]) -> float:
    """Медиана цен в центах -> евро."""
    if not values:
        return 0.0
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid] / 100
    return (ordered[mid - 1] + ordered[mid]) / 2 / 100