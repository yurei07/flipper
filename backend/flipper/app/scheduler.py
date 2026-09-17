"""Расписание циклов сканирования: cooldown + single-instance lock.

Защита от бана двумя способами:
- если дошли до блока, сейвим cooldown 1 час (правило §2.5) и НЕ ходим к API;
- если в данный момент уже бежит другой цикл (CLI или второй процесс) — пропускаем.
"""
from __future__ import annotations

import fcntl
import logging
import random
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from flipper.app.cooldown import in_cooldown, set_cooldown
from flipper.app.scraper.client import ScraperBlocked
from flipper.config import load_config

log = logging.getLogger(__name__)

_LOCK_PATH = Path("/tmp/flipper_cycle.lock")


class CycleLock:
    """Файловый лок: между CLI и таймером не бывает двух одновременных циклов."""

    def __init__(self, path: Path = _LOCK_PATH) -> None:
        self.file = open(path, "w")

    def acquire(self) -> bool:
        """True если лок получен (никто другой не бежит сейчас)."""
        try:
            fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def release(self) -> None:
        fcntl.flock(self.file, fcntl.LOCK_UN)
        self.file.close()


def run_cycle(manual: bool = False) -> None:
    """Один цикл: скан -> детали -> анализ. Общий вход для таймера и CLI.

    manual=False => таймер. manual=True => CLI run --once (тоже уважает lock).
    Возвращает (не бизнес-код, просто логи).
    """
    cfg = load_config()

    # Гарантируем инициализированную БД (таймер стартует одновременно с сервером)
    from flipper.app.db import init_db
    init_db(cfg.db.url)

    if in_cooldown():
        log.info("cooldown активен — цикл пропущен")
        return

    lock = CycleLock()
    if not lock.acquire():
        log.info("другой цикл уже работает — пропускаем")
        return

    try:
        from flipper.app.analyzer.ollama import OllamaProvider
        from flipper.app.scraper.client import ScraperClient
        from flipper.app.scraper.detail import enrich_new
        from flipper.app.scraper.search import scan_one
        from flipper.app.pipeline import analyze_pending

        client = ScraperClient(
            delay_range=cfg.scraper.detail_delay_sec,
            max_requests=cfg.scraper.max_requests_per_cycle,
        )
        provider = OllamaProvider(
            base_url=cfg.analyzer.ollama_base_url,
            model=cfg.analyzer.model,
            temperature=cfg.analyzer.temperature,
            timeout_sec=cfg.analyzer.timeout_sec,
            retries=cfg.analyzer.retries,
        )

        new = scan_one(cfg.scraper, client)
        if new:
            enrich_new(client, limit=cfg.scraper.enrich_per_cycle)
        analyses = analyze_pending(
            provider, client, cfg.deal, limit=cfg.scraper.analyze_per_cycle
        )
        log.info(
            "цикл готов: новых=%d, проанализировано=%d, запросов=%d/%d",
            new,
            len(analyses),
            client.requests_used,
            cfg.scraper.max_requests_per_cycle,
        )
    except ScraperBlocked as e:
        # Дошли до бюджета или капчи -> реально ждём час (правило §2.5)
        log.warning("блок: %s — включаю cooldown", e)
        set_cooldown()
    except Exception:
        log.exception("цикл упал")
    finally:
        lock.release()


def start_scheduler() -> None:
    """Запускает фоновое расписание APScheduler с джиттером (§2.1)."""
    cfg = load_config()
    interval = cfg.scraper.poll_interval_min
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_cycle,
        trigger=IntervalTrigger(minutes=interval, jitter=random.randint(1, 5 * 60)),
        id="crawl_cycle",
        max_instances=1,
        misfire_grace_time=60,
    )
    scheduler.start()
    log.info("scheduler: каждые %d мин (с джиттером)", interval)