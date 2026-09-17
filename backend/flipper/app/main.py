"""Точка входа uvicorn + фоновое расписание (§3)."""
from __future__ import annotations

from flipper.app.api.routes import app
from flipper.app.scheduler import start_scheduler

# Храним ссылку, чтобы корректно погасить расписание на shutdown
_scheduler_state = {"scheduler": None}


def _get_scheduler():
    if _scheduler_state["scheduler"] is None:
        _scheduler_state["scheduler"] = start_scheduler()
    return _scheduler_state["scheduler"]


@app.on_event("startup")
def _on_startup() -> None:
    _get_scheduler()


@app.on_event("shutdown")
def _on_shutdown() -> None:
    sched = _scheduler_state.get("scheduler")
    if sched is not None:
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass