"""Пользовательские настройки в БД (поверх config.yaml).

Актуальная идея: часть опций (стоп-слова, вкл/выкл Vinted, дефолтная категория)
можно менять со страницы настроек без правки YAML и перезапуска. Значения
хранятся JSON-ом в таблице settings; приложение читает их на каждом цикле.
"""
from __future__ import annotations

import json

from flipper.app.db import get_session
from flipper.app.models import Setting

_UNSET = object()


def _load() -> dict:
    with get_session() as s:
        return {r.key: r.value for r in s.query(Setting).all()}


def get_settings() -> dict:
    """Все настройки с распарсенным JSON."""
    out: dict = {}
    for key, raw in _load().items():
        try:
            out[key] = json.loads(raw) if raw else None
        except (TypeError, json.JSONDecodeError):
            out[key] = raw
    return out


def get_setting(key: str, default=_UNSET):
    """Одна настройка; default вернётся если нет или при корявом JSON."""
    with get_session() as s:
        row = s.get(Setting, key)
    if row is None or not row.value:
        return default if default is not _UNSET else None
    try:
        return json.loads(row.value)
    except (TypeError, json.JSONDecodeError):
        return default if default is not _UNSET else row.value        # 1:1 text


def update_settings(patch: dict) -> dict:
    """merge-обновление настроек (None из patch удаляет ключ)."""
    with get_session() as s:
        for key, value in patch.items():
            row = s.get(Setting, key)
            if value is None or value == "":
                if row is not None:
                    s.delete(row)
                continue
            if row is None:
                row = Setting(key=key, value="")
                s.add(row)
            row.value = json.dumps(value, ensure_ascii=False)
        s.commit()
    return get_settings()


def effective_excludes(scraper_cfg):
    """Стоп-слова: из настроек БД, если заданы, иначе из config.yaml."""
    excl = get_setting("exclude_keywords")
    excl_title = get_setting("exclude_title_keywords")
    return (
        excld_list(excl, scraper_cfg.exclude_keywords),
        excld_list(excl_title, scraper_cfg.exclude_title_keywords),
    )


def excld_list(value, fallback: list[str]) -> list[str]:
    if not value:
        return list(fallback or [])
    if isinstance(value, str):
        return [w for w in value.replace(",", " ").split() if w]
    return [str(w).strip() for w in value if str(w).strip()]


def vinted_enabled_effective(vinted_cfg) -> bool:
    """Vinted вкл/выкл: настройка БД перекрывает config.yaml."""
    val = get_setting("vinted_enabled")
    return bool(val) if val is not None else bool(vinted_cfg.enabled)


def vinted_default_catalogs_effective(vinted_cfg) -> list[int]:
    """Дефолтные категории Vinted: настройка БД перекрывает config.yaml."""
    val = get_setting("vinted_default_catalog_ids")
    if val is not None:
        return [int(c) for c in val if str(c).strip().isdigit()]
    fallback = getattr(vinted_cfg, "default_catalog_ids", None)
    return list(fallback or [])


def has_active_vinted_searches(vinted_cfg) -> bool:
    """Есть ли что сканировать: конфиг или активные поиски Vinted в БД."""
    if vinted_cfg.searches:
        return True
    try:
        from flipper.app.db import get_session
        from flipper.app.models import SOURCE_VINTED, Search
        from sqlalchemy import select

        with get_session() as s:
            rows = s.execute(
                select(Search.id).where(
                    Search.active == 1, Search.source == SOURCE_VINTED
                )
            ).first()
            return rows is not None
    except Exception:
        return False