"""Точка входа uvicorn + фоновое расписание (§3).

FastAPI lifespan поднимает APScheduler на старте и гасит на shutdown.
Раньше здесь были @app.on_event (deprecated) — из-за этого shutdown
расписания не срабатывал.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from flipper.app.api.routes import make_app
from flipper.app.scheduler import start_scheduler

# Показываем наши логгеры (flipper.*) на уровне INFO — scheduler/циклы пишут туда
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

log = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app):
    """Старт: поднимаем расписание циклов. Стоп: корректно гасим его."""
    scheduler = start_scheduler()
    try:
        yield
    finally:
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            log.exception("не смог погасить scheduler")


app = make_app(lifespan=_lifespan)
