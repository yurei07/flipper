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
    return _parse_with_retry(provider, SYSTEM_PROMPT, user, raw)


def _parse_with_retry(provider: Provider, system: str, user: str, raw: str) -> AnalysisResult:
    """Пробуем распарсить. Один ретрай на экспорт market_working_eur. Потом SKIP."""
    for attempt in range(2):
        try:
            block = parse_json_block(raw)
            data = json.loads(block)
            return AnalysisResult.model_validate(data)
        except (ValueError, json.JSONDecodeError, ValidationError) as e:
            log.warning("ответ модели не прошел валидацию (попытка %d): %s", attempt + 1, e)
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