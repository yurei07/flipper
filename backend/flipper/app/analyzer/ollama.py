"""Локальный провайдер через Ollama. Без SDK — чистый HTTP (физически проще)."""
from __future__ import annotations

import logging

import httpx

from flipper.app.analyzer.base import Provider

log = logging.getLogger(__name__)


class OllamaProvider(Provider):
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.1,
        timeout_sec: int = 120,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.retries = retries

    def chat(self, system: str, user: str, images: list[str] | None = None) -> str:
        """POST /api/chat. При неудаче — ретраи, потом поднимаем RuntimeError."""
        url = f"{self.base_url}/api/chat"
        user_msg: dict = {"role": "user", "content": user}
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                user_msg,
            ],
            "options": {"temperature": self.temperature},
            "stream": False,
        }
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_sec) as c:
                    r = c.post(url, json=body)
                    r.raise_for_status()
                    data = r.json()
                return data["message"]["content"]
            except Exception as e:
                last_err = e
                log.warning("ollama попытка %d/%d упала: %s", attempt + 1, self.retries + 1, e)
        raise RuntimeError(f"ollama недоступен: {last_err}") from last_err