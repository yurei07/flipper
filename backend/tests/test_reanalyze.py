"""Тесты отбора кандидатов пере-анализа: старые SKIP, активные, маржа сверху."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing, STATUS_ACTIVE


@pytest.fixture(autouse=True)
def _db():
    init_db("sqlite:///:memory:")
    yield


def _listing(ad_id: int, status: str = STATUS_ACTIVE) -> Listing:
    return Listing(ad_id=ad_id, title=f"item {ad_id}", status=status)


def test_reanalyze_picks_old_active_skips_by_margin(monkeypatch, capsys):
    from flipper.cli import cmd_reanalyze

    old = datetime.utcnow() - timedelta(hours=48)
    with get_session() as s:
        s.add_all(
            [
                _listing(1),
                _listing(2),
                _listing(3),
                _listing(4, status="added"),
                _listing(5),
            ]
        )
        s.commit()
        # старые SKIP: 2 (маржа 40) и 1 (маржа 10) — оба в выборку при limit=2
        s.add_all(
            [
                Analysis(listing_id=2, verdict="SKIP", margin_eur=40, created_at=old),
                Analysis(listing_id=1, verdict="SKIP", margin_eur=10, created_at=old),
                # свежий SKIP — не трогаем
                Analysis(listing_id=3, verdict="SKIP", margin_eur=99),
                # неактивное объявление — не трогаем
                Analysis(listing_id=4, verdict="SKIP", margin_eur=50, created_at=old),
                # TAKE не трогаем
                Analysis(listing_id=5, verdict="TAKE", margin_eur=60, created_at=old),
            ]
        )
        s.commit()

    cmd_reanalyze(limit=2, hours=24)

    out = capsys.readouterr().out
    assert "2" in out and "1" in out and "3" not in out and "4" not in out and "5" not in out

    with get_session() as s:
        rows = s.query(Analysis).all()
        assert len(rows) == 3  # 2 и 1 удалены, остальные на месте
        assert sorted(r.listing_id for r in rows) == [3, 4, 5]


def test_reanalyze_no_candidates(capsys):
    from flipper.cli import cmd_reanalyze

    with get_session() as s:
        s.add(_listing(1))
        s.add(Analysis(listing_id=1, verdict="SKIP", margin_eur=-5))
        s.commit()

    cmd_reanalyze(limit=5, hours=24)
    out = capsys.readouterr().out
    assert "нет" in out