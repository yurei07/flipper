"""Тесты LLM-слоя: фабрика провайдера и OpenAI-совместимый клиент."""
from __future__ import annotations

from flipper.app.analyzer.factory import FallbackProvider, make_provider
from flipper.app.analyzer.ollama import OllamaProvider
from flipper.app.analyzer.openai_compat import OpenAICompatProvider
from flipper.config import AnalyzerConfig


def test_factory_local_returns_ollama():
    p = make_provider(AnalyzerConfig(provider="local", model="qwen3:8b"))
    assert isinstance(p, OllamaProvider)
    assert p.model == "qwen3:8b"


def test_factory_without_key_falls_back_local(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("FLIPPER_LLM_API_KEY", raising=False)
    cfg = AnalyzerConfig(provider="groq", model="llama-3.3-70b-versatile",
                         local_model="qwen3:8b")
    p = make_provider(cfg)
    assert isinstance(p, OllamaProvider)  # нет ключа -> локально
    assert p.model == "qwen3:8b"


def test_factory_with_key_wraps_fallback(monkeypatch, tmp_path):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("FLIPPER_LLM_API_KEY", raising=False)
    cfg = AnalyzerConfig(provider="groq", model="llama-3.3-70b-versatile",
                         api_key="test-key", local_model="qwen3:8b")
    p = make_provider(cfg)
    assert isinstance(p, FallbackProvider)
    assert isinstance(p.primary, OpenAICompatProvider)
    assert p.model == "llama-3.3-70b-versatile"


def test_openai_compat_parses_content(monkeypatch):
    from flipper.app.analyzer import openai_compat

    class _Resp:
        status_code = 200
        headers: dict = {}

        def json(self):
            return {"choices": [{"message": {"content": "привет"}}]}

        def raise_for_status(self):
            pass

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(openai_compat.httpx, "Client", _Client)
    p = OpenAICompatProvider("https://api.test/v1", "k", "m")
    assert p.chat("sys", "user") == "привет"


def test_retry_after_parsing():
    from flipper.app.analyzer.openai_compat import _retry_after_seconds

    assert _retry_after_seconds("12") == 12.0
    assert _retry_after_seconds("1m26.4s") == 86.4
    assert _retry_after_seconds("26s") == 26.0
    assert _retry_after_seconds("1m") == 60.0
    assert _retry_after_seconds(None) == 5.0
    assert _retry_after_seconds("garbage") == 5.0

