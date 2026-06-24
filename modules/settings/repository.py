"""Репозиторий settings: таблица settings (key/value, схема прод-БД)."""
from __future__ import annotations

from core import crypto
from core.db import Database

# Ключи-секреты: их значения хранятся в БД зашифрованными (Этап 3Б).
_SECRET_KEYS = {"tmdb_token"}


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return None
        return crypto.decrypt(row["value"]) if key in _SECRET_KEYS else row["value"]

    async def set(self, key: str, value: str) -> None:
        stored = crypto.encrypt(value) if key in _SECRET_KEYS else value
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, stored))

    async def encrypt_legacy_secrets(self) -> int:
        """Одноразовая миграция секретных ключей в шифр (Этап 3Б).
        Идемпотентна. Возвращает число перешифрованных значений."""
        migrated = 0
        for key in _SECRET_KEYS:
            row = await self._db.fetch_one(
                "SELECT value FROM settings WHERE key=?", (key,))
            if row and row["value"] and not crypto.is_encrypted(row["value"]):
                await self._db.execute(
                    "UPDATE settings SET value=? WHERE key=?",
                    (crypto.encrypt(row["value"]), key))
                migrated += 1
        return migrated

    async def by_prefix(self, prefix: str) -> dict:
        rows = await self._db.fetch_all(
            "SELECT key, value FROM settings WHERE key LIKE ?",
            (prefix + "%",))
        return {r["key"]: r["value"] for r in rows}

    # --- saved_paths (отдельная таблица, тот же владелец) ------------------

    async def list_paths(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, path FROM saved_paths ORDER BY id")

    async def add_path(self, path: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO saved_paths (path) VALUES (?)", (path,))

    async def remove_path(self, path_id: int) -> None:
        await self._db.execute(
            "DELETE FROM saved_paths WHERE id=?", (path_id,))
