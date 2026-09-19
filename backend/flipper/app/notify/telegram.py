"""Telegram-уведомления о выгодных лотах (§9, альтернатива FCM).

Проще FCM: не нужен мобильный клиент — только бот и chat_id.
Включается переменными окружения FLIPPER_TELEGRAM_BOT_TOKEN / FLIPPER_TELEGRAM_CHAT_ID
(или теми же ключами в config.yaml → notify). Не настроено — тихо ничего не шлём.
"""
from __future__ import annotations

import html
import logging

import httpx

from flipper.app.db import get_session
from flipper.app.models import Analysis, Listing
from flipper.app.notify.gate import should_notify
from flipper.config import DealConfig, load_config

log = logging.getLogger(__name__)

# Bot API кладёт токен в URL — не даём httpx логировать его на INFO.
logging.getLogger("httpx").setLevel(logging.WARNING)

_API = "https://api.telegram.org/bot{token}/sendMessage"


def _configured() -> tuple[str, str] | None:
    """(token, chat_id) если настроено, иначе None."""
    n = load_config().notify
    if not n.telegram_bot_token or not n.telegram_chat_id:
        return None
    return n.telegram_bot_token, n.telegram_chat_id


def format_deal(a: Analysis) -> str:
    """Человекочитаемая HTML-карточка лота с кнопкой-ссылкой."""
    with get_session() as s:
        listing = s.get(Listing, a.listing_id)
    device = a.device_json or {}
    model = html.escape(device.get("model") or device.get("type") or f"объявление {a.listing_id}")
    city = html.escape((listing.location_city if listing else None) or "?")
    price = (listing.price_cents / 100) if listing else 0.0
    market = a.market_working_eur or 0.0

    icon = {"TAKE": "🟢", "MAYBE": "🟡", "SKIP": "⚪"}.get(a.verdict, "•")
    flip = "ремонт" if a.flip_type == "repair" else "перепродажа"
    parts = f" · вложения ~{a.parts_cost_eur:.0f}€" if a.parts_cost_eur else ""
    lines = [
        f"<b>{icon} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%) · {flip}</b>",
        f"<b>{model}</b>",
        f"Цена <b>{price:.0f}€</b> · рынок <b>{market:.0f}€</b>{parts}",
        f"📍 {city}",
    ]
    return "\n".join(lines)


def format_deal_reply_markup(url: str) -> dict:
    """Inline-кнопка «Открыть объявление» (Telegram web-app не нужен)."""
    return {"inline_keyboard": [[{"text": "Открыть объявление ↗", "url": url}]]}


def send_message(
    text: str,
    parse_mode: str | None = "HTML",
    reply_markup: dict | None = None,
) -> bool:
    """Шлёт текст в настроенный чат. False — если канал не настроен/ошибка."""
    cfg = _configured()
    if cfg is None:
        return False
    token, chat_id = cfg
    payload: dict = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        resp = httpx.post(_API.format(token=token), json=payload, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        log.warning("telegram не отправился: %s", e)
        return False


def notify_telegram(a: Analysis, deal: DealConfig) -> bool:
    """Шлёт уведомление о лоте, если он проходит общие условия (§9)."""
    if not should_notify(a, deal):
        return False
    cfg = _configured()
    if cfg is None:
        log.debug("telegram не настроен — пропуск")
        return False
    with get_session() as s:
        listing = s.get(Listing, a.listing_id)
    url = (listing.url if listing else "") or ""
    ok = send_message(
        format_deal(a), parse_mode="HTML", reply_markup=format_deal_reply_markup(url)
    )
    if ok:
        # Запоминаем цену, по которой уведомили: падение ниже = повторный алерт.
        with get_session() as s:
            row = s.get(Listing, a.listing_id)
            if row is not None:
                row.last_notified_price_cents = row.price_cents
                s.commit()
    return ok
