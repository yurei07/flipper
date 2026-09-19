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
    """Старт: расписание циклов + Telegram-команды. Стоп: корректно их гасим."""
    scheduler = start_scheduler()
    from flipper.app.notify.bot import start_bot_thread

    stop_bot = start_bot_thread()
    try:
        yield
    finally:
        stop_bot.set()
        try:
            scheduler.shutdown(wait=False)
        except Exception:
            log.exception("не смог погасить scheduler")


app = make_app(lifespan=_lifespan)
