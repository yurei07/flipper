"""Интерфейс провайдера LLM. Реализации: ollama.py (+ потом groq.py/deepseek.py)."""
from __future__ import annotations

from abc import ABC, abstractmethod


class Provider(ABC):
    """Обязательный контракт любого LLM-провайдера."""

    model: str
    temperature: float
    timeout_sec: int
    retries: int

    @abstractmethod
    def chat(self, system: str, user: str) -> str:
        """Один запрос, возвращает СЫРОЙ текст ответа модели."""
        raise NotImplementedError