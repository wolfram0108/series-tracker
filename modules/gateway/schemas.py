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
    """Задача очереди (агент/загрузки). Поля динамические — extra='allow'
    пропускает всё; жёстко фиксировать форму очереди не требуется."""
