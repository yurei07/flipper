"""Общие условия отправки уведомления (§9).

Используется всеми каналами (FCM, Telegram), чтобы не дублировать логику:
вердикт в списке, маржа выше порога, объявление живо и не отмёчено пользователем.
"""
from __future__ import annotations

from sqlalchemy import select

from flipper.app.db import get_session
from flipper.app.models import Analysis, Listing, UserAction
from flipper.config import DealConfig


def should_notify(a: Analysis, deal: DealConfig) -> bool:
    """True, если об анализе стоит уведомлять пользователя."""
    if a.verdict not in deal.notify_only_verdicts:
        return False

    min_margin = (
        deal.min_margin_eur_resale if a.flip_type == "resale" else deal.min_margin_eur
    )
    if a.margin_eur < min_margin:
        return False

    with get_session() as s:
        listing = s.get(Listing, a.listing_id)
        if listing is None or listing.status != "active":
            return False
        dismissed = s.execute(
            select(UserAction).where(
                UserAction.listing_id == a.listing_id,
                UserAction.action.in_(["dismissed", "ignored"]),
            )
        ).scalars().first()
        if dismissed is not None:
            return False
    return True
