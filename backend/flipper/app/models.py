"""Таблицы БД (SQLite) — схема из брифа §5."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# Статус объявления в ленте
STATUS_ACTIVE = "active"
STATUS_GONE = "gone"  # 404 на сайте - удалено/продано

# Площадка, откуда приехал лот
SOURCE_KLEINANZEIGEN = "kleinanzeigen"
SOURCE_VINTED = "vinted"


def _now() -> datetime:
    return datetime.utcnow()


class Search(Base):
    """Одна строка поиска с курсором для инкрементального скана."""
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default=SOURCE_KLEINANZEIGEN)
    query: Mapped[str] = mapped_column(String(200))
    filters_json: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(default=True)
    last_cursor_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Listing(Base):
    """Объявление на Kleinanzeigen или Vinted."""
    __tablename__ = "listings"

    ad_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(20), default=SOURCE_KLEINANZEIGEN)
    title: Mapped[str] = mapped_column(String(300))
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(String(500), default="")
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)
    attributes_json: Mapped[dict] = mapped_column(JSON, default=dict)
    image_urls_json: Mapped[list] = mapped_column(JSON, default=list)
    location_city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    seller_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    views_count: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    status: Mapped[str] = mapped_column(String(10), default=STATUS_ACTIVE)
    # Цена на момент последнего уведомления пользователя (для price-drop алертов)
    last_notified_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Analysis(Base):
    """Результат LLM-анализа конкретного объявления."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.ad_id"))
    flip_type: Mapped[str] = mapped_column(String(10), default="repair")  # repair | resale
    device_json: Mapped[dict] = mapped_column(JSON, default=dict)
    fault_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # null для resale
    repairability_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    parts_cost_eur: Mapped[float] = mapped_column(Float, default=0.0)
    market_working_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    market_estimated: Mapped[bool] = mapped_column(Boolean, default=False)  # рынок из малой выборки
    margin_eur: Mapped[float] = mapped_column(Float, default=0.0)
    margin_pct: Mapped[float] = mapped_column(Float, default=0.0)
    verdict: Mapped[str] = mapped_column(String(10))  # TAKE | MAYBE | SKIP
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    pros_ru_json: Mapped[list] = mapped_column(JSON, default=list)
    cons_ru_json: Mapped[list] = mapped_column(JSON, default=list)
    action_ru: Mapped[str] = mapped_column(Text, default="")
    model_used: Mapped[str] = mapped_column(String(50), default="")
    raw_json: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PriceSample(Base):
    """Цена рабочего аналога с рынка — источник медианы для pricing."""
    __tablename__ = "price_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_key: Mapped[str] = mapped_column(String(200), index=True, default="")
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    source_search: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class PushSubscription(Base):
    """Web-Push подписка браузера/телефона (PWA)."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(500), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200), default="")
    auth: Mapped[str] = mapped_column(String(100), default="")
    send_maybe: Mapped[bool] = mapped_column(Boolean, default=False)  # дублировать и MAYBE
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UserAction(Base):
    """Действия пользователя в приложении (saved/ignored/dismissed)."""
    __tablename__ = "user_actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(ForeignKey("listings.ad_id"))
    action: Mapped[str] = mapped_column(String(20))  # saved | ignored | dismissed
    note: Mapped[str] = mapped_column(Text, default="")
    at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class DeviceToken(Base):
    """FCM-токен устройства для пуше (Phase B)."""
    __tablename__ = "device_tokens"

    token: Mapped[str] = mapped_column(String(400), primary_key=True)
    platform: Mapped[str] = mapped_column(String(20), default="android")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Setting(Base):
    """key/value тюнинга порогов из приложения."""
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


# Статус флипа в портфеле пользователя
FLIP_STOCK = "stock"  # куплено, в наличии / готовится к продаже
FLIP_SOLD = "sold"    # продано


class Flip(Base):
    """Личная сделка пользователя: купил -> (вложил) -> продал = профит.

    Заводится вручную или со страницы лота. Хранит факт покупки/продажи и
    расходы, чтобы приложение считало реальную прибыль и ROI.
    """
    __tablename__ = "flips"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listings.ad_id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(20), default=SOURCE_KLEINANZEIGEN)
    buy_price_eur: Mapped[float] = mapped_column(Float, default=0.0)
    buy_date: Mapped[datetime] = mapped_column(DateTime, default=_now)
    extras_eur: Mapped[float] = mapped_column(Float, default=0.0)  # запчасти, дорога, расходники
    status: Mapped[str] = mapped_column(String(10), default=FLIP_STOCK)
    sale_price_eur: Mapped[float | None] = mapped_column(Float, nullable=True)
    sell_fees_eur: Mapped[float] = mapped_column(Float, default=0.0)  # комиссия/доставка при продаже
    sold_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class CycleRun(Base):
    """Одна запись о проходе цикл приготовления: мониторинг стабильности (Phase B).

    outcome: ok | blocked | error | cooldown
    """
    __tablename__ = "cycle_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str] = mapped_column(String(20), default="ok")
    new_listings: Mapped[int] = mapped_column(Integer, default=0)
    analyses: Mapped[int] = mapped_column(Integer, default=0)
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")