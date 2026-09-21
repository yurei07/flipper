"""Web Push (VAPID) для PWA — пуши в браузер/на телефон из ленты приложения."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from flipper.app.db import get_session

log = logging.getLogger(__name__)


def _vapid_file() -> Path:
    return Path.home() / ".flipper" / "vapid.json"


def get_vapid_keys() -> tuple[str, str]:
    """(private_key, public_key). Генерирует и сохраняет ключи при первом запуске."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid, b64urlencode

    f = _vapid_file()
    if f.exists():
        data = json.loads(f.read_text())
        return data["private_key"], data["public_key"]
    keys = Vapid()
    keys.generate_keys()
    d = keys.private_key.private_numbers().private_value.to_bytes(32, "big")
    private_b64 = b64urlencode(d)
    public_b64 = b64urlencode(
        keys.public_key.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"private_key": private_b64, "public_key": public_b64}))
    log.info("сгенерированы VAPID-ключи Web Push в %s", f)
    return private_b64, public_b64


def send_push(
    s,
    title: str,
    body: str,
    url: str | None = None,
    *,
    maybe_ok: bool = False,
) -> int:
    """Разослать уведомление всем подпискам.

    maybe_ok=True — событие уровня MAYBE: уйдёт только подписчикам с send_maybe;
    иначе (TAKE/падение цены) — всем подпискам.
    Возвращает число успешных доставок.
    """
    from pywebpush import WebPushException, webpush

    from flipper.app.models import PushSubscription

    if maybe_ok:
        subs = s.query(PushSubscription).filter(PushSubscription.send_maybe.is_(True)).all()
    else:
        subs = s.query(PushSubscription).all()
    if not subs:
        return 0

    private_key, _ = get_vapid_keys()
    sent = 0
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub.endpoint,
                    "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
                },
                data=json.dumps(
                    {"title": title, "body": body, "url": url},
                    ensure_ascii=False,
                ),
                vapid_private_key=private_key,
                vapid_claims={"sub": "mailto:flipper@localhost"},
                ttl=86400,
            )
            sent += 1
        except WebPushException as e:
            if e.response is not None and e.response.status_code in (404, 410):
                s.delete(sub)  # подписка умерла — чистим
                log.info("push-подписка устарела, удалена: %s", sub.endpoint)
            else:
                log.warning("push не доставлен: %s", e)
    return sent


def notify_push(a, deal) -> int:
    """Пуш по анализу: TAKE — всем подпискам, MAYBE — только с send_maybe.

    Тот же гейт, что у Telegram (verdict, маржа, активность, не dismissed),
    общий файл flipper.app.notify.gate.
    """
    from flipper.app.models import Listing, UserAction
    from flipper.app.notify.gate import should_notify
    from flipper.config import DealConfig

    if a.verdict not in ("TAKE", "MAYBE"):
        return 0
    if a.verdict == "TAKE":
        if not should_notify(a, deal):
            return 0
        maybe_ok = False
    else:
        min_margin = (
            deal.min_margin_eur_resale if a.flip_type == "resale" else deal.min_margin_eur
        )
        if a.margin_eur < min_margin:
            return 0
        maybe_ok = True
        with get_session() as s:
            listing = s.get(Listing, a.listing_id)
            if listing is None or listing.status != "active":
                return 0
            dismissed = s.query(UserAction).filter(
                UserAction.listing_id == a.listing_id,
                UserAction.action.in_(["dismissed", "ignored"]),
            ).first()
            if dismissed is not None:
                return 0

    with get_session() as s:
        listing = s.get(Listing, a.listing_id)
        title = f"{'🟢' if a.verdict == 'TAKE' else '🟡'} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%)"
        url = listing.url if listing else ""
        body = (
            f"{listing.title if listing else ''} · {listing.location_city or ''} · "
            f"рынок {a.market_working_eur:.0f}€" if a.market_working_eur
            else f"{listing.title if listing else ''} · {listing.location_city or ''}"
        )
        return send_push(s, title, body, url or None, maybe_ok=maybe_ok)