"""Категоризированное логирование (принцип 6).

Группа = имя модуля. Формат и раскладка сохранены от старой системы:
ротируемые JSON-строки в logs/, по файлу на уровень (debug.log,
info.log, warning.log, error.log). Без внешних зависимостей.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

LOG_DIR = os.environ.get("ST_LOG_DIR", "logs")
_LEVELS = {
    logging.DEBUG: "debug.log",
    logging.INFO: "info.log",
    logging.WARNING: "warning.log",
    logging.ERROR: "error.log",
}
_configured = False

# Группы с включённой DEBUG-детализацией (принцип 6: регулируемые
# уровни). Пустой набор = DEBUG не пишется никому; INFO+ — всегда.
# Управляется вкладкой отладки через gateway (Р-22).
_debug_groups: set[str] = set()


def set_debug_groups(groups: set[str]) -> None:
    global _debug_groups
    _debug_groups = set(groups)


def debug_groups() -> set[str]:
    return set(_debug_groups)


class _LevelOnly(logging.Filter):
    def __init__(self, level: int) -> None:
        super().__init__()
        self._level = level

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno != self._level:
            return False
        if record.levelno == logging.DEBUG:
            return record.name in _debug_groups
        return True


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(
                record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "group": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure(level: int = logging.DEBUG) -> None:
    """Идемпотентная настройка корневого логгера под раскладку logs/."""
    global _configured
    if _configured:
        return
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level)
    fmt = _JsonFormatter()
    for lvl, fname in _LEVELS.items():
        handler = RotatingFileHandler(
            os.path.join(LOG_DIR, fname),
            maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        handler.setLevel(lvl)
        handler.addFilter(_LevelOnly(lvl))
        handler.setFormatter(fmt)
        root.addHandler(handler)
    _configured = True


def get_logger(group: str) -> logging.Logger:
    """Логгер группы. Имя группы = имя модуля (принцип 6)."""
    configure()
    return logging.getLogger(group)
