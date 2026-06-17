"""Pydantic-модели ответов gateway (Ф0 типизации фронта,
docs/frontend-rewrite.md §9).

ПРАВИЛО ADDITIVE: response_model по умолчанию ОТРЕЗАЕТ поля, которых нет
в модели. Чтобы форма ответов не менялась молча, базовая модель —
`extra="allow"` (лишние поля проходят насквозь). На маршрутах с
опциональными полями ставим `response_model_exclude_none=True`, иначе
FastAPI добавит `null`-поле. Любое изменение формы ловит golden-харнесс
(tests/api_golden.py check).

Формы выверены по снятому эталону (tests/golden/api/*.json).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ApiModel(BaseModel):
    """База: лишние поля не отрезаются (additive-инвариант)."""
    model_config = ConfigDict(extra="allow")


class OkResponse(ApiModel):
    """Стандартный успех: {success: true[, message]}."""
    success: bool
    message: str | None = None


class ErrorResponse(ApiModel):
    """Ошибка (отдаётся через JSONResponse; здесь — для документации
    `responses={code: {"model": ErrorResponse}}`)."""
    success: bool = False
    error: str


# --- блок «скан и очереди» (api_system) -------------------------------------

class ScannerStatus(ApiModel):
    scanner_enabled: bool
    is_scanning: bool
    is_awaiting_tasks: bool
    scan_interval: int
    next_scan_time: str | None = None


class QueueTask(ApiModel):
    """Задача очереди (агент/загрузки/прогресс). Поля динамические —
    extra='allow' пропускает всё; жёстко фиксировать не требуется."""


# --- блок «серии» (api_series) ----------------------------------------------

class TmdbInfo(ApiModel):
    """TMDB-маппинг серии; поля динамические (tmdb_id, series_name, year,
    poster_path, last_updated, ...)."""


class SeriesObject(ApiModel):
    """Серия (список и карточка). Объявлены ВСЕГДА присутствующие поля;
    условные (tracker_info — только торренты; downloaded_episodes_count —
    только список) проходят через extra='allow'. ВАЖНО: на серии НЕ
    применять exclude_none — иначе исчезнут null-поля (quality_override,
    tmdb_info и т.п.), что сломает форму контракта."""
    id: int
    name: str
    name_en: str
    site: str
    url: str
    save_path: str
    season: str | None = None  # в БД season nullable
    source_type: str
    vk_search_mode: str
    auto_scan_enabled: bool
    is_busy: bool
    statuses: list[str]
    parser_profile_id: int | None = None
    quality: str | None = None
    quality_override: str | None = None
    resolution_override: str | None = None
    ignored_seasons: str | None = None
    vk_quality_priority: str | None = None
    last_scan_time: str | None = None
    tmdb_info: TmdbInfo | None = None


class CreatedSeries(ApiModel):
    success: bool
    series_id: int


class TorrentHistoryItem(ApiModel):
    # Только torrent_id/link в БД NOT NULL; остальное nullable.
    id: int
    series_id: int
    torrent_id: str
    link: str
    qb_hash: str | None = None
    is_active: bool | None = None
    date_time: str | None = None
    episodes: str | None = None
    quality: str | None = None
