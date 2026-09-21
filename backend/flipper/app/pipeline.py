"""Полный пайплайн: listing -> LLM -> реальная рыночная цена -> вердикт -> БД."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select

from flipper.app.analyzer.analyze import analyze_listing
from flipper.app.analyzer.base import Provider
from flipper.app.analyzer.schema import AnalysisResult, FlipType, Repairability, Verdict
from flipper.app.db import get_session
from flipper.app.models import Analysis, Listing
from flipper.app.notify.fcm import maybe_notify
from flipper.app.notify.telegram import notify_telegram
from flipper.app.notify.push import notify_push
from flipper.app.pricing.market import market_price_eur, model_key
from flipper.app.pricing.parts import get_parts_cost
from flipper.app.scraper.client import ScraperClient, ScraperBlocked
from flipper.config import DealConfig

log = logging.getLogger(__name__)

# Уникальные маркеры того, что взять ТОЧНО нельзя (кража/вода/битая плата)
_HARD_FLAG_TERMS = (
    "iclocked", "icl", "frp", "аккаунт", "учетн", "крад",
    "wasserschaden", "вод", "залит", "korrosion",
    "platine gebrochen", "тресну", "плат" " слома",
)


def _hard_flags_hit(res: AnalysisResult) -> list[str]:
    """Жёсткие флаги -> список причин (пусто = ок)."""
    hits: list[str] = []
    if res.fault and (res.fault.water_damage or res.fault.locked):
        hits.append("water/locked в fault")
    text = " ".join(res.flag_reasons_ru).lower()
    text = "".join(text.split())  # убираем пробелы для надёжного поиска
    for term in _HARD_FLAG_TERMS:
        if term in text:
            hits.append(term)
    return hits


# Маркеры дефекта в заголовке/описании: если они есть — сделка это ремонт (repair),
# даже если модель ошибочно решила, что устройство «рабочее» и его можно просто
# продать. Модель путает «чинить» с «перепродать» — здесь это чиним детерминированно.
_DEFECT_MARKERS = (
    "defekt", "kaputt", "bastler", "reparatur", "reparieren", "repararbedürftig",
    "wasserschaden", "kein bild", "keine funktion", "ohne funktion", "ohne bild",
    "nicht eingeschaltet", "startet nicht", "eingeschaltet", "ersatzteil",
    "as-is", "as is", "for parts", "not working", "broken", "nonfunctional",
    "zum schlachten", "teileträger", "nur als bastelprojekt", "display defekt",
)


def _normalize_flip(res: AnalysisResult, listing) -> None:
    """Заставляет flip_type совпадать с фактом: есть дефект -> repair, нет -> resale.

    Мутирует res: ставь flip_type, чистит fault/repairability для resale, добирает
    ремонтные данные, если модели их не дала.
    """
    combined = f"{listing.title} {listing.description or ''}".lower()
    defect = any(m in combined for m in _DEFECT_MARKERS)
    has_fault = bool(res.fault and (res.fault.summary_ru or res.fault.water_damage or res.fault.locked))

    if (defect or has_fault) and res.flip_type.value != "repair":
        res.flip_type = FlipType.repair
        log.info("дефект в лоте %s — перевод в repair", listing.ad_id)
        if res.repairability is None:
            res.repairability = Repairability(
                score=3,
                home_repairable=True,
                parts_cost_eur=float(res.deal.repair_cost_eur or 0),
                what_needs_fixing_ru=(
                    f"требуется ремонт: {res.fault.summary_ru}" if res.fault else "недостаточно данных"
                ),
                explanation_ru="модель не описала ремонт — затраты оценочные",
            )
    elif res.flip_type.value == "repair" and not defect and not has_fault:
        res.flip_type = FlipType.resale
        res.fault = None
        res.repairability = None
        log.info("дефекта в лоте %s нет — перевод в resale", listing.ad_id)


def recompute_verdict(
    res: AnalysisResult, deal: DealConfig
) -> tuple[float, float, Verdict]:
    """Пересчёт маржи по РЕАЛЬНОЙ market, потом вердикт по порогам из §6."""
    price_ask = res.deal.price_ask_eur
    market = res.deal.market_working_eur
    flip = res.flip_type.value

    # Реальная стоимость вложений: либо ремонт, либо логистика перепродажи
    if flip == "resale":
        cost = deal.logistics_eur
        margin_eur = market - price_ask - cost
        take_eur, take_pct = deal.min_margin_eur_resale, deal.min_margin_pct_resale
        maybe_eur = 25.0
    else:
        cost = res.repairability.parts_cost_eur if res.repairability else 0.0
        margin_eur = market - price_ask - cost
        take_eur, take_pct = deal.min_margin_eur, deal.min_margin_pct
        maybe_eur = 15.0

    margin_pct = (margin_eur / price_ask * 100) if price_ask > 0 else 0.0

    if margin_eur >= take_eur and margin_pct >= take_pct:
        verdict = Verdict.TAKE
    elif margin_eur >= maybe_eur:
        verdict = Verdict.MAYBE
    else:
        verdict = Verdict.SKIP
    return margin_eur, margin_pct, verdict


def run_on_listing(
    listing_id: int,
    provider: Provider,
    scraper: ScraperClient,
    deal_cfg: DealConfig,
    notify: bool = True,
) -> Analysis | None:
    """Анализ + ценовой модуль, запись в analyses. Возвращает SQLAlchemy-объект."""
    with get_session() as s:
        listing = s.get(Listing, listing_id)
        if listing is None:
            log.warning("листинг %s не найден", listing_id)
            return None
        res = analyze_listing(listing, provider)
        _normalize_flip(res, listing)

        # --- §7: реальная рыночная цена (перезаписываем модель) ---
        key = model_key(res.device.model_dump())
        real = market_price_eur(key, scraper, deal_cfg)
        estimated = real[1]
        if real[0] is not None:
            res.deal.market_working_eur = real[0]
        log.info(
            "реальный рынок %s: %.0f EUR%s",
            key,
            res.deal.market_working_eur,
            " (estimate)" if real[1] else "",
        )

        # Пересчитываем маржу/вердикт от реального рынка, не от модели
        margin_eur, margin_pct, verdict = recompute_verdict(res, deal_cfg)
        res.deal.margin_eur = margin_eur
        res.deal.margin_pct = margin_pct

        # Части для resale не нужны — ноль
        parts_cost = 0.0 if res.flip_type.value == "resale" else (
            (res.repairability.parts_cost_eur if res.repairability else 0.0)
        )
        parts_cost = get_parts_cost(key, parts_cost) if parts_cost else parts_cost

        # Понижаем вердикт ТОЛЬКО за жёсткие флаги (кража/вода/битая плата),
        # а не за рассуждения модели вроде «низкая цена» — они уже в марже
        hard_flags = _hard_flags_hit(res)
        if hard_flags:
            verdict = Verdict.SKIP
            log.info("жёсткие флаги: %s — вердикт SKIP", hard_flags)

        # Рынок оценочный (мало объявлений-аналогов) — не верим красивой марже:
        # богатая цифра не дороже MAYBE, пока её не подтвердит реальная выборка.
        if estimated and verdict == Verdict.TAKE:
            log.info("рынок оценочный (лотов-аналогов мало) — TAKE понижен до MAYBE")
            verdict = Verdict.MAYBE

        analysis = Analysis(
            listing_id=listing.ad_id,
            flip_type=res.flip_type.value,
            device_json=res.device.model_dump(),
            fault_json=(res.fault.model_dump() if res.fault else None),
            repairability_json=(res.repairability.model_dump() if res.repairability else None),
            parts_cost_eur=parts_cost,
            market_working_eur=res.deal.market_working_eur,
            market_estimated=estimated,
            margin_eur=margin_eur,
            margin_pct=margin_pct,
            verdict=verdict.value,
            confidence=res.deal.confidence,
            pros_ru_json=res.deal.pros_ru,
            cons_ru_json=res.deal.cons_ru,
            action_ru=res.deal.action_ru,
            model_used=getattr(provider, "model", "?"),
            raw_json=res.model_dump(),
        )
        s.add(analysis)
        s.commit()
        s.refresh(analysis)
        # Push после успешного сохранения (новый TAKE заслуживает пуш по §9)
        if notify:
            maybe_notify(analysis, deal_cfg)       # FCM (если настроен)
            notify_telegram(analysis, deal_cfg)    # Telegram (если настроен)
            notify_push(analysis, deal_cfg)        # Web Push PWA (подписки)
        return analysis


def prune_stale_maybes(days: float, limit: int = 2) -> int:
    """Удаляет залежавшиеся MAYBE-анализы, чтобы цикл пересмотрел их заново.

    MAYBE — «посмотреть, но цена кусается»: цена упала -> рынок понял лучше ->
    вердикт мог смениться (в т.ч. на TAKE, и снова уведомим). Понемногу за цикл,
    чтобы не жечь LLM-бюджет. Возвращает число удалённых анализов.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    with get_session() as s:
        rows = (
            s.execute(
                select(Analysis)
                .join(Listing, Analysis.listing_id == Listing.ad_id)
                .where(
                    Analysis.created_at < cutoff,
                    Analysis.verdict == "MAYBE",
                    Listing.status == "active",
                )
                .order_by(Analysis.margin_eur.desc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
        if rows:
            for r in rows:
                s.delete(r)
            s.commit()
            return len(rows)
    return 0


def _pending_query(s, analyzed_ids: set, limit: int) -> list[Listing]:
    """Открытые лоты без анализа и без пустого описания. Свежие первыми:
    быть быстрее конкурента на недооценённых объявлениях."""
    return (
        s.execute(
            select(Listing)
            .where(
                Listing.status == "active",
                ~Listing.ad_id.in_(analyzed_ids),
                Listing.description != "",
            )
            .order_by(Listing.first_seen_at.desc())
            .limit(limit)
        ).scalars().all()
    )


def analyze_pending(
    provider: Provider,
    scraper: ScraperClient,
    deal_cfg: DealConfig,
    limit: int = 5,
    max_seconds: float | None = None,
) -> list[Analysis]:
    """Прогон анализа по объявлениям без анализа. Возвращает список свежих Analysis.

    limit — бюджет за цикл (медленный разгон, защита от бана). ScraperBlocked
    пробрасывается наверх, чтобы цикл ушёл в cooldown.

    max_seconds — мягкий дедлайн на весь проход: начатые объявления доводим до
    конца, новые после дедлайна не стартуем (цикл не вылезет за интервал).
    """
    import time

    deadline = (time.monotonic() + max_seconds) if max_seconds else None

    with get_session() as s:
        analyzed_ids = set(
            s.execute(select(Analysis.listing_id)).scalars().all()
        )
        pending = _pending_query(s, analyzed_ids, limit)

    results = []
    for listing in pending:
        if deadline is not None and time.monotonic() >= deadline:
            log.info(
                "дедлайн цикла: анализ остановлен (обработано %d из %d)",
                len(results),
                len(pending),
            )
            break
        if results:
            # Пейсинг под бесплатный TPM-лимит облачной LLM: не бьём всеми
            # запросами в одну минуту. Локальная модель не лимитирована — пауза
            # всё равно безвредна (цикл и так раз в 15 мин).
            gap = 15.0
            if deadline is not None and time.monotonic() + gap >= deadline:
                log.info("дедлайн близко: пауза пейсинга пропущена")
                break
            time.sleep(gap)
        try:
            a = run_on_listing(listing.ad_id, provider, scraper, deal_cfg)
            if a is not None:
                results.append(a)
        except ScraperBlocked:
            raise  # блок/бюджет -> цикл в cooldown, счётчик в следующий раз
        except Exception as e:
            log.warning("ошибка анализа %s: %s", listing.ad_id, e)
    return results