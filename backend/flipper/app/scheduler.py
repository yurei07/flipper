"""Расписание циклов сканирования: cooldown + single-instance lock.

Защита от бана двумя способами:
- если дошли до блока, сейвим cooldown 1 час (правило §2.5) и НЕ ходим к API;
- если в данный момент уже бежит другой цикл (CLI или второй процесс) — пропускаем.
"""
from __future__ import annotations

import fcntl
import logging
import time
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from flipper.app.cooldown import in_cooldown, set_cooldown
from flipper.app.scraper.client import ScraperBlocked
from flipper.config import load_config

log = logging.getLogger(__name__)

_LOCK_PATH = Path("/tmp/flipper_cycle.lock")


def _record(outcome, new_listings, analyses, requests_used, error) -> int | None:
    """Создаёт запись cycle_runs. Возвращает id (для _finish) или None."""
    try:
        from flipper.app.db import get_session
        from flipper.app.models import CycleRun

        with get_session() as s:
            r = CycleRun(
                outcome=outcome,
                new_listings=new_listings,
                analyses=analyses,
                requests_used=requests_used,
                error=error,
            )
            s.add(r)
            s.commit()
            s.refresh(r)
            return r.id
    except Exception:
        log.exception("не смог записать cycle_run")  # мониторинг — не критично
        return None


def _finish(run_id, outcome, new_listings, analyses, requests_used, error) -> None:
    """Заполняет финальную статистику уже созданной записи."""
    if run_id is None:
        return
    try:
        from datetime import datetime, timezone

        from flipper.app.db import get_session
        from flipper.app.models import CycleRun

        with get_session() as s:
            row = s.get(CycleRun, run_id)
            if row is not None:
                row.outcome = outcome
                row.new_listings = new_listings
                row.analyses = analyses
                row.requests_used = requests_used
                row.error = error
                row.finished_at = datetime.now(timezone.utc)
                s.commit()
    except Exception:
        log.exception("не смог финализировать cycle_run")


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


def _in_rest_window(scraper) -> bool:
    """True, если сейчас окно отдыха (долгий перерыв после 2-3 ч работы).

    Период = active_minutes + rest_minutes, отсчёт от эпохи — детерминированно
    и без внешнего состояния: сначала 2.5 ч работы, потом 30 мин полного покоя.
    """
    active = scraper.active_minutes * 60
    rest = scraper.rest_minutes * 60
    if active <= 0 or rest <= 0 or scraper.active_minutes >= 24 * 60:
        return False
    return (time.time() % (active + rest)) >= active


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
        _record("cooldown", 0, 0, 0, "")
        return

    if _in_rest_window(cfg.scraper):
        log.info("окно отдыха — цикл пропущен (бережём аккаунт)")
        _record("rest", 0, 0, 0, "")
        return

    lock = CycleLock()
    if not lock.acquire():
        log.info("другой цикл уже работает — пропускаем")
        return

    run_id = _record("ok", 0, 0, 0, "")  # стартовая запись, финализируем ниже
    try:
        from flipper.app.analyzer.factory import make_provider
        from flipper.app.scraper.client import ScraperClient
        from flipper.app.scraper.detail import enrich_new
        from flipper.app.scraper.search import scan_one
        from flipper.app.pipeline import analyze_pending

        client = ScraperClient(
            delay_range=cfg.scraper.detail_delay_sec,
            max_requests=cfg.scraper.max_requests_per_cycle,
            max_requests_per_hour=cfg.scraper.max_requests_per_hour,
        )
        provider = make_provider(cfg.analyzer)

        new = scan_one(cfg.scraper, client)
        if new:
            enrich_new(client, limit=cfg.scraper.enrich_per_cycle)
        # Мягкий дедлайн: анализ занимает большую часть цикла, но не вылезает
        # за интервал (иначе APScheduler пропускает следующие запуски).
        analyses = analyze_pending(
            provider,
            client,
            cfg.deal,
            limit=cfg.scraper.analyze_per_cycle,
            max_seconds=cfg.scraper.poll_interval_min * 60 * 0.6,
        )
        log.info(
            "цикл готов: новых=%d, проанализировано=%d, запросов=%d/%d",
            new,
            len(analyses),
            client.requests_used,
            cfg.scraper.max_requests_per_cycle,
        )
        _finish(run_id, "ok", new, len(analyses), client.requests_used, "")
    except ScraperBlocked as e:
        # Дошли до бюджета или капчи -> реально ждём час (правило §2.5)
        log.warning("блок: %s — включаю cooldown", e)
        _finish(run_id, "blocked", 0, 0, 0, str(e))
        set_cooldown()
    except Exception as e:
        log.exception("цикл упал")
        _finish(run_id, "error", 0, 0, 0, str(e)[:500])
    finally:
        lock.release()


def start_scheduler() -> BackgroundScheduler:
    """Запускает фоновое расписание APScheduler с джиттером (§2.1)."""
    cfg = load_config()
    interval = cfg.scraper.poll_interval_min
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        run_cycle,
        trigger=IntervalTrigger(minutes=interval, jitter=60),
        id="crawl_cycle",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    log.info("scheduler: каждые %d мин (с джиттером)", interval)
    return scheduler