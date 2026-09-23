"""Загрузка конфига из config.yaml + переменных окружения."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Путь к config.yaml относительно этого файла (backend/config.yaml)
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"

# .env лежит в корне репозитория (на уровень выше backend/).
# Сервис также получает его через EnvironmentFile в systemd — здесь для CLI.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


@dataclass
class SearchConfig:
    """Одна строка поиска на Kleinanzeigen."""
    query: str
    max_price_eur: float | None = None
    radius_km: int | None = None
    location: str | None = None
    sort_price: str | None = None  # "ASCENDING" | "DESCENDING" | None (по дате)
    category_ids: list[int] = field(default_factory=list)


@dataclass
class ScraperConfig:
    poll_interval_min: int = 20
    active_minutes: int = 150
    rest_minutes: int = 30
    max_requests_per_hour: int = 70
    searches_per_cycle: int = 0  # 0 = все; >0 = ротация подмножества ниш
    pages_per_search: int = 2
    detail_delay_sec: list[float] = field(default_factory=lambda: [2.0, 6.0])
    max_requests_per_cycle: int = 25
    analyze_per_cycle: int = 5
    enrich_per_cycle: int = 5
    reanalyze_maybe_days: float = 3.0     # MAYBE старше N дней пересматриваем заново
    reanalyze_maybe_per_cycle: int = 2    # сколько задежавшихся MAYBE за цикл
    searches: list[SearchConfig] = field(default_factory=list)
    exclude_keywords: list[str] = field(default_factory=list)
    exclude_title_keywords: list[str] = field(default_factory=list)


@dataclass
class VintedSearchConfig:
    """Одна строка поиска на Vinted."""
    query: str
    max_price_eur: float | None = None
    catalog_ids: list[int] = field(default_factory=list)   # attribute_ids[catalog]
    brand_ids: list[int] = field(default_factory=list)     # attribute_ids[brand]
    status_ids: list[int] = field(default_factory=list)    # attribute_ids[status] (Zustand)
    order: str = "newest_first"


@dataclass
class VintedConfig:
    """Настройки второго источника — Vinted (своя дисциплина anti-ban)."""
    enabled: bool = False
    domain: str = "https://www.vinted.de"
    per_page: int = 24
    max_requests_per_cycle: int = 8
    max_requests_per_hour: int = 0
    analyze_requests_per_cycle: int = 0   # из цикла резервируем на дозагрузку деталей для анализа
    default_catalog_ids: list[int] = field(default_factory=list)
    searches: list[VintedSearchConfig] = field(default_factory=list)


@dataclass
class AnalyzerConfig:
    provider: str = "local"          # local | groq | openrouter | cerebras | together
    model: str = "qwen3:8b"
    temperature: float = 0.1
    timeout_sec: int = 120
    retries: int = 2
    min_confidence: float = 0.7
    ollama_base_url: str = "http://localhost:11434"
    # OpenAI-совместимый API (Groq/OpenRouter/Cerebras/Together…)
    base_url: str = ""
    api_key: str = ""                # лучше через env FLIPPER_LLM_API_KEY
    api_key_env: str = ""            # напр. "GROQ_API_KEY"
    fallback_local: bool = True      # при сбое API — падать на локальную модель
    local_model: str = "qwen3:8b"


@dataclass
class DealConfig:
    min_margin_eur: float = 40
    min_margin_pct: float = 35
    min_margin_eur_resale: float = 50
    min_margin_pct_resale: float = 40
    logistics_eur: float = 5
    notify_only_verdicts: list[str] = field(default_factory=lambda: ["TAKE"])
    market_factor: float = 0.88
    price_cache_days: int = 7
    min_price_samples: int = 3


@dataclass
class ApiConfig:
    api_key: str = ""


@dataclass
class DbConfig:
    url: str = "sqlite:///./flipper.db"


@dataclass
class NotifyConfig:
    fcm_service_account: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    heartbeat_interval_min: int = 180
    price_drop_pct: int = 15  # % падения цены на уведомлённом лоте для повторного алерта


@dataclass
class Config:
    scraper: ScraperConfig
    vinted: VintedConfig
    analyzer: AnalyzerConfig
    deal: DealConfig
    api: ApiConfig
    db: DbConfig
    notify: NotifyConfig


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    """Читает YAML, провалит кривые ключи, а не тихо их съест."""
    if not path.exists():
        raise FileNotFoundError(f"config.yaml не найден: {path}")

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    scr = raw.get("scraper", {})
    vin = raw.get("vinted", {})
    ana = raw.get("analyzer", {})
    dl = raw.get("deal", {})
    api = raw.get("api", {})
    db = raw.get("db", {})
    notif = raw.get("notify", {})

    searches = [
        SearchConfig(
            query=s.get("query", ""),
            max_price_eur=s.get("max_price_eur"),
            radius_km=s.get("radius_km"),
            location=s.get("location"),
            sort_price=s.get("sort_price"),
            category_ids=s.get("category_ids", []),
        )
        for s in scr.get("searches", [])
    ]

    return Config(
        scraper=ScraperConfig(
            poll_interval_min=scr.get("poll_interval_min", 20),
            active_minutes=scr.get("active_minutes", 150),
            rest_minutes=scr.get("rest_minutes", 30),
            max_requests_per_hour=scr.get("max_requests_per_hour", 70),
            searches_per_cycle=scr.get("searches_per_cycle", 0),
            pages_per_search=scr.get("pages_per_search", 2),
            detail_delay_sec=scr.get("detail_delay_sec", [2.0, 6.0]),
            max_requests_per_cycle=scr.get("max_requests_per_cycle", 25),
            analyze_per_cycle=scr.get("analyze_per_cycle", 5),
            enrich_per_cycle=scr.get("enrich_per_cycle", 5),
            reanalyze_maybe_days=scr.get("reanalyze_maybe_days", 3.0),
            reanalyze_maybe_per_cycle=scr.get("reanalyze_maybe_per_cycle", 2),
            searches=searches,
            exclude_keywords=scr.get("exclude_keywords", []),
            exclude_title_keywords=scr.get("exclude_title_keywords", []),
        ),
        vinted=VintedConfig(
            enabled=vin.get("enabled", False),
            domain=vin.get("domain", "https://www.vinted.de").rstrip("/"),
            per_page=vin.get("per_page", 24),
            max_requests_per_cycle=vin.get("max_requests_per_cycle", 8),
            max_requests_per_hour=vin.get("max_requests_per_hour", 0),
            analyze_requests_per_cycle=vin.get("analyze_requests_per_cycle", 0),
            default_catalog_ids=vin.get("default_catalog_ids", []),
            searches=[
                VintedSearchConfig(
                    query=s.get("query", ""),
                    max_price_eur=s.get("max_price_eur"),
                    catalog_ids=s.get("catalog_ids", []),
                    brand_ids=s.get("brand_ids", []),
                    status_ids=s.get("status_ids", []),
                    order=s.get("order", "newest_first"),
                )
                for s in vin.get("searches", [])
            ],
        ),
        analyzer=AnalyzerConfig(
            provider=ana.get("provider", "local"),
            model=ana.get("model", "qwen3:8b"),
            temperature=ana.get("temperature", 0.1),
            timeout_sec=ana.get("timeout_sec", 120),
            retries=ana.get("retries", 2),
            min_confidence=ana.get("min_confidence", 0.7),
            ollama_base_url=ana.get("ollama_base_url", "http://localhost:11434"),
            base_url=os.getenv("FLIPPER_LLM_BASE_URL", ana.get("base_url", "")),
            api_key=os.getenv("FLIPPER_LLM_API_KEY", ana.get("api_key", "")),
            api_key_env=ana.get("api_key_env", ""),
            fallback_local=ana.get("fallback_local", True),
            local_model=ana.get("local_model", "qwen3:8b"),
        ),
        deal=DealConfig(
            min_margin_eur=dl.get("min_margin_eur", 40),
            min_margin_pct=dl.get("min_margin_pct", 35),
            min_margin_eur_resale=dl.get("min_margin_eur_resale", 50),
            min_margin_pct_resale=dl.get("min_margin_pct_resale", 40),
            logistics_eur=dl.get("logistics_eur", 5),
            notify_only_verdicts=dl.get("notify_only_verdicts", ["TAKE"]),
            market_factor=dl.get("market_factor", 0.88),
            price_cache_days=dl.get("price_cache_days", 7),
            min_price_samples=dl.get("min_price_samples", 3),
        ),
        api=ApiConfig(api_key=os.getenv("FLIPPER_API_KEY", api.get("api_key", ""))),
        db=DbConfig(url=db.get("url", "sqlite:///./flipper.db")),
        notify=NotifyConfig(
            fcm_service_account=os.getenv(
                "FLIPPER_FCM_SERVICE_ACCOUNT", notif.get("fcm_service_account", "")
            ),
            telegram_bot_token=os.getenv(
                "FLIPPER_TELEGRAM_BOT_TOKEN", notif.get("telegram_bot_token", "")
            ),
            telegram_chat_id=os.getenv(
                "FLIPPER_TELEGRAM_CHAT_ID", notif.get("telegram_chat_id", "")
            ),
            heartbeat_interval_min=notif.get("heartbeat_interval_min", 180),
            price_drop_pct=notif.get("price_drop_pct", 15),
        ),
    )