"""FastAPI-приложение: health + базовые REST-роуты (§8, базовый минимум для Phase A)."""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from flipper.app.db import get_session
from flipper.app.models import Analysis, DeviceToken, Listing, UserAction
from flipper.config import Config, load_config
from flipper.app.db import init_db

cfg: Config = load_config()


class RegisterBody(BaseModel):
    """Тело регистрации FCM-токена телефона (§8)."""

    fcm_token: str = Field(min_length=10, max_length=4096)
    platform: str = "android"


def make_app() -> FastAPI:
    app = FastAPI(title="flipper", version="0.1.0")
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
        limit: int = 50,
        offset: int = 0,
        _=Depends(require_key),
        s: Session = Depends(get_session),
    ):
        """Лента: анализы с verdict-фильтром и порогом маржи."""
        q = select(Analysis).order_by(Analysis.created_at.desc())
        if verdict:
            q = q.where(Analysis.verdict == verdict)
        if min_margin:
            q = q.where(Analysis.margin_eur >= min_margin)
        rows = s.execute(q.offset(offset).limit(limit)).scalars().all()
        titles = {
            l.ad_id: l.title
            for l in s.execute(select(Listing)).scalars().all()
        }
        return [
            {
                "ad_id": a.listing_id,
                "title": titles.get(a.listing_id, ""),
                "verdict": a.verdict,
                "margin_eur": a.margin_eur,
                "margin_pct": a.margin_pct,
                "confidence": a.confidence,
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
        return {
            "ad_id": listing.ad_id,
            "title": listing.title,
            "price_cents": listing.price_cents,
            "description": listing.description,
            "url": listing.url,
            "location_city": listing.location_city,
            "analysis": (
                {
                    "flip_type": analysis.flip_type,
                    "verdict": analysis.verdict,
                    "margin_eur": analysis.margin_eur,
                    "margin_pct": analysis.margin_pct,
                    "parts_cost_eur": analysis.parts_cost_eur,
                    "market_working_eur": analysis.market_working_eur,
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
        if s.get(Listing, ad_id) is None:
            raise HTTPException(status_code=404, detail="not found")
        s.add(UserAction(listing_id=ad_id, action="saved"))
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

    @app.get("/api/v1/notifications/tokens")
    def list_tokens(_=Depends(require_key), s: Session = Depends(get_session)):
        """Для отладки: сколько устройств получают пуши."""
        rows = s.execute(select(DeviceToken)).scalars().all()
        return [{"token": t.token, "platform": t.platform} for t in rows]

    return app


app = make_app()