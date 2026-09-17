"""Движок БД, сессии и инициализация схемы."""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from flipper.app.models import Base

# Ленивый singleton — engine создаётся при первом обращении (по конфигу)
_engine = None
_SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str) -> None:
    """Создаёт engine и таблицы (для старта без alembic — достаточно)."""
    global _engine, _SessionLocal
    _engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine, autoflush=False)


def get_session() -> Session:
    """Короткоживущая сессия для одного запроса."""
    if _SessionLocal is None:
        raise RuntimeError("init_db() не вызывался")
    return _SessionLocal()