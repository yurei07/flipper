"""Cooldown (правило §2.5): при блоке запрещаем ходить к API час.

Состояние лежит в таблице settings (key=cooldown_until, ISO-строка).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flipper.app.db import get_session
from flipper.app.models import Setting

log = logging.getLogger(__name__)

_KEY = "cooldown_until"
DEFAULT_COOLDOWN_MIN = 60


def _now_utc() -> datetime:
    return datetime.now(ZoneInfo("UTC"))


def _ensure_db() -> None:
    """Гарантируем инициализированную БД (settings-таблица)."""
    from flipper.app.db import init_db
    from flipper.config import load_config

    try:
        init_db(load_config().db.url)
    except Exception:
        pass


def get_cooldown() -> datetime | None:
    """Вернёт datetime, до которого нельзя ходить (None = можно)."""
    _ensure_db()
    with get_session() as s:
        row = s.get(Setting, _KEY)
        if row is None or not row.value:
            return None
        try:
            return datetime.fromisoformat(row.value)
        except ValueError:
            return None


def in_cooldown() -> bool:
    """True = сейчас нельзя трогать API."""
    until = get_cooldown()
    return until is not None and _now_utc() < until


def set_cooldown(minutes: int = DEFAULT_COOLDOWN_MIN) -> datetime:
    """Устанавливает cooldown на N минут. Возвращает момент окончания."""
    _ensure_db()
    until = _now_utc() + timedelta(minutes=minutes)
    with get_session() as s:
        row = s.get(Setting, _KEY)
        if row is None:
            s.add(Setting(key=_KEY, value=until.isoformat()))
        else:
            row.value = until.isoformat()
        s.commit()
    log.warning("cooldown до %s UTC", until.isoformat())
    return until


def clear_cooldown() -> None:
    """Ручной сброс (на случай ложного срабатывания)."""
    _ensure_db()
    with get_session() as s:
        row = s.get(Setting, _KEY)
        if row is not None:
            s.delete(row)
            s.commit()