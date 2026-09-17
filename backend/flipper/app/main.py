"""Точка входа: uvicorn flipper.app.main:app + APScheduler-расписание (§3)."""
from __future__ import annotations

import logging
import random

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from flipper.app.api.routes import app
from flipper.app.analyzer.ollama import OllamaProvider
from flipper.app.scraper.client import ScraperClient
from flipper.app.scraper.detail import enrich_new
from flipper.app.scraper.search import scan_one
from flipper.app.pipeline import analyze_pending
from flipper.config import load_config

log = logging.getLogger("flipper.scheduler")


def _run_cycle() -> None:
    """Один запланированный цикл: скан -> детали -> анализ (правила §2).
    Не бросаем исключение наружу — плохой проход не должен ронять сервер."""
    try:
        cfg = load_config()
        client = ScraperClient(delay_range=cfg.scraper.detail_delay_sec)
        provider = OllamaProvider(
            base_url=cfg.analyzer.ollama_base_url,
            model=cfg.analyzer.model,
            temperature=cfg.analyzer.temperature,
            timeout_sec=cfg.analyzer.timeout_sec,
            retries=cfg.analyzer.retries,
        )
        new = scan_one(cfg.scraper, client)
        if new:
            enrich_new(client)
        analyses = analyze_pending(provider, client, cfg.deal, limit=20)
        log.info("цикл готов: новых=%d, проанализировано=%d", new, len(analyses))
    except Exception:
        log.exception("цикл упал (cooldown 1 час)")  # §2.5: пусть таймер ждёт дальше


_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Запускает фоновое расписание. Вызывается только при запуске сервера."""
    global _scheduler
    cfg = load_config()
    interval = cfg.scraper.poll_interval_min
    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        _run_cycle,
        trigger=IntervalTrigger(minutes=interval, jitter=random.randint(1, 300)),
        id="crawl_cycle",
        misfire_grace_time=60,
    )
    _scheduler.start()


@app.on_event("startup")
def _on_startup() -> None:
    start_scheduler()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    if _scheduler:
        _scheduler.shutdown(wait=False)