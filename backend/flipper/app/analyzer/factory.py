"""Фабрика LLM-провайдера: локальный Ollama или бесплатный OpenAI-совместимый API.

Приоритет — тот, что указан в config.yaml (analyzer.provider). Если для облака
нет ключа или оно падает — при fallback_local=True работаем локальной моделью,
чтобы сканер не останавливался.
"""
from __future__ import annotations

import logging
import os

from flipper.app.analyzer.base import Provider
from flipper.app.analyzer.ollama import OllamaProvider
from flipper.app.analyzer.openai_compat import OpenAICompatProvider, RateLimitedError
from flipper.config import AnalyzerConfig

log = logging.getLogger(__name__)

# base_url и env-переменная с ключом для популярных бесплатных провайдеров
_PRESETS: dict[str, tuple[str, str]] = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "together": ("https://api.together.xyz/v1", "TOGETHER_API_KEY"),
}


class FallbackProvider(Provider):
    """Пробуем облако, при ошибке — локальная модель (никогда не стоим)."""

    def __init__(self, primary: Provider, fallback: Provider) -> None:
        self.primary = primary
        self.fallback = fallback
        self.model = primary.model
        self.vision = getattr(primary, "vision", False)

    def chat(self, system: str, user: str, images: list[str] | None = None) -> str:
        try:
            return self.primary.chat(system, user, images=images)
        except RateLimitedError:
            # Лимит бесплатного тарифа — не поломка облака: не играем в «умную»
            # локальную модель, а оставляем лот следующим циклам (качество выше).
            log.warning("облачная LLM залимитирована (429) — лот перейдёт на следующий цикл")
            raise
        except Exception as e:
            log.warning("облачная LLM упала (%s) — перехожу на локальную", e)
            return self.fallback.chat(system, user)


def _ollama(cfg: AnalyzerConfig, model: str | None = None) -> OllamaProvider:
    return OllamaProvider(
        base_url=cfg.ollama_base_url,
        model=model or cfg.model,
        temperature=cfg.temperature,
        timeout_sec=cfg.timeout_sec,
        retries=cfg.retries,
    )


def make_provider(cfg: AnalyzerConfig) -> Provider:
    """Собирает провайдера по конфигу, с деградацией до локального."""
    provider = (cfg.provider or "local").lower()
    if provider == "local":
        return _ollama(cfg)

    preset_url, preset_env = _PRESETS.get(provider, ("", ""))
    base_url = cfg.base_url or preset_url
    api_key = (
        cfg.api_key
        or (os.getenv(cfg.api_key_env) if cfg.api_key_env else "")
        or os.getenv(preset_env)
        or os.getenv("FLIPPER_LLM_API_KEY")
        or ""
    )
    if not base_url or not api_key:
        log.warning(
            "провайдер '%s' не сконфигурирован (base_url=%r, ключ есть=%s) —"
            " работаю локально (%s)",
            provider,
            bool(base_url),
            bool(api_key),
            cfg.local_model,
        )
        return _ollama(cfg, model=cfg.local_model)

    primary: Provider = OpenAICompatProvider(
        base_url=base_url,
        api_key=api_key,
        model=cfg.model,
        temperature=cfg.temperature,
        timeout_sec=cfg.timeout_sec,
        retries=cfg.retries,
        max_tokens=cfg.max_tokens,
        vision=cfg.vision,
    )
    log.info("LLM: %s (%s)%s", cfg.model, provider, " +фото" if cfg.vision else "")
    if cfg.fallback_local:
        return FallbackProvider(primary, _ollama(cfg, model=cfg.local_model))
    return primary
