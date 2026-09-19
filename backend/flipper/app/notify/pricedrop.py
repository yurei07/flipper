"""Слежение за ценой: алерты, когда на уже уведомлённом лоте цена упала.

Сценарий: лот был профитным (TAKE/MAYBE) и о нём уже сообщили. Продавец снизил
цену — маржа выросла, это «новый шанс». Не гоняем LLM (не тратим облачный
бюджет): пересчитываем маржу локально из сохранённого анализа и шлём карточку.
"""
from __future__ import annotations

import html
import logging

from sqlalchemy import select

from flipper.app.db import get_session
from flipper.app.models import Analysis, Listing, STATUS_ACTIVE
from flipper.app.notify.telegram import (
    _configured,
    format_deal_reply_markup,
    send_message,
)
from flipper.config import DealConfig, NotifyConfig

log = logging.getLogger(__name__)


def _latest_analysis(listing_id: int) -> Analysis | None:
    """Последний (актуальный) анализ лота."""
    with get_session() as s:
        return (
            s.execute(
                select(Analysis)
                .where(Analysis.listing_id == listing_id)
                .order_by(Analysis.created_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )


def _recompute(a: Analysis, price_eur: float, deal: DealConfig) -> tuple:
    """Маржа/вердикт от НОВОЙ цены — та же логика, что pipeline.recompute_verdict."""
    market = a.market_working_eur or 0.0
    if a.flip_type == "resale":
        cost = deal.logistics_eur
        maybe_eur = 25.0
    else:
        cost = a.parts_cost_eur or 0.0
        maybe_eur = 15.0
    margin = market - price_eur - cost
    pct = margin / price_eur * 100 if price_eur > 0 else 0.0
    if margin >= deal.min_margin_eur and pct >= deal.min_margin_pct:
        verdict = "TAKE"
    elif margin >= maybe_eur:
        verdict = "MAYBE"
    else:
        verdict = "SKIP"
    return margin, pct, verdict


def format_drop(a: Analysis, old_price_eur: float, new_price_eur: float) -> str:
    """HTML-карточка повторного алерта о снижении цены."""
    with get_session() as s:
        listing = s.get(Listing, a.listing_id)
    device = a.device_json or {}
    model = html.escape(device.get("model") or device.get("type") or f"объявление {a.listing_id}")
    city = html.escape((listing.location_city if listing else None) or "?")
    drop_pct = (old_price_eur - new_price_eur) / old_price_eur * 100 if old_price_eur else 0.0
    flip = "ремонт" if a.flip_type == "repair" else "перепродажа"
    parts = f" · вложения ~{a.parts_cost_eur:.0f}€" if a.parts_cost_eur else ""
    icon = {"TAKE": "🟢", "MAYBE": "🟡"}.get(a.verdict, "•")
    return "\n".join(
        [
            f"<b>🔻 Цена упала: {old_price_eur:.0f}€ → {new_price_eur:.0f}€ (−{drop_pct:.0f}%)</b>",
            f"<b>{icon} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%) · {flip}</b>",
            f"<b>{model}</b>",
            f"Новая цена <b>{new_price_eur:.0f}€</b> · рынок <b>{a.market_working_eur or 0:.0f}€</b>{parts}",
            f"📍 {city}",
        ]
    )


def check_price_drops(deal: DealConfig, notify: NotifyConfig) -> int:
    """Разовое сканирование уведомлённых лотов на падение цены. Возвращает число алертов."""
    if not notify.telegram_bot_token:
        return 0
    threshold = notify.price_drop_pct
    with get_session() as s:
        listings = (
            s.execute(
                select(Listing).where(
                    Listing.status == STATUS_ACTIVE,
                    Listing.last_notified_price_cents.is_not(None),
                )
            )
            .scalars()
            .all()
        )

    sent = 0
    for l in listings:
        if l.price_cents <= 0 or l.price_cents >= l.last_notified_price_cents:
            continue
        drop = (l.last_notified_price_cents - l.price_cents) / l.last_notified_price_cents * 100
        if drop < threshold:
            continue
        a = _latest_analysis(l.ad_id)
        if a is None or a.verdict not in ("TAKE", "MAYBE"):
            continue
        margin, pct, verdict = _recompute(a, l.price_cents / 100, deal)
        if verdict == "SKIP":
            continue
        a.margin_eur = margin
        a.margin_pct = pct
        a.verdict = verdict
        if send_message(
            format_drop(a, l.last_notified_price_cents / 100, l.price_cents / 100),
            parse_mode="HTML",
            reply_markup=format_deal_reply_markup(l.url or ""),
        ):
            sent += 1
            with get_session() as s:
                row = s.get(Listing, l.ad_id)
                if row is not None:
                    row.last_notified_price_cents = row.price_cents
                    s.commit()
            log.info("цена упала на лоте %s (−%.0f%%): повторное уведомление", l.ad_id, drop)
    return sent