"""Telegram-команды бота: /status /stats /pulse /help.

Демон-поток крутит getUpdates (long-polling) и отвечает на команды.
Ничего из цикла не трогает — только чтение БД и пульс.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

from flipper.app.notify.heartbeat import send_heartbeat_now
from flipper.app.notify.telegram import _configured, send_message

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/getUpdates"

_HELP = (
    "<b>Команды flipper:</b>\n"
    "/recent — последние лоты (с id)\n"
    "/deal &lt;id&gt; — полный вердикт по id\n"
    "/status — сводка по базе\n"
    "/stats — статистика за 24 ч\n"
    "/pulse — принудительный «пульс» сейчас\n"
    "/help — это сообщение"
)


def _reply(chat_id: int, text: str) -> None:
    send_message(text, parse_mode="HTML", chat_id=chat_id)


def _status_text() -> str:
    from sqlalchemy import func, select

    from flipper.app.db import get_session
    from flipper.app.models import Analysis, Listing

    with get_session() as s:
        active = s.execute(
            select(func.count()).select_from(Listing).where(Listing.status == "active")
        ).scalar()
        analyses = s.execute(select(func.count()).select_from(Analysis)).scalar()
        by_verdict = dict(
            s.execute(select(Analysis.verdict, func.count()).group_by(Analysis.verdict)).all()
        )
    verdicts = " · ".join(f"{k}={v}" for k, v in sorted(by_verdict.items()))
    return (
        "<b>Статус базы</b>\n"
        f"📦 активных: <b>{active}</b>\n"
        f"🧠 анализов: <b>{analyses}</b>\n"
        f"Вердикты: {verdicts}"
    )


def _recent_text(n: int) -> str:
    import html

    from sqlalchemy import select

    from flipper.app.db import get_session
    from flipper.app.models import Analysis, Listing

    with get_session() as s:
        listings = (
            s.execute(select(Listing).order_by(Listing.last_seen_at.desc()).limit(n))
            .scalars()
            .all()
        )
        if not listings:
            return "объявлений пока нет"
        latest: dict[int, Analysis] = {}
        for row in (
            s.execute(select(Analysis).order_by(Analysis.listing_id, Analysis.created_at.desc()))
            .scalars()
            .all()
        ):
            latest.setdefault(row.listing_id, row)

    lines = ["<b>Последние лоты:</b>"]
    icons = {"TAKE": "🟢", "MAYBE": "🟡", "SKIP": "⚪"}
    for l in listings:
        a = latest.get(l.ad_id)
        city = html.escape(l.location_city or "?")
        price = l.price_cents / 100
        if a:
            icon = icons.get(a.verdict, "•")
            m = f"{a.verdict} +{a.margin_eur:.0f}€"
        else:
            icon = "⏳"
            m = "не разобран"
        when = l.last_seen_at.strftime("%d.%m %H:%M")
        lines.append(
            f"{icon} <b>{m}</b> · {price:.0f}€ · {city}\n"
            f"<code>{l.ad_id}</code> · <a href=\"{html.escape(l.url)}\">{when}</a>"
        )
    lines.append("\nДетали: /deal &lt;id&gt;")
    return "\n".join(lines)


def _deal_text(ad_id: int) -> str:
    import html

    from sqlalchemy import select

    from flipper.app.db import get_session
    from flipper.app.models import Analysis, Listing

    with get_session() as s:
        listing = s.get(Listing, int(ad_id))
        if listing is None:
            return f"лота <code>{ad_id}</code> нет в базе"
        a = (
            s.execute(
                select(Analysis)
                .where(Analysis.listing_id == listing.ad_id)
                .order_by(Analysis.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        title = html.escape(listing.title)
        city = html.escape(listing.location_city or "?")
        price = listing.price_cents / 100

    head = [f"<b>{title}</b>", f"📍 {city} · <b>{price:.0f}€</b>"]
    if a is None:
        head.append("\n⏳ ещё не проанализирован")
        return "\n".join(head)

    device = a.device_json or {}
    model = html.escape(device.get("model") or device.get("type") or title)
    icon = {"TAKE": "🟢", "MAYBE": "🟡", "SKIP": "⚪"}.get(a.verdict, "•")
    flip = "ремонт" if a.flip_type == "repair" else "перепродажа"
    parts = f"~{a.parts_cost_eur:.0f}€" if a.parts_cost_eur else "—"
    details = [
        "<b>" + f"{icon} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%) · {flip}" + "</b>",
        f"<b>{model}</b>",
        f"Цена <b>{price:.0f}€</b> · рынок <b>{a.market_working_eur or 0:.0f}€</b> · вложения {parts}",
        f"Уверенность {a.confidence:.2f} · {html.escape(a.model_used or '?')} · {a.created_at:%d.%m %H:%M}",
    ]
    if a.pros_ru_json:
        details.append(
            "➕ Плюсы: " + html.escape("; ".join(a.pros_ru_json))[:400]
        )
    if a.cons_ru_json:
        details.append(
            "➖ Минусы: " + html.escape("; ".join(a.cons_ru_json))[:400]
        )
    if a.action_ru:
        details.append("🎯 " + html.escape(a.action_ru)[:400])
    return "\n".join(head + [""] + details)


def _stats_text() -> str:
    from datetime import datetime, timedelta

    from flipper.app.notify.heartbeat import collect_stats, format_heartbeat

    st = collect_stats(datetime.utcnow() - timedelta(hours=24))
    return format_heartbeat(st, 60 * 24)


def handle_command(text: str, chat_id: int) -> None:
    """Разбирает команду и отвечает в тот же чат. Незнакомое — игнор."""
    parts = (text or "").strip().lower().split()
    if not parts:
        return
    cmd = parts[0]
    if cmd in ("/start", "/help", "help"):
        _reply(chat_id, _HELP)
    elif cmd == "/status":
        _reply(chat_id, _status_text())
    elif cmd == "/stats":
        _reply(chat_id, _stats_text())
    elif cmd == "/pulse":
        ok = send_heartbeat_now()
        _reply(chat_id, "✅ пульс отправлен" if ok else "❌ telegram не настроен")
    elif cmd == "/recent":
        n = 10
        if len(parts) > 1 and parts[1].isdigit():
            n = min(int(parts[1]), 20)
        _reply(chat_id, _recent_text(n))
    elif cmd == "/deal":
        if len(parts) < 2 or not parts[1].isdigit():
            _reply(chat_id, "как: <code>/deal 1234567890</code> — id из /recent")
            return
        _reply(chat_id, _deal_text(int(parts[1])))


def apply_updates(updates: list[dict], offset: int) -> int:
    """Обрабатывает батч getUpdates, возвращает следующий offset."""
    for u in updates:
        msg = (u.get("message") or {})
        text = msg.get("text")
        chat_id = (msg.get("chat") or {}).get("id")
        if isinstance(text, str) and text.startswith("/") and chat_id:
            try:
                handle_command(text, chat_id)
            except Exception:
                log.exception("ошибка обработки команды %r", text)
        offset = max(offset, u.get("update_id", 0) + 1)
    return offset


def poll_once(offset: int) -> int:
    """Один long-poll запрос. Возвращает новый offset (или старый при сбоях молча)."""
    cfg = _configured()
    if cfg is None:
        return offset
    token = cfg[0]
    params = {"timeout": 20}
    if offset:
        params["offset"] = offset
    resp = httpx.get(_API.format(token=token), params=params, timeout=25)
    resp.raise_for_status()
    return apply_updates(resp.json().get("result", []), offset)


def _poll_loop(stop: threading.Event) -> None:
    # На старте выравниваемся по последнему апдейту — старые сообщения не читаем.
    offset = 0
    cfg = _configured()
    if cfg is not None:
        try:
            r = httpx.get(_API.format(token=cfg[0]), params={"offset": -1}, timeout=10)
            ups = r.json().get("result", [])
            offset = max((u.get("update_id", 0) + 1 for u in ups), default=0)
        except Exception as e:
            log.warning("первичный vyзов getUpdates: %s", e)
    while not stop.is_set():
        try:
            offset = poll_once(offset)
        except Exception as e:
            log.warning("polling telegram: %s", e)
            time.sleep(15)
        time.sleep(0.3)


def start_bot_thread() -> threading.Event:
    """Запускает демон-поток polling. Возвращает Event для остановки."""
    stop = threading.Event()
    threading.Thread(target=_poll_loop, args=(stop,), daemon=True, name="tg-bot").start()
    return stop