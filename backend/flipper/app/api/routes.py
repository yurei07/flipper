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
from flipper.app.models import Analysis, DeviceToken, Listing, PushSubscription, Search, UserAction
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
    """Тело создания/обновления поиска (управление из приложения)."""

    query: str = Field(min_length=1, max_length=200)
    max_price_eur: float | None = None
    radius_km: int | None = None
    location: str | None = None
    sort_price: str | None = None
    active: bool = True


def _set_action(s: Session, ad_id: int, action: str) -> dict:
    """Сохраняет действие пользователя по объявлению (saved/ignored/dismissed)."""
    if s.get(Listing, ad_id) is None:
        raise HTTPException(status_code=404, detail="not found")
    s.add(UserAction(listing_id=ad_id, action=action))
    s.commit()
    return {"ok": True}


def make_app(lifespan=None) -> FastAPI:
    app = FastAPI(title="flipper", version="0.1.0", lifespan=lifespan)
    init_db(cfg.db.url)

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
        listings = {
            l.ad_id: l
            for l in s.execute(select(Listing)).scalars().all()
        }
        # Последнее действие пользователя по объявлению (для badges в ленте)
        actions: dict[int, str] = {}
        for ua in s.execute(
            select(UserAction).order_by(UserAction.at.desc())
        ).scalars().all():
            actions.setdefault(ua.listing_id, ua.action)  # первое (свежайшее) побеждает
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
        return {
            "ad_id": listing.ad_id,
            "title": listing.title,
            "price_cents": listing.price_cents,
            "description": listing.description,
            "url": listing.url,
            "location_city": listing.location_city,
            "image_urls": listing.image_urls_json,
            "created_at": listing.first_seen_at.isoformat() if listing.first_seen_at else None,
            "saved": action is not None and action.action == "saved",
            "ignored": action is not None and action.action in ("ignored", "dismissed"),
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
            ["дата", "id", "название", "вердикт", "тип", "маржа_eur", "маржа_pct",
             "цена_eur", "рынок_eur", "запчасти_eur", "город", "url"]
        )
        for a in rows:
            l = listings.get(a.listing_id)
            w.writerow(
                [
                    a.created_at.strftime("%Y-%m-%d") if a.created_at else "",
                    a.listing_id,
                    l.title if l else "",
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
        from flipper.app.analyzer.ollama import OllamaProvider
        from flipper.app.pipeline import run_on_listing
        from flipper.app.scraper.client import ScraperClient

        if not cfg.api.api_key:
            pass  # auth уже пройден через bearer
        from flipper.app.cooldown import in_cooldown
        if in_cooldown():
            raise HTTPException(status_code=429, detail="cooldown active")

        scraper = ScraperClient(
            delay_range=(0.1, 0.2),
            max_requests=cfg.scraper.max_requests_per_cycle,
        )
        provider = OllamaProvider(
            base_url=cfg.analyzer.ollama_base_url,
            model=cfg.analyzer.model,
            temperature=cfg.analyzer.temperature,
            timeout_sec=cfg.analyzer.timeout_sec,
            retries=cfg.analyzer.retries,
        )
        analysis = run_on_listing(ad_id, provider, scraper, cfg.deal, notify=False)
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
            "query": r.query,
            "filters": r.filters_json,
            "active": r.active,
        } for r in rows]

    @app.post("/api/v1/searches")
    def create_search(body: SearchBody, _=Depends(require_key), s: Session = Depends(get_session)):
        r = Search(
            query=body.query,
            filters_json={
                "max_price_eur": body.max_price_eur,
                "radius_km": body.radius_km,
                "location": body.location,
                "sort_price": body.sort_price,
            },
            active=body.active,
        )
        s.add(r)
        s.commit()
        s.refresh(r)
        return {"id": r.id, "query": r.query, "active": r.active}

    @app.delete("/api/v1/searches/{search_id}")
    def delete_search(search_id: int, _=Depends(require_key), s: Session = Depends(get_session)):
        r = s.get(Search, search_id)
        if r is None:
            raise HTTPException(status_code=404, detail="not found")
        s.delete(r)
        s.commit()
        return {"ok": True}

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