"""OpenAI-совместимый провайдер: Groq, OpenRouter, Cerebras, Together, vLLM…

Один и тот же протокол POST /chat/completions, отличается только base_url,
ключ и имя модели. Бесплатные тарифы (Groq и др.) дают open-source модели
класса Llama-3.3-70B / Qwen3 без своей видеокарты.
"""
from __future__ import annotations

import logging
import re
import time

import httpx

from flipper.app.analyzer.base import Provider

log = logging.getLogger(__name__)


def _retry_after_seconds(value: str | None, default: float = 5.0) -> float:
    """Разбор Retry-After: число секунд или формат «1m26.4s»/«26s»/«1m»."""
    if not value:
        return default
    v = value.strip().lower()
    try:
        return float(v)
    except ValueError:
        pass
    m = re.match(r"^(?:(\d+)m)?(?:([\d.]+)s)?$", v)
    if not m or (m.group(1) is None and m.group(2) is None):
        return default
    return int(m.group(1) or 0) * 60 + float(m.group(2) or 0)



class OpenAICompatProvider(Provider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        temperature: float = 0.1,
        timeout_sec: int = 60,
        retries: int = 2,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.retries = retries

    def chat(self, system: str, user: str) -> str:
        """POST /chat/completions с ретраями; 429 — ждём и повторяем."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.temperature,
            "max_tokens": 2500,  # ответ — компактный JSON, но gpt-oss тратит часть на reasoning
            "stream": False,
        }
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_sec) as c:
                    r = c.post(url, json=body, headers=headers)
                if r.status_code == 429:  # rate limit бесплатного тарифа
                    wait = min(max(_retry_after_seconds(r.headers.get("retry-after")), 5.0), 30.0)
                    log.warning("LLM 429 (лимит), ждём %.0fs", wait)
                    time.sleep(wait)
                    last_err = RuntimeError("429 rate limit")
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_err = e
                log.warning(
                    "LLM попытка %d/%d упала: %s", attempt + 1, self.retries + 1, e
                )
        raise RuntimeError(f"{self.base_url} недоступен: {last_err}") from last_err
