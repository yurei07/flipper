"""Высокоуровневый запуск анализа одного объявления.

Пайплайн: listing → промпт → модель → сырой текст → JSON → pydantic → Analysis.
Bad output: 1 ретрай, затем verdict=SKIP по правилу §6.
"""
from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from flipper.app.analyzer.base import Provider
from flipper.app.analyzer.prompt import SYSTEM_PROMPT, build_user_prompt
from flipper.app.analyzer.schema import AnalysisResult, parse_json_block
from flipper.app.models import Listing

log = logging.getLogger(__name__)


def analyze_listing(listing: Listing, provider: Provider) -> AnalysisResult:
    """Возвращает распарсенный и СХЕМО-провалидированный результат анализа."""
    user = build_user_prompt(listing)
    raw = provider.chat(SYSTEM_PROMPT, user)
    return _parse_with_retry(listing, provider, SYSTEM_PROMPT, user, raw)


def _parse_with_retry(
    listing: Listing, provider: Provider, system: str, user: str, raw: str
) -> AnalysisResult:
    """Пробуем распарсить. Один ретрай на экспорт market_working_eur. Потом SKIP."""
    for attempt in range(2):
        try:
            block = parse_json_block(raw)
            data = json.loads(block)
        except (ValueError, json.JSONDecodeError) as e:
            log.warning("кривой JSON от модели (попытка %d): %s", attempt + 1, e)
            if attempt == 0:
                raw = provider.chat(system, user)  # ретрай: у модели мог выйти кривой JSON
            continue
        if data.get("device", {}).get("type") == "accessory":
            return _accessory_result(listing)
        try:
            return AnalysisResult.model_validate(data)
        except ValidationError as e:
            log.warning("ответ модели не прошёл валидацию (попытка %d): %s", attempt + 1, e)
            if attempt == 0:
                raw = provider.chat(system, user)  # ретрай: у модели мог выйти кривой JSON
    return AnalysisResult(
        flip_type="repair",
        device={"type": "unknown"},
        deal={
            "price_ask_eur": listing.price_cents / 100,
            "verdict": "SKIP",
            "confidence": 0.0,
            "action_ru": "не удалось распарсить ответ модели",
        },
        flag_reasons_ru=["не удалось распарсить ответ модели"],
    )


def _accessory_result(listing: Listing) -> AnalysisResult:
    """Модель распознала аксессуар → честный SKIP, а не мусорный TAKE."""
    return AnalysisResult(
        flip_type="resale",
        device={"type": "accessory", "brand": "", "model": "", "specs_hint": ""},
        deal={
            "price_ask_eur": listing.price_cents / 100,
            "market_working_eur": 0.0,
            "repair_cost_eur": 0.0,
            "margin_eur": 0.0,
            "margin_pct": 0.0,
            "resale_speed_hint_ru": "",
            "verdict": "SKIP",
            "confidence": 1.0,
            "pros_ru": [],
            "cons_ru": [],
            "action_ru": "аксессуар, не устройство",
        },
        flag_reasons_ru=["модель распознала аксессуар"],
    )