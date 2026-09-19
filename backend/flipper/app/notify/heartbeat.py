"""«Сердцебиение» бота: периодический статус в Telegram вместо тишины.

Чтобы бот не молчал часами, раз в notify.heartbeat_interval_min шлём в чат
краткую сводку: сколько новых нашёл, проанализировал и сколько TAKE за период.
Последний отправ занят флагом в settings (key=heartbeat_at) — спама нет.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select

from flipper.app.db import get_session
from flipper.app.models import Analysis, CycleRun, Listing, Setting
from flipper.app.notify.telegram import send_message

log = logging.getLogger(__name__)

_KEY = "heartbeat_at"


def _now_utc() -> datetime:
    return datetime.utcnow()


def _last_sent() -> datetime | None:
    with get_session() as s:
        row = s.get(Setting, _KEY)
        if row is None or not row.value:
            return None
        try:
            return datetime.fromisoformat(row.value)
        except ValueError:
            return None


def _mark_sent() -> None:
    with get_session() as s:
        row = s.get(Setting, _KEY)
        if row is None:
            s.add(Setting(key=_KEY, value=_now_utc().isoformat()))
        else:
            row.value = _now_utc().isoformat()
        s.commit()


def _telegram_ok() -> bool:
    from flipper.config import load_config

    n = load_config().notify
    return bool(n.telegram_bot_token and n.telegram_chat_id)


def collect_stats(since: datetime) -> dict:
    """Сводка за период: новые поиском, разобрано, TAKE, активных в базе."""
    with get_session() as s:
        new = s.execute(
            select(func.coalesce(func.sum(CycleRun.new_listings), 0)).where(
                CycleRun.started_at >= since
            )
        ).scalar()
        analyzed = s.execute(
            select(func.coalesce(func.sum(CycleRun.analyses), 0)).where(
                CycleRun.started_at >= since
            )
        ).scalar()
        takes = s.execute(
            select(func.count())
            .select_from(Analysis)
            .where(Analysis.verdict == "TAKE", Analysis.created_at >= since)
        ).scalar()
        active = s.execute(
            select(func.count()).select_from(Listing).where(Listing.status == "active")
        ).scalar()
    return {
        "new": int(new or 0),
        "analyzed": int(analyzed or 0),
        "takes": int(takes or 0),
        "active": int(active or 0),
    }


def format_heartbeat(stats: dict, period_min: int) -> str:
    hours = period_min / 60
    return (
        f"<b>💓 Пульс · за {hours:g} ч</b>\n"
        f"🔎 новых объявлений: <b>{stats['new']}</b>\n"
        f"🧠 проанализировано: <b>{stats['analyzed']}</b>\n"
        f"🟢 выгодных TAKE: <b>{stats['takes']}</b>\n"
        f"📦 активных в базе: <b>{stats['active']}</b>"
    )


def maybe_heartbeat(interval_min: int) -> bool:
    """Отправляет пульс, если подошёл интервал и канал настроен."""
    if interval_min <= 0:
        return False
    if not _telegram_ok():
        return False
    last = _last_sent()
    now = _now_utc()
    if last is not None and (now - last).total_seconds() < interval_min * 60:
        return False
    window = last if last is not None else now - timedelta(minutes=interval_min)
    stats = collect_stats(window)
    ok = send_message(format_heartbeat(stats, interval_min))
    if ok:
        _mark_sent()
    return ok


def send_heartbeat_now() -> bool:
    """Принудительный пульс (CLI test-heartbeat): сводка за последние 24 часа."""
    if not _telegram_ok():
        log.warning("telegram не настроен — пульс не отправлен")
        return False
    window = _now_utc() - timedelta(hours=24)
    stats = collect_stats(window)
    ok = send_message(format_heartbeat(stats, 24 * 60))
    if ok:
        _mark_sent()
    return ok