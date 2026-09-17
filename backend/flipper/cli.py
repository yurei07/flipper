"""CLI: python -m flipper run --once | analyze <ad_id> | status.

Phase A самое главное: run --once = новый скан -> анализ -> человекочитаемые вердикты.
"""
from __future__ import annotations

import argparse
import logging
from sqlalchemy import func, select

from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, Listing, STATUS_ACTIVE
from flipper.app.pipeline import analyze_pending, run_on_listing
from flipper.app.scraper.client import ScraperClient
from flipper.app.scraper.detail import enrich_new
from flipper.app.scraper.search import scan_one
from flipper.config import load_config
from flipper.app.analyzer.ollama import OllamaProvider


def _print_deal(a: Analysis) -> None:
    """Человекочитаемая строка вердикта, как в брифе §13.6."""
    icon = {"TAKE": "🟢", "MAYBE": "🟡", "SKIP": "⚪"}.get(a.verdict, "?")
    flip = "ремонт" if a.flip_type == "repair" else "перепродажа"
    print(
        f"{icon} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%) |"
        f" {flip} | {a.listing_id} | конфиденция {a.confidence:.2f}"
    )


def cmd_run_once(cfg, provider) -> int:
    """Один полный цикл: скрейп -> детали -> анализ -> вывод вердиктов."""
    client = ScraperClient(delay_range=cfg.scraper.detail_delay_sec)
    new = scan_one(cfg.scraper, client)
    enriched = enrich_new(client)
    print(f"скан: {new} новых, {enriched} с деталями")

    analyses = analyze_pending(provider, client, cfg.deal, limit=20)
    print(f"проанализировано: {len(analyses)}")
    for a in analyses:
        _print_deal(a)
    return len(analyses)


def cmd_analyze_one(cfg, provider, ad_id: int) -> None:
    """Ручной прогон: python -m flipper analyze <ad_id>."""
    client = ScraperClient(delay_range=(0.1, 0.2))
    a = run_on_listing(ad_id, provider, client, cfg.deal)
    if a is None:
        print(f"листинг {ad_id} не найден")
    else:
        _print_deal(a)


def cmd_status(cfg) -> None:
    """Короткая статистика по БД."""
    with get_session() as s:
        listings = s.execute(select(func.count()).select_from(Listing)).scalar()
        active = s.execute(
            select(func.count()).select_from(Listing).where(Listing.status == STATUS_ACTIVE)
        ).scalar()
        analyses = s.execute(select(func.count()).select_from(Analysis)).scalar()
        by_verdict = dict(
            s.execute(
                select(Analysis.verdict, func.count()).group_by(Analysis.verdict)
            ).all()
        )
    print(f"объявлений: {listings} (активных {active}), анализов: {analyses}")
    print("вердикты:", ", ".join(f"{k}={v}" for k, v in by_verdict.items()))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    p = argparse.ArgumentParser(prog="flipper", description="Kleinanzeigen deal scanner")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    once = sub.add_parser("run")
    once.add_argument("--once", action="store_true", help="один проход скана+анализа")
    ana = sub.add_parser("analyze")
    ana.add_argument("ad_id", type=int, help="номер объявления")
    args = p.parse_args()

    cfg = load_config()
    init_db(cfg.db.url)
    provider = OllamaProvider(
        base_url=cfg.analyzer.ollama_base_url,
        model=cfg.analyzer.model,
        temperature=cfg.analyzer.temperature,
        timeout_sec=cfg.analyzer.timeout_sec,
        retries=cfg.analyzer.retries,
    )

    if args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "run":
        cmd_run_once(cfg, provider)
    elif args.cmd == "analyze":
        cmd_analyze_one(cfg, provider, args.ad_id)


if __name__ == "__main__":
    main()