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
        log.warning("FCM не настроен (fcm_service_account пуст) — пуши отключены")
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
        tokens = list(s.execute(DeviceToken.__table__.select()).mappings())
    if not tokens:
        log.info("пуш для %s: нет зарегистрированных устройств", a.listing_id)
        return False

    payload = _notification_payload(a)
    sent = 0
    for row in tokens:
        try:
            msg = messaging.Message(
                notification=messaging.Notification(title=payload["title"], body=payload["body"]),
                data=payload["data"],
                token=row["token"],
            )
            messaging.send(msg)
            sent += 1
        except Exception as e:
            log.warning("пуш на %s не ушёл: %s", row["token"][:16], e)
    log.info("пуш %s: отправлено на %d устройств", a.listing_id, sent)
    return sent > 0