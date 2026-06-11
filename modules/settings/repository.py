"""Репозиторий settings: таблица settings (key/value, схема прод-БД)."""
from __future__ import annotations

from core.db import Database


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    async def set(self, key: str, value: str) -> None:
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value))
