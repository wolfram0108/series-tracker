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

# Колонки, которые можно писать извне (без id и last_scan_time —
# последняя обновляется только touch_scan_time).
_WRITABLE = {"url", "name", "name_en", "site", "save_path", "season",
             "quality", "auto_scan_enabled", "quality_override",
             "resolution_override", "source_type", "parser_profile_id",
             "ignored_seasons", "vk_search_mode", "vk_quality_priority"}


class CatalogRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    _BOOLS = ("auto_scan_enabled",)

    async def all_series(self) -> list[dict]:
        rows = await self._db.fetch_all(
            f"SELECT {_COLUMNS} FROM series ORDER BY id")
        return self._db.coerce_bools(rows, self._BOOLS)

    async def get_series(self, series_id: int) -> dict | None:
        row = await self._db.fetch_one(
            f"SELECT {_COLUMNS} FROM series WHERE id=?", (series_id,))
        return self._db.coerce_bools(row, self._BOOLS)

    async def set_save_path(self, series_id: int, save_path: str) -> None:
        await self._db.execute(
            "UPDATE series SET save_path=? WHERE id=?",
            (save_path, series_id))

    async def create_series(self, data: dict) -> int:
        fields = {k: v for k, v in data.items() if k in _WRITABLE}
        # дефолты старой ORM-модели (NOT NULL-колонки без DB-дефолтов)
        fields.setdefault("source_type", "torrent")
        fields.setdefault("auto_scan_enabled", False)
        fields.setdefault("vk_search_mode", "search")
        fields.setdefault("ignored_seasons", "[]")
        cols = ", ".join(fields)
        marks = ", ".join("?" for _ in fields)
        await self._db.execute(
            f"INSERT INTO series ({cols}) VALUES ({marks})",
            tuple(fields.values()))
        row = await self._db.fetch_one(
            "SELECT id FROM series ORDER BY id DESC LIMIT 1")
        return row["id"]

    async def update_series(self, series_id: int, data: dict) -> dict:
        """Обновляет только живые колонки; возвращает применённые поля."""
        fields = {k: v for k, v in data.items() if k in _WRITABLE}
        if fields:
            sets = ", ".join(f"{k}=?" for k in fields)
            await self._db.execute(
                f"UPDATE series SET {sets} WHERE id=?",
                (*fields.values(), series_id))
        return fields

    async def delete_series(self, series_id: int) -> None:
        # series_statuses — мёртвая таблица (Р-11), но строка-сирота после
        # удаления серии не нужна и ей: чистим вместе со строкой series.
        await self._db.execute(
            "DELETE FROM series_statuses WHERE series_id=?", (series_id,))
        # enforce_fk=False: дочерние таблицы дочищают владельцы по
        # событию series.deleted (Р-19) — мгновенные сироты допустимы.
        await self._db.execute(
            "DELETE FROM series WHERE id=?", (series_id,), enforce_fk=False)

    async def touch_scan_time(self, series_id: int) -> str:
        from datetime import datetime, timezone
        # naive-UTC с микросекундами — формат хранения прод-БД
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")
        await self._db.execute(
            "UPDATE series SET last_scan_time=? WHERE id=?",
            (now, series_id))
        return now
