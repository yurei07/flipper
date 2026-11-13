"""OpenAI-совместимый провайдер: Groq, OpenRouter, Cerebras, Together, vLLM…

Один и тот же протокол POST /chat/completions, отличается только base_url,
ключ и имя модели. Бесплатные тарифы (Groq и др.) дают open-source модели
класса Llama-3.3-70B / Qwen3 без своей видеокарты.
"""
from __future__ import annotations

import base64
import logging
import re
import time

import httpx

from flipper.app.analyzer.base import Provider

log = logging.getLogger(__name__)


class RateLimitedError(RuntimeError):
    """Бесплатный лимит тарифа (HTTP 429). Лимит — не поломка облака:
    лот просто дождётся следующего цикла, а не пойдёт на локальную модель."""


def _image_data_uri(url: str) -> str | None:
    """Скачиваем фото и превращаем в data:URI для multimodal-запроса."""
    try:
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.get(url)
        if r.status_code != 200:
            return None
        ctype = r.headers.get("content-type", "image/jpeg").split(";")[0]
        if not ctype.startswith("image/"):
            return None
        b64 = base64.b64encode(r.content).decode("ascii")
        return f"data:{ctype};base64,{b64}"
    except Exception as e:  # noqa: BLE001 — фото не критично, работаем по тексту
        log.warning("не смог скачать фото %s: %s", url[:70], e)
        return None


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
        max_tokens: int = 2000,
        vision: bool = False,
        max_images: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout_sec = timeout_sec
        self.retries = retries
        self.max_tokens = max_tokens
        self.vision = vision
        self.max_images = max_images

    def chat(self, system: str, user: str, images: list[str] | None = None) -> str:
        """POST /chat/completions с ретраями; 429 — ждём и повторяем."""
        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        # Multimodal: текст + фото (только если провайдер умеет смотреть картинки)
        user_content: object = user
        if images and self.vision:
            parts: list[dict] = [{"type": "text", "text": user}]
            for u in images[: self.max_images]:
                data_uri = _image_data_uri(u)
                if data_uri:
                    parts.append({"type": "image_url", "image_url": {"url": data_uri}})
            if len(parts) > 1:
                user_content = parts
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": False,
        }
        last_err: Exception | None = None
        rated = False
        for attempt in range(self.retries + 1):
            try:
                with httpx.Client(timeout=self.timeout_sec) as c:
                    r = c.post(url, json=body, headers=headers)
                if r.status_code == 429:  # rate limit бесплатного тарифа
                    rated = True
                    wait = min(max(_retry_after_seconds(r.headers.get("retry-after")), 5.0), 30.0)
                    log.warning("LLM 429 (лимит), ждём %.0fs", wait)
                    time.sleep(wait)
                    last_err = RateLimitedError("429 rate limit")
                    continue
                r.raise_for_status()
                data = r.json()
                return data["choices"][0]["message"]["content"]
            except Exception as e:
                last_err = e
                log.warning(
                    "LLM попытка %d/%d упала: %s", attempt + 1, self.retries + 1, e
                )
        if rated:
            raise RateLimitedError(f"{self.base_url} лимит исчерпан") from last_err
        raise RuntimeError(f"{self.base_url} недоступен: {last_err}") from last_err
