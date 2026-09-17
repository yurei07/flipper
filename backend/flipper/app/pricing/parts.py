"""Типовые цены запчастей: кэш + эвристика, если модель дала нули (§7.2)."""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Базовые цены на ходовые запчасти в EUR (экономный б/у вариант)
PART_FALLBACKS: dict[str, float] = {
    "display": 55.0,
    "battery": 35.0,
    "keyboard": 25.0,
    "trackpad": 20.0,
    "fan": 15.0,
    "dcjack": 10.0,
    "ssd": 30.0,
    "ram": 25.0,
    "charging_port": 20.0,
    "screen_cable": 15.0,
}


# Простейший кэш: model_key -> (parts_cost_eur, timestamp) для 24 ч
_cache: dict[str, tuple[float, float]] = {}


def get_parts_cost(model_key: str, model_guess: float) -> float:
    """Вернуть оценку стоимости запчастей.

    Приоритет: уже посчитанное сегодня (кэш) > оценка модели (если > 5 EUR)
    > фолбэк из хитовых запчастей.
    """
    if model_key in _cache:
        val, ts = _cache[model_key]
        if ts >= 0:
            return val  # валидная запись из этой сессии

    if model_guess and model_guess >= 5.0:
        _cache[model_key] = (model_guess, 0.0)
        return model_guess

    # Совсем без данных — ставим разумный фолбэк для мелких ремонтов
    fallback = PART_FALLBACKS.get(model_key.split("-")[0], 30.0)
    if model_key not in _cache:
        _cache[model_key] = (fallback, 0.0)
    log.info("parts: фолбэк для %s = %.0f EUR", model_key, fallback)
    return fallback