"""Обёртка над kleinanzeigen-api: политичные паузы + детект блока."""
from __future__ import annotations

import logging
import random
import time

from kleinanzeigen_api import KleinanzeigenAPI

log = logging.getLogger(__name__)

# Коды/признаки возможного бана (наш защитник от §2)
BLOCKED_EMPTY_MAX = 3  # N пустых ответов подряд считаем капчей/блоком


class ScraperBlocked(RuntimeError):
    """Скрейпер упёрся в капчу/блок — нужен cooldown 1 час."""
    pass


class ScraperClient:
    """Единственная точка доступа к Kleinanzeigen (фолбэк добавляется сюда же)."""

    def __init__(self, delay_range: tuple[float, float] = (2.0, 6.0)) -> None:
        self._api = KleinanzeigenAPI()  # rate-limit ~1.5s внутри самой библиотеки
        self._delay_range = delay_range
        self._empty_streak = 0

    def _pause(self) -> None:
        """Случайная пауза 2-6 сек перед детальным запросом (правило §2.3)."""
        time.sleep(random.uniform(*self._delay_range))

    def _watch_empty(self, n: int) -> None:
        """Считаем пустые ответы подряд: ведёт себя как блок -> сигналим."""
        self._empty_streak = self._empty_streak + 1 if n == 0 else 0
        if self._empty_streak >= BLOCKED_EMPTY_MAX:
            raise ScraperBlocked("третий пустой ответ подряд — вероятна капча")

    def search(
        self,
        q: str,
        max_price: float | None = None,
        radius_km: int | None = None,
        location: str | None = None,
        sort_price: str | None = None,
        pages: int = 1,
    ) -> list:
        """Поиск объявлений. Возвращает сырые Listing объекта монаркл-клиента."""
        kwargs: dict = {"q": q, "pages": pages}
        if max_price is not None:
            kwargs["max_price"] = max_price
        if radius_km is not None:
            kwargs["distance_km"] = radius_km
        if location is not None:
            kwargs["location"] = location
        # По умолчанию самый свежие сверху; для «дешёвой рабочей» — по цене
        kwargs["sort_type"] = sort_price or "DATE_DESCENDING"
        ads = self._api.search(**kwargs)
        self._watch_empty(len(ads))
        return ads

    def get_ad(self, ad_id: int | str):
        """Детальная страница. Возвращает None если объявление удалено (404)."""
        self._pause()
        try:
            ad = self._api.get_ad(str(ad_id))
        except Exception:
            # monkrel бросает exception на 404 — трактуем как «продано/удалено»
            return None
        return ad