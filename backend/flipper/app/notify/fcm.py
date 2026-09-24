"""Отправка пушей через Firebase Cloud Messaging (§9).

Ленивый init: firebase-admin поднимается только при первом реальном пуше.
Если service-account не настроен (путь пуст) — тихо логируем и ничего не шлём,
чтобы backend работал и без Phase B.
"""
from __future__ import annotations

import logging

from flipper.app.db import get_session
from flipper.app.models import Analysis, DeviceToken
from flipper.app.notify.gate import should_notify
from flipper.config import DealConfig

log = logging.getLogger(__name__)

_app = None  # синглтон firebase_admin.App


def _get_messaging() -> object | None:
    """Возвращает firebase_admin.messaging или None (не настроено)."""
    global _app
    if _app is not None:  # уже инициализированы (или решили не настраивать)
        return _app if _app != "disabled" else None
    from flipper.config import load_config

    path = load_config().notify.fcm_service_account
    if not path:
        log.debug("FCM не настроен (fcm_service_account пуст) — пуши отключены")
        _app = "disabled"
        return None
    try:
        import firebase_admin
        from firebase_admin import credentials, messaging

        cred = credentials.Certificate(path)
        firebase_admin.initialize_app(cred)
        _app = messaging
        log.info("FCM инициализирован из %s", path)
        return _app
    except Exception as e:
        log.error("FCM не инициализирован: %s", e)
        _app = "disabled"
        return None


def _notification_payload(a: Analysis) -> dict | None:
    """Собирает титул/тело по анализу (§9): '💰 +70€ | MacBook Pro 2017'."""
    device = a.device_json or {}
    model = device.get("model") or device.get("type") or f"объявление {a.listing_id}"
    title = f"💰 +{a.margin_eur:.0f}€ | {model}"
    body = (a.action_ru or "")[:90]
    return {"title": title, "body": body, "data": {"ad_id": str(a.listing_id), "kind": "deal"}}


def _fluent_tokens(s) -> list[dict]:
    """Все зарегистрированные DeviceToken как mappings."""
    return list(s.execute(DeviceToken.__table__.select()).mappings())


def _is_unregistered(e: Exception) -> bool:
    """Firebase сообщает, что токен протух (апп удалён/переустановлен) — такой удаляем."""
    name = type(e).__name__.lower()
    if "unregistered" in name or "notfound" in name:
        return True
    code = str(getattr(e, "code", "")).lower()
    return any(x in code for x in ("token-not-registered", "invalid-registration-token", "invalid-argument"))


def _forget_tokens(rows: list[dict]) -> None:
    """Удаляем протухшие токены из БД."""
    if not rows:
        return
    with get_session() as s:
        for row in rows:
            s.execute(DeviceToken.__table__.delete().where(DeviceToken.token == row["token"]))
        s.commit()
    log.info("протухшие токены удалены: %d", len(rows))


def _send_to_rows(messaging, payload: dict, rows: list[dict], kind: str) -> int:
    """Отправка по токенам; мёртвые токены вычищаются. Возвращает число успешных."""
    sent = 0
    bad: list[dict] = []
    for row in rows:
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=payload["title"], body=payload["body"]),
                data=payload["data"],
                token=row["token"],
            )
            messaging.send(msg)
            sent += 1
        except Exception as e:
            if _is_unregistered(e):
                bad.append(row)
            log.warning("%s на %s не ушёл: %s", kind, row["token"][:16], e)
    if bad:
        _forget_tokens(bad)
    return sent


def send_to_all(title: str = "flipper тест", body: str = "Ты в базе получателей ✅") -> int:
    """Тестовый пуш всем зарегистрированным устройствам (API /notifications/test)."""
    messaging = _get_messaging()
    if messaging is None:
        return 0
    with get_session() as s:
        rows = _fluent_tokens(s)
    if not rows:
        return 0
    payload = {"title": title, "body": body, "data": {"kind": "test"}}
    return _send_to_rows(messaging, payload, rows, "тест-пуш")


def maybe_notify(a: Analysis, deal: DealConfig) -> bool:
    """Решает по §9, стоит ли пучить, и пучит.

    Условия общие (см. gate.should_notify): вердикт, маржа, живость, не отмёл.
    """
    if not should_notify(a, deal):
        return False
    return send_deal_notification(a)


def send_deal_notification(a: Analysis) -> bool:
    """Шлёт пуш всем зарегистрированным устройствам (без обид на фейлы)."""
    messaging = _get_messaging()
    if messaging is None:
        return False

    with get_session() as s:
        rows = _fluent_tokens(s)
    if not rows:
        log.info("пуш для %s: нет зарегистрированных устройств", a.listing_id)
        return False

    payload = _notification_payload(a)
    sent = _send_to_rows(messaging, payload, rows, "пуш")
    log.info("пуш %s: отправлено на %d устройств", a.listing_id, sent)
    return sent > 0