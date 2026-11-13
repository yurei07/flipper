"""Интерфейс провайдера LLM. Реализации: ollama.py (+ потом groq.py/deepseek.py)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    """Обязательный контракт любого LLM-провайдера."""

    model: str
    temperature: float
    timeout_sec: int
    retries: int
    vision: bool = False  # умеет ли провайдер смотреть фото

    @abstractmethod
    def chat(self, system: str, user: str, images: list[str] | None = None) -> str:
        """Один запрос, возвращает СЫРОЙ текст ответа модели.

        images — список URL фото объявления (используется только если vision=True).
        """
        raise NotImplementedError