"""Репозиторий catalog: таблица series (схема прод-БД).

Колонка state и таблица series_statuses сознательно не читаются и не
пишутся (Р-11: статус — вычисляемое значение агрегатора); физически они
остаются в схеме до зачистки после переключения.
"""
from __future__ import annotations

from core.db import Database

# Все живые колонки series, КРОМЕ state (см. докстринг).
_COLUMNS = ("id, url, name, name_en, site, save_path, season, quality, "
            "last_scan_time, auto_scan_enabled, quality_override, "
            "resolution_override, source_type, parser_profile_id, "
            "ignored_seasons, vk_search_mode, vk_quality_priority")


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def all_series(self) -> list[dict]:
        return await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM series ORDER BY id")

    async def get_series(self, series_id: int) -> dict | None:
        return await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM series WHERE id=?", (series_id,))

    async def set_save_path(self, series_id: int, save_path: str) -> None:
        await self._db.execute(
            "UPDATE series SET save_path=? WHERE id=?",
            (save_path, series_id))

    async def touch_scan_time(self, series_id: int) -> None:
        from datetime import datetime, timezone
        # naive-UTC с микросекундами — формат хранения прод-БД
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        await self._db.execute(
            "UPDATE series SET last_scan_time=? WHERE id=?",
            (now, series_id))
