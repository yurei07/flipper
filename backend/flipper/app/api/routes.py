"""FastAPI-приложение: health + базовые REST-роуты (§8, базовый минимум для Phase A)."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipper.app.db import get_session
from flipper.app.models import (
    Analysis,
    DeviceToken,
    Flip,
    FLIP_SOLD,
    FLIP_STOCK,
    Listing,
    PushSubscription,
    Search,
    UserAction,
)
from flipper.config import Config, load_config
from flipper.app.db import init_db

cfg: Config = load_config()


class RegisterBody(BaseModel):
    """Тело регистрации FCM-токена телефона (§8)."""

    fcm_token: str = Field(min_length=10, max_length=4096)
    platform: str = "android"


class SubscribeBody(BaseModel):
    """Тело подписки Web Push (браузер/телефон PWA)."""

    endpoint: str = Field(min_length=10, max_length=1000)
    p256dh: str = Field(min_length=10, max_length=300)
    auth: str = Field(min_length=1, max_length=200)
    send_maybe: bool = False


class SearchBody(BaseModel):
    """Тело создания/обновления поиска (управление из приложения).

    source='kleinanzeigen' | 'vinted'. Для KK важен radius/location,
    для Vinted — catalog/brand/status ids. Остальное — общее (цена, активность).
    """

    query: str = Field(min_length=1, max_length=200)
    source: str = "kleinanzeigen"
    max_price_eur: float | None = None
    radius_km: int | None = None
    location: str | None = None
    sort_price: str | None = None
    catalog_ids: list[int] = Field(default_factory=list)
    brand_ids: list[int] = Field(default_factory=list)
    status_ids: list[int] = Field(default_factory=list)
    order: str | None = None
    active: bool = True


class SettingsBody(BaseModel):
    """Патч настроек из приложения (стоп-слова, вкл/выкл Vinted и т.п.)."""

    exclude_keywords: list[str] | None = None
    exclude_title_keywords: list[str] | None = None
    vinted_enabled: bool | None = None
    vinted_default_catalog_ids: list[int] | None = None


class FlipBody(BaseModel):
    """Новая личная сделка: «купил» со страницы лота или вручную."""

    listing_id: int | None = None
    title: str = Field(default="", max_length=300)
    source: str | None = None
    buy_price_eur: float = 0.0
    extras_eur: float = 0.0
    buy_date: str | None = None  # ISO-дата
    note: str = Field(default="", max_length=2000)


class FlipUpdate(BaseModel):
    """Патч сделки: правки суммы, статус «продано», цена/расходы продажи."""

    title: str | None = Field(default=None, max_length=300)
    buy_price_eur: float | None = None
    extras_eur: float | None = None
    status: str | None = None  # stock | sold
    sale_price_eur: float | None = None
    sell_fees_eur: float | None = None
    sold_date: str | None = None
    note: str | None = Field(default=None, max_length=2000)


def _parse_date(value: str | None):
    """Парсит ISO-дату из клиента; пустую строку трактует как None."""
    from datetime import datetime

    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise HTTPException(status_code=422, detail="bad date")


def _flip_dict(f: Flip) -> dict:
    """Сделка + посчитанная экономика (вложено, профит, ROI)."""
    invested = round((f.buy_price_eur or 0.0) + (f.extras_eur or 0.0), 2)
    if f.status == FLIP_SOLD and f.sale_price_eur is not None:
        revenue = round(f.sale_price_eur - (f.sell_fees_eur or 0.0), 2)
        profit = round(revenue - invested, 2)
        roi = round(profit / invested * 100, 1) if invested > 0 else None
    else:
        revenue = None
        profit = None
        roi = None
    return {
        "id": f.id,
        "listing_id": f.listing_id,
        "title": f.title,
        "source": f.source,
        "buy_price_eur": f.buy_price_eur,
        "extras_eur": f.extras_eur,
        "buy_date": f.buy_date.isoformat() if f.buy_date else None,
        "status": f.status,
        "sale_price_eur": f.sale_price_eur,
        "sell_fees_eur": f.sell_fees_eur,
        "sold_date": f.sold_date.isoformat() if f.sold_date else None,
        "note": f.note,
        "invested_eur": invested,
        "revenue_eur": revenue,
        "profit_eur": profit,
        "roi_pct": roi,
        "created_at": f.created_at.isoformat() if f.created_at else None,
    }


def _set_action(s: Session, ad_id: int, action: str) -> dict:
    """Сохраняет действие пользователя по объявлению (saved/ignored/dismissed)."""
    if s.get(Listing, ad_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    s.add(UserAction(listing_id=ad_id, action=action))
    s.commit()
    return {"ok": True}


def _public_settings() -> dict:
    """Настройки для страницы: значения из БД поверх дефолтов config.yaml."""
    from flipper.app.settings import get_settings, vinted_enabled_effective

    db_settings = get_settings()
    return {
        "exclude_keywords": db_settings.get("exclude_keywords")
        or cfg.scraper.exclude_keywords,
        "exclude_title_keywords": db_settings.get("exclude_title_keywords")
        or cfg.scraper.exclude_title_keywords,
        "vinted_enabled": vinted_enabled_effective(cfg.vinted),
        "vinted_default_catalog_ids": db_settings.get("vinted_default_catalog_ids")
        or cfg.vinted.default_catalog_ids,
        "vinted_domain": cfg.vinted.domain,
    }


def make_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="flipper", version="0.1.0", lifespan=lifespan)
    init_db(cfg.db.url)

    # CORS для Android APK (Capacitor WebView грузится с capacitor://localhost)
    # и для браузера с другого origin. Аутентификация — Bearer-токен, не куки,
    # поэтому `allow_credentials=False` и открытый список origin безопасны.
    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # --- Bearer-авторизация (один пользователь) ---
    bearer = HTTPBearer(auto_error=False)

    def require_key(cred: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if not cred or cred.credentials != cfg.api.api_key or not cfg.api.api_key:
            raise HTTPException(status_code=401, detail="invalid api key")

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/v1/listings")
    def listings(
        verdict: str | None = None,
        min_margin: float = 0.0,
        created_after_hours: float = 0.0,
        saved: bool = False,
        limit: int = 50,
        offset: int = 0,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Лента: анализы с verdict-фильтром, порогом маржи и «новые за N часов»."""
        from datetime import datetime, timedelta

        q = select(Analysis).order_by(Analysis.created_at.desc())
        if verdict:
            q = q.where(Analysis.verdict == verdict)
        if min_margin:
            q = q.where(Analysis.margin_eur >= min_margin)
        if created_after_hours > 0:
            cutoff = datetime.utcnow() - timedelta(hours=created_after_hours)
            q = q.where(Analysis.created_at >= cutoff)
        rows = s.execute(q.offset(offset).limit(limit)).scalars().all()
        # Грузим из listings ТОЛЬКО объявления текущей страницы.
        # Раньше здесь читалась вся таблица (64k+ строк) на каждый запрос —
        # из-за этого лента открывалась по несколько секунд.
        ids = [a.listing_id for a in rows]
        listings = (
            {
                l.ad_id: l
                for l in s.execute(
                    select(Listing).where(Listing.ad_id.in_(ids))
                ).scalars().all()
            }
            if ids
            else {}
        )
        # Последнее действие пользователя по этим объявлениям (для badges в ленте)
        actions: dict[int, str] = {}
        if ids:
            for ua in s.execute(
                select(UserAction)
                .where(UserAction.listing_id.in_(ids))
                .order_by(UserAction.at.desc())
            ).scalars().all():
                actions.setdefault(ua.listing_id, ua.action)  # первое (свежайшее) побеждает
        if saved:
            rows = [a for a in rows if actions.get(a.listing_id) == "saved"]
        return [
            {
                "ad_id": a.listing_id,
                "title": listings.get(a.listing_id).title if listings.get(a.listing_id) else "",
                "price_cents": listings.get(a.listing_id).price_cents if listings.get(a.listing_id) else 0,
                # Первое фото объявления — карточка в ленте
                "image_url": (
                    listings[a.listing_id].image_urls_json[0]
                    if listings.get(a.listing_id) and listings[a.listing_id].image_urls_json
                    else None
                ),
                "verdict": a.verdict,
                "margin_eur": a.margin_eur,
                "margin_pct": a.margin_pct,
                "confidence": a.confidence,
                "market_estimated": a.market_estimated,
                "location_city": (
                    listings[a.listing_id].location_city
                    if listings.get(a.listing_id) else None
                ),
                "saved": actions.get(a.listing_id) == "saved",
                "ignored": actions.get(a.listing_id) in ("ignored", "dismissed"),
                "source": listings[a.listing_id].source if listings.get(a.listing_id) else "kleinanzeigen",
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ]

    @app.get("/api/v1/listings/{ad_id}")
    def listing_detail(ad_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        listing = s.get(Listing, ad_id)
        if listing is None:
            raise HTTPException(status_code=404, detail="not found")
        analysis = s.execute(
            select(Analysis).where(Analysis.listing_id == ad_id).order_by(Analysis.created_at.desc())
        ).scalars().first()
        action = s.execute(
            select(UserAction).where(UserAction.listing_id == ad_id).order_by(UserAction.at.desc())
        ).scalars().first()
        flip = s.execute(
            select(Flip).where(Flip.listing_id == ad_id).order_by(Flip.created_at.desc())
        ).scalars().first()
        return {
            "ad_id": listing.ad_id,
            "title": listing.title,
            "source": listing.source,
            "price_cents": listing.price_cents,
            "description": listing.description,
            "url": listing.url,
            "location_city": listing.location_city,
            "image_urls": listing.image_urls_json,
            "created_at": listing.first_seen_at.isoformat() if listing.first_seen_at else None,
            "saved": action is not None and action.action == "saved",
            "ignored": action is not None and action.action in ("ignored", "dismissed"),
            "flip": _flip_dict(flip) if flip else None,
            "analysis": (
                {
                    "flip_type": analysis.flip_type,
                    "verdict": analysis.verdict,
                    "margin_eur": analysis.margin_eur,
                    "margin_pct": analysis.margin_pct,
                    "parts_cost_eur": analysis.parts_cost_eur,
                    "market_working_eur": analysis.market_working_eur,
                    "market_estimated": analysis.market_estimated,
                    "pros_ru": analysis.pros_ru_json,
                    "cons_ru": analysis.cons_ru_json,
                    "action_ru": analysis.action_ru,
                    "confidence": analysis.confidence,
                }
                if analysis
                else None
            ),
        }

    @app.post("/api/v1/listings/{ad_id}/saved")
    def save_listing(ad_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        return _set_action(s, ad_id, "saved")

    @app.post("/api/v1/listings/{ad_id}/ignored")
    def ignore_listing(ad_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        return _set_action(s, ad_id, "ignored")

    @app.post("/api/v1/listings/{ad_id}/dismissed")
    def dismiss_listing(ad_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        return _set_action(s, ad_id, "dismissed")

    # --- Портфель флиппера: личные сделки (купил → продал → профит) ---

    @app.get("/api/v1/flips")
    def list_flips(
        status: str | None = None,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Все сделки пользователя (свежие сверху), опц. фильтр по статусу."""
        q = select(Flip).order_by(Flip.created_at.desc())
        if status:
            q = q.where(Flip.status == status)
        return [_flip_dict(f) for f in s.execute(q).scalars().all()]

    @app.get("/api/v1/flips/summary")
    def flip_summary(_=Depends(require_key), s: Session = Depends(get_session)):
        """Сводка портфеля: вложено, выручка, профит, ROI, лучшая сделка."""
        rows = s.execute(select(Flip)).scalars().all()
        stock = [f for f in rows if f.status != FLIP_SOLD]
        sold = [f for f in rows if f.status == FLIP_SOLD and f.sale_price_eur is not None]
        stock_invested = sum((f.buy_price_eur or 0) + (f.extras_eur or 0) for f in stock)
        invested = sum((f.buy_price_eur or 0) + (f.extras_eur or 0) for f in sold)
        revenue = sum((f.sale_price_eur or 0) - (f.sell_fees_eur or 0) for f in sold)
        profit = round(revenue - invested, 2)
        roi = round(profit / invested * 100, 1) if invested > 0 else None
        profits = [
            (f.sale_price_eur or 0) - (f.sell_fees_eur or 0) - (f.buy_price_eur or 0) - (f.extras_eur or 0)
            for f in sold
        ]
        return {
            "total": len(rows),
            "stock_count": len(stock),
            "sold_count": len(sold),
            "stock_invested_eur": round(stock_invested, 2),
            "invested_eur": round(invested, 2),
            "revenue_eur": round(revenue, 2),
            "profit_eur": profit,
            "roi_pct": roi,
            "best_profit_eur": round(max(profits), 2) if profits else 0.0,
        }

    @app.post("/api/v1/flips")
    def create_flip(
        body: FlipBody,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Заводит сделку. Если указан listing_id — подтягивает заголовок/источник."""
        title = body.title
        source = body.source or "kleinanzeigen"
        if body.listing_id is not None:
            listing = s.get(Listing, body.listing_id)
            if listing is None:
                raise HTTPException(status_code=404, detail="listing not found")
            title = title or listing.title
            source = body.source or listing.source
        buy_date = _parse_date(body.buy_date)
        flip = Flip(
            listing_id=body.listing_id,
            title=title,
            source=source,
            buy_price_eur=body.buy_price_eur,
            extras_eur=body.extras_eur,
            note=body.note,
        )
        if buy_date is not None:
            flip.buy_date = buy_date
        s.add(flip)
        s.commit()
        s.refresh(flip)
        return _flip_dict(flip)

    @app.put("/api/v1/flips/{flip_id}")
    def update_flip(
        flip_id: int,
        body: FlipUpdate,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Правки сделки. При переводе в «продано» ставит дату, если не задана."""
        flip = s.get(Flip, flip_id)
        if flip is None:
            raise HTTPException(status_code=404, detail="not found")
        data = body.model_dump(exclude_unset=True)
        if "sold_date" in data:
            flip.sold_date = _parse_date(data.pop("sold_date"))
        for key, value in data.items():
            if key == "status" and value not in (FLIP_STOCK, FLIP_SOLD):
                raise HTTPException(status_code=422, detail="bad status")
            setattr(flip, key, value)
        if flip.status == FLIP_SOLD and flip.sold_date is None:
            from datetime import datetime

            flip.sold_date = datetime.utcnow()
        s.commit()
        s.refresh(flip)
        return _flip_dict(flip)

    @app.delete("/api/v1/flips/{flip_id}")
    def delete_flip(
        flip_id: int,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Удаляет сделку из портфеля."""
        flip = s.get(Flip, flip_id)
        if flip is None:
            raise HTTPException(status_code=404, detail="not found")
        s.delete(flip)
        s.commit()
        return {"ok": True}

    @app.post("/api/v1/notifications/register")
    def register_device(
        body: RegisterBody,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Телефон шлёт свой FCM-токен при старте. Повторный post обновляет platform."""
        s.merge(DeviceToken(token=body.fcm_token, platform=body.platform))
        s.commit()
        return {"ok": True}

    @app.post("/api/v1/notifications/test")
    def test_push(_=Depends(require_key)):
        """Тестовое уведомление на все зарегистрированные устройства."""
        from flipper.app.notify.fcm import send_to_all

        sent = send_to_all()
        return {"sent": sent}

    @app.get("/api/v1/notifications/tokens")
    def list_tokens(_=Depends(require_key), s: Session = Depends(get_session)):
        """Для отладки: сколько устройств получают пуши."""
        rows = s.execute(select(DeviceToken)).scalars().all()
        return [{"token": t.token, "platform": t.platform} for t in rows]

    @app.get("/api/v1/push/keys")
    def push_keys(_=Depends(require_key)):
        """Публичный VAPID-ключ для подписки в браузере."""
        from flipper.app.notify.push import get_vapid_keys

        _, public_key = get_vapid_keys()
        return {"public_key": public_key}

    @app.post("/api/v1/push/subscribe")
    def push_subscribe(
        body: SubscribeBody,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Сохранить Web Push подписку (заменяет при изменении настроек)."""
        existing = s.query(PushSubscription).filter(
            PushSubscription.endpoint == body.endpoint
        ).first()
        if existing:
            existing.send_maybe = body.send_maybe
            existing.p256dh = body.p256dh
            existing.auth = body.auth
        else:
            s.add(
                PushSubscription(
                    endpoint=body.endpoint,
                    p256dh=body.p256dh,
                    auth=body.auth,
                    send_maybe=body.send_maybe,
                )
            )
        s.commit()
        return {"ok": True}

    @app.delete("/api/v1/push/unsubscribe")
    def push_unsubscribe(
        endpoint: str,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        s.query(PushSubscription).filter(PushSubscription.endpoint == endpoint).delete()
        s.commit()
        return {"ok": True}

    @app.get("/api/v1/export.csv")
    def export_csv(
        verdict: str | None = None,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """CSV всех анализов (для учёта сделок в Excel/Google Sheets)."""
        import csv
        import io

        q = (
            select(Analysis)
            .join(Listing, Analysis.listing_id == Listing.ad_id)
            .order_by(Analysis.created_at.desc())
        )
        if verdict:
            q = q.where(Analysis.verdict == verdict)
        rows = s.execute(q).scalars().all()
        listings = {
            l.ad_id: l
            for l in s.execute(select(Listing)).scalars().all()
        }
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(
            ["дата", "id", "название", "источник", "вердикт", "тип", "маржа_eur", "маржа_pct",
             "цена_eur", "рынок_eur", "запчасти_eur", "город", "url"]
        )
        for a in rows:
            l = listings.get(a.listing_id)
            w.writerow(
                [
                    a.created_at.strftime("%Y-%m-%d") if a.created_at else "",
                    a.listing_id,
                    l.title if l else "",
                    l.source if l else "kleinanzeigen",
                    a.verdict,
                    a.flip_type,
                    f"{a.margin_eur:.2f}",
                    f"{a.margin_pct:.1f}",
                    f"{(l.price_cents / 100) if l else '':.2f}",
                    f"{a.market_working_eur:.0f}" if a.market_working_eur else "",
                    f"{a.parts_cost_eur:.2f}",
                    l.location_city if l else "",
                    l.url if l else "",
                ]
            )
        return Response(
            content=buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="flipper.csv"'},
        )

    @app.get("/api/v1/stats")
    def stats(_=Depends(require_key), s: Session = Depends(get_session)):
        """Сводка для футера/статуса приложения."""
        total = s.query(Listing).count()
        by = {
            v: s.query(Analysis).filter(Analysis.verdict == v).count()
            for v in ("TAKE", "MAYBE", "SKIP")
        }
        return {"total_listings": total, "verdicts": by}

    @app.post("/api/v1/analyze/{ad_id}")
    def reanalyze(ad_id: int, _=Depends(require_key)):
        """Ручной (пере)анализ объявления — cooldown соблюдается. Пушей не шлёт."""
        from flipper.app.analyzer.factory import make_provider
        from flipper.app.pipeline import run_on_listing
        from flipper.app.scraper.client import ScraperClient

        from flipper.app.cooldown import in_cooldown
        if in_cooldown():
            raise HTTPException(status_code=429, detail="cooldown active")

        scraper = ScraperClient(
            delay_range=(0.1, 0.2),
            max_requests=cfg.scraper.max_requests_per_cycle,
        )
        provider = make_provider(cfg.analyzer)
        # Лоты с Vinted считаем рынок на Vinted (иначе KK)
        vinted_client = None
        if vinted_client is None and cfg.vinted.enabled:
            from flipper.app.db import get_session as _gs
            from flipper.app.scraper.vinted import VintedClient
            with _gs() as _s:
                list_row = _s.get(Listing, ad_id)
            if list_row is not None and getattr(list_row, "source", None) == "vinted":
                vinted_client = VintedClient(
                    cfg.vinted,
                    max_requests=cfg.vinted.max_requests_per_cycle,
                    max_requests_per_hour=cfg.vinted.max_requests_per_hour,
                )
        analysis = run_on_listing(ad_id, provider, scraper, cfg.deal, notify=False, vinted_client=vinted_client)
        if analysis is None:
            raise HTTPException(status_code=404, detail="not found")
        return {
            "ad_id": analysis.listing_id,
            "verdict": analysis.verdict,
            "margin_eur": analysis.margin_eur,
            "margin_pct": analysis.margin_pct,
            "confidence": analysis.confidence,
            "action_ru": analysis.action_ru,
        }

    @app.get("/api/v1/searches")
    def list_searches(_=Depends(require_key), s: Session = Depends(get_session)):
        rows = s.execute(select(Search).order_by(Search.id)).scalars().all()
        return [{
            "id": r.id,
            "source": r.source,
            "query": r.query,
            "filters": r.filters_json or {},
            "active": bool(r.active),
        } for r in rows]

    @app.post("/api/v1/searches")
    def create_search(body: SearchBody, _=Depends(require_key), s: Session = Depends(get_session)):
        r = Search(
            source=body.source,
            query=body.query,
            filters_json={
                "max_price_eur": body.max_price_eur,
                "radius_km": body.radius_km,
                "location": body.location,
                "sort_price": body.sort_price,
                "catalog_ids": body.catalog_ids,
                "brand_ids": body.brand_ids,
                "status_ids": body.status_ids,
                "order": body.order,
            },
            active=body.active,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return {"id": r.id, "source": r.source, "query": r.query, "active": r.active}

    @app.put("/api/v1/searches/{search_id}")
    def update_search(search_id: int, body: SearchBody, _=Depends(require_key), s: Session = Depends(get_session)):
        r = s.get(Search, search_id)
        if r is None:
            raise HTTPException(status_code=404, detail="not found")
        r.source = body.source
        r.query = body.query
        r.active = body.active
        r.filters_json = {
            "max_price_eur": body.max_price_eur,
            "radius_km": body.radius_km,
            "location": body.location,
            "sort_price": body.sort_price,
            "catalog_ids": body.catalog_ids,
            "brand_ids": body.brand_ids,
            "status_ids": body.status_ids,
            "order": body.order,
        }
        s.commit()
        return {"ok": True}

    @app.delete("/api/v1/searches/{search_id}")
    def delete_search(search_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        r = s.get(Search, search_id)
        if r is None:
            raise HTTPException(status_code=404, detail="not found")
        s.delete(r)
        s.commit()
        return {"ok": True}

    @app.get("/api/v1/settings")
    def get_settings(_=Depends(require_key)):
        return _public_settings()

    @app.put("/api/v1/settings")
    def put_settings(body: SettingsBody, _=Depends(require_key)):
        from flipper.app.settings import update_settings

        patch = body.model_dump(exclude_none=True)
        if patch:
            update_settings(patch)
        return _public_settings()

    @app.get("/api/v1/vinted/catalogs")
    def vinted_catalogs(_=Depends(require_key)):
        from flipper.app.scraper.vinted import VINTED_CATALOGS

        return [{"id": cid, "title": title} for cid, title in VINTED_CATALOGS.items()]

    _dist = Path(__file__).resolve().parents[3].parent / "frontend" / "dist"
    if _dist.exists():
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles

        _assets = _dist / "assets"
        if _assets.exists():
            app.mount("/assets", StaticFiles(directory=_assets), name="assets")

        @app.get("/")
        def spa_root():
            return FileResponse(_dist / "index.html")

        @app.get("/{full_path:path}")
        def spa(full_path: str):
            candidate = (_dist / full_path).resolve()
            if _dist in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(_dist / "index.html")

    return app


app = make_app()