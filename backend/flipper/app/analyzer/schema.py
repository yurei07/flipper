"""Pydantic-схема ответа LLM. Поля строго по §6 брифа (плюс flip_type)."""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    TAKE = "TAKE"
    MAYBE = "MAYBE"
    SKIP = "SKIP"


class FlipType(str, Enum):
    repair = "repair"  # сломанное -> чиним -> продаём
    resale = "resale"  # рабочее/косметика -> перепродаём как есть


class Device(BaseModel):
    type: str = ""                        # laptop / phone / monitor ...
    brand: str = ""
    model: str = ""
    specs_hint: str = ""


class Fault(BaseModel):
    summary_ru: str = ""
    water_damage: bool = False
    locked: bool = False                  # iCloud/FRP — красный флаг


class Repairability(BaseModel):
    score: int = 3                        # сложность 1..5
    home_repairable: bool = True
    what_needs_fixing_ru: str = ""
    tools_needed: list[str] = Field(default_factory=list)
    parts_needed: list[str] = Field(default_factory=list)
    parts_cost_eur: float = 0.0
    time_hours: float = 0.0
    explanation_ru: str = ""


class Deal(BaseModel):
    price_ask_eur: float = 0.0
    market_working_eur: float = 0.0       # ОЦЕНКА модели; сервер перезапишет (§7)
    repair_cost_eur: float = 0.0
    margin_eur: float = 0.0
    margin_pct: float = 0.0
    resale_speed_hint_ru: str = ""
    verdict: Verdict
    confidence: float = 0.0
    pros_ru: list[str] = Field(default_factory=list)
    cons_ru: list[str] = Field(default_factory=list)
    action_ru: str = ""


class AnalysisResult(BaseModel):
    flip_type: FlipType = FlipType.repair
    device: Device = Field(default_factory=Device)
    fault: Fault | None = None            # null для resale
    repairability: Repairability | None = None  # null для resale
    deal: Deal
    flag_reasons_ru: list[str] = Field(default_factory=list)


def parse_json_block(text: str) -> dict:
    """Извлекает JSON из ответа модели даже если она добавила текст вокруг."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("нет JSON-блока в ответе модели")
    return text[start : end + 1]