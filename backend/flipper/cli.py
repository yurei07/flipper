"""CLI: python -m flipper run --once | analyze <ad_id> | status.

Phase A главное: run --once = скан -> анализ -> человекочитаемые вердикты.
Все команды уважают single-instance lock и cooldown — не мастурбируют API вручную.
"""
from __future__ import annotations

import argparse
import logging
from sqlalchemy import func, select

from flipper.app.analyzer.ollama import OllamaProvider
from flipper.app.db import get_session, init_db
from flipper.app.models import Analysis, CycleRun, Listing, STATUS_ACTIVE
from flipper.app.scheduler import run_cycle
from flipper.app.pipeline import run_on_listing
from flipper.app.scraper.client import ScraperClient
from flipper.app.cooldown import clear_cooldown, in_cooldown
from flipper.config import load_config


def _print_deal(a: Analysis) -> None:
    """Человекочитаемая строка вердикта, как в брифе §13.6."""
    icon = {"TAKE": "🟢", "MAYBE": "🟡", "SKIP": "⚪"}.get(a.verdict, "?")
    flip = "ремонт" if a.flip_type == "repair" else "перепродажа"
    print(
        f"{icon} {a.verdict} +{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%) |"
        f" {flip} | {a.listing_id} | конфиденция {a.confidence:.2f}"
    )


def cmd_run_once() -> None:
    """Один полный цикл через run_cycle (с блокировкой и cooldown)."""
    run_cycle()  # весь пайплайн внутри scheduler.run_cycle


def cmd_analyze_one(ad_id: int) -> None:
    """Ручной прогон анализа без скана (не пишет в API поисками)."""
    cfg = load_config()
    provider = OllamaProvider(
        base_url=cfg.analyzer.ollama_base_url,
        model=cfg.analyzer.model,
        temperature=cfg.analyzer.temperature,
        timeout_sec=cfg.analyzer.timeout_sec,
        retries=cfg.analyzer.retries,
    )
    # Ручной анализ тоже уважает cooldown (не ходим к рынку во время бана)
    if in_cooldown():
        print("cooldown активен — анализ пропущен")
        return
    client = ScraperClient(
        delay_range=(0.1, 0.2), max_requests=cfg.scraper.max_requests_per_cycle
    )
    a = run_on_listing(ad_id, provider, client, cfg.deal, notify=False)
    if a is None:
        print(f"листинг {ad_id} не найден")
    else:
        _print_deal(a)


def cmd_test_notify() -> None:
    """Проверка Telegram-канала: шлём тестовое сообщение в настроенный чат."""
    from flipper.app.notify.telegram import _configured, send_message

    if _configured() is None:
        print(
            "Telegram не настроен. Задай в .env:\n"
            "  FLIPPER_TELEGRAM_BOT_TOKEN=<токен от @BotFather>\n"
            "  FLIPPER_TELEGRAM_CHAT_ID=<твой chat_id>"
        )
        return
    ok = send_message("✅ flipper: тестовое уведомление — связь есть")
    print("отправлено, проверь чат" if ok else "не удалось отправить (смотри лог)")


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


def cmd_recent(n: int = 15) -> None:
    """Последние найденные объявления: город, цена, вердикт, маржа, ссылка."""
    with get_session() as s:
        listings = (
            s.execute(select(Listing).order_by(Listing.last_seen_at.desc()).limit(n))
            .scalars()
            .all()
        )
        if not listings:
            print("объявлений пока нет")
            return
        latest: dict[int, Analysis] = {}
        for row in s.execute(
            select(Analysis)
            .order_by(Analysis.listing_id, Analysis.created_at.desc())
        ).scalars().all():
            latest.setdefault(row.listing_id, row)
    for l in listings:
        a = latest.get(l.ad_id)
        v = a.verdict if a else "-"
        m = f"+{a.margin_eur:.0f}€ ({a.margin_pct:.0f}%)" if a else "-"
        city = l.location_city or "?"
        print(
            f"{l.last_seen_at:%H:%M} {city:<18} {l.price_cents / 100:>6.0f}€"
            f"  {v:<5} {m:<16} {l.ad_id}  {l.url}"
        )


def cmd_cycles(n: int = 10) -> None:
    """Последние N циклов сканирования (мониторинг 24ч-прогона)."""
    with get_session() as s:
        rows = (
            s.execute(select(CycleRun).order_by(CycleRun.id.desc()).limit(n))
            .scalars()
            .all()
        )
    if not rows:
        print("циклов пока не было")
        return
    print(f"{'id':>3} {'итог':<9} {'новых':>5} {'анализ':>7} {'запросов':>8}  старт")
    for r in reversed(rows):
        print(
            f"{r.id:>3} {r.outcome:<9} {r.new_listings:>5} {r.analyses:>7} "
            f"{r.requests_used:>8}  {r.started_at:%H:%M} {str(r.error)[:40]}"
        )


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
    uncool = sub.add_parser("uncooldown", help="сбросить cooldown вручную")
    cyc = sub.add_parser("cycles")
    cyc.add_argument("-n", type=int, default=10, help="сколько последних циклов показать")
    rec = sub.add_parser("recent")
    rec.add_argument("-n", type=int, default=15, help="сколько объявлений показать")
    sub.add_parser("test-notify", help="проверить Telegram-уведомления")
    args = p.parse_args()

    cfg = load_config()
    init_db(cfg.db.url)

    if args.cmd == "status":
        cmd_status(cfg)
    elif args.cmd == "run":
        if not args.once:
            p.error("run требует --once")
        cmd_run_once()
    elif args.cmd == "analyze":
        cmd_analyze_one(args.ad_id)
    elif args.cmd == "uncooldown":
        clear_cooldown()
        print("cooldown сброшен")
    elif args.cmd == "cycles":
        cmd_cycles(args.n)
    elif args.cmd == "recent":
        cmd_recent(args.n)
    elif args.cmd == "test-notify":
        cmd_test_notify()


if __name__ == "__main__":
    main()