"""Юнит-тесты без сети. Живые интеграционные — маркер @pytest.mark.live."""
from __future__ import annotations

import pytest

from flipper.app.analyzer.schema import AnalysisResult, Verdict, parse_json_block
from flipper.app.pricing.market import _median


# ---------- §13.7: парсинг архитектуры JSON-ответа модели ----------

def test_parse_json_block_ignores_wrap_text():
    text = "Вот результат:\n```json\n{\"a\": 1}\n```\nконец"
    assert parse_json_block(text) == '{"a": 1}'


def test_parse_json_block_raises_without_braces():
    with pytest.raises(ValueError):
        parse_json_block("совсем без JSON")


def test_analysis_schema_valid():
    res = AnalysisResult.model_validate(
        {
            "flip_type": "repair",
            "device": {"type": "laptop", "brand": "Apple", "model": "MacBook Air"},
            "fault": {"summary_ru": "экран", "water_damage": False, "locked": False},
            "repairability": {
                "score": 2, "home_repairable": True, "what_needs_fixing_ru": "экран",
                "tools_needed": [], "parts_needed": [], "parts_cost_eur": 60,
                "time_hours": 1, "explanation_ru": "просто",
            },
            "deal": {
                "price_ask_eur": 100, "market_working_eur": 250, "repair_cost_eur": 60,
                "margin_eur": 90, "margin_pct": 90, "resale_speed_hint_ru": "",
                "verdict": "TAKE", "confidence": 0.9,
                "pros_ru": [], "cons_ru": [], "action_ru": "брать",
            },
            "flag_reasons_ru": [],
        }
    )
    assert res.flip_type.value == "repair"
    assert res.deal.verdict == Verdict.TAKE


def test_analysis_schema_allows_resale_without_fault():
    res = AnalysisResult.model_validate(
        {
            "flip_type": "resale",
            "device": {"type": "laptop", "brand": "Lenovo", "model": "ThinkPad T480"},
            "fault": None,
            "repairability": None,
            "deal": {
                "price_ask_eur": 60, "market_working_eur": 130, "repair_cost_eur": 0,
                "margin_eur": 65, "margin_pct": 108,
                "resale_speed_hint_ru": "", "verdict": "TAKE", "confidence": 0.8,
                "pros_ru": [], "cons_ru": [], "action_ru": "брать",
            },
            "flag_reasons_ru": [],
        }
    )
    assert res.flip_type.value == "resale"
    assert res.fault is None and res.repairability is None


# ---------- §13.7: медиана (ценовой модуль) ----------

def test_median_odd():
    # цены в центах, медиана возвращается в евро
    assert _median([100, 200, 300]) == pytest.approx(2.0)


def test_median_even():
    assert _median([100, 200, 300, 400]) == pytest.approx(2.5)


def test_median_empty():
    assert _median([]) == 0.0