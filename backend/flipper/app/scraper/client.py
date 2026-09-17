"""Обёртка над kleinanzeigen-api: политичные паузы + детект блока.

Анти-бан поверх библиотеки:
- стабильный install-id (не «новое устройство» на каждый цикл);
- счётчик запросов (бюджет за цикл, §2);
- детект блока (пустые ответы подряд) -> ScraperBlocked -> cooldown.
"""
from __future__ import annotations

import logging
import os
import random
import time
import uuid
from pathlib import Path

from kleinanzeigen_api import KleinanzeigenAPI

log = logging.getLogger(__name__)

# Коды/признаки возможного бана (наш защитник от §2)
BLOCKED_EMPTY_MAX = 3  # N пустых ответов подряд считаем капчей/блоком

# Где храним стабильный install-id (права 0700, как у токенов)
_INSTALL_FILE = Path.home() / ".config" / "flipper" / "install_id"

# Человекочитаемые значения sort_price -> то, что понимает API
_SORT_MAP = {
    "ASCENDING": "PRICE_ASCENDING",
    "DESCENDING": "PRICE_DESCENDING",
    "DATE": "DATE_DESCENDING",
}


def _normalize_sort(sort_price: str | None) -> str:
    """Приводит sort_price к валидному sortType API (по умолчанию по дате)."""
    if not sort_price:
        return "DATE_DESCENDING"
    return _SORT_MAP.get(sort_price, sort_price)


class ScraperBlocked(RuntimeError):
    """Скрейпер упёрся в капчу/блок — нужен cooldown 1 час."""
    pass


def _load_or_create_install_id() -> str:
    """Стабильный идентификатор «установки» между запусками."""
    try:
        if _INSTALL_FILE.exists():
            return _INSTALL_FILE.read_text().strip() or _INSTALL_FILE.read_text()
        _INSTALL_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Тот же формат, что шлёт официальное приложение: uuid4 + ms-таймстамп
        value = f"{uuid.uuid4()}{int(time.time() * 1000)}"
        _INSTALL_FILE.write_text(value)
        return value
    except OSError as e:
        # Нет доступа к ~/.config — сгенерим на эту сессию, это не катастрофа
        log.warning("не сохранить install-id (%s), будет случайный", e)
        return f"{uuid.uuid4()}{int(time.time() * 1000)}"


class ScraperClient:
    """Единственная точка доступа к Kleinanzeigen. Считает запросы."""

    def __init__(
        self,
        delay_range: tuple[float, float] = (2.0, 6.0),
        max_requests: int = 25,
    ) -> None:
        self._api = KleinanzeigenAPI()
        # Подставляем стабильный install id вместо нового на каждый экземпляр.
        # Поле приватное, но это единственный способ влияния в этой библиотеке.
        self._api._xapp = _load_or_create_install_id()
        self._delay_range = delay_range
        self._empty_streak = 0
        self.max_requests = max_requests
        self._requests = 0

    # -- учёт запросов (бюджет за цикл) ------------------------------------ #
    @property
    def requests_used(self) -> int:
        return self._requests

    def _tick(self) -> None:
        """Отмечаем один исходящий запрос; по превышении бюджета — блок критерии."""
        self._requests += 1
        if self._requests > self.max_requests:
            raise ScraperBlocked(
                f"бюджет {self.max_requests} запросов за цикл исчерпан"
            )

    # -- паузы (правило §2.3) --------------------------------------------- #
    def _pause(self) -> None:
        """Случайная пауза 2-6 сек перед детальным запросом."""
        time.sleep(random.uniform(*self._delay_range))

    def _watch_empty(self, n: int) -> None:
        """Пустые ответы подряд — вероятен блок."""
        self._empty_streak = self._empty_streak + 1 if n == 0 else 0
        if self._empty_streak >= BLOCKED_EMPTY_MAX:
            raise ScraperBlocked(
                "третий пустой ответ подряд — вероятна капча/блок"
            )

    # -- API --------------------------------------------------------------- #
    def search(
        self,
        q: str,
        max_price: float | None = None,
        radius_km: int | None = None,
        location: str | None = None,
        sort_price: str | None = None,
        pages: int = 1,
        category_id: int | None = None,
    ) -> list:
        """Поиск объявлений. Возвращает сырые Listing объекты."""
        kwargs: dict = {"q": q, "pages": pages}
        if max_price is not None:
            kwargs["max_price"] = max_price
        if radius_km is not None:
            kwargs["distance_km"] = radius_km
        if location is not None:
            kwargs["location"] = location
        if category_id is not None:
            kwargs["category_id"] = category_id
        kwargs["sort_type"] = _normalize_sort(sort_price)
        self._tick()
        ads = self._api.search(**kwargs)
        self._watch_empty(len(ads))
        return ads

    def get_ad(self, ad_id: int | str):
        """Детальная страница. Возвращает None если объявление удалено (404)."""
        self._pause()
        self._tick()
        try:
            return self._api.get_ad(str(ad_id))
        except Exception:
            # monkrel бросает exception на 404 — трактуем как «продано/удалено»
            return None