"""The repository of settings: the table settings (key/value, the schema of the production database) and saved_paths."""
from __future__ import annotations

from core import crypto
from core.db import Database
from core.secrets import SECRET_SETTING_KEYS

# The secret keys: their values are kept in the database encrypted (stage 3Б).
SECRET_KEYS = SECRET_SETTING_KEYS


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str) -> str | None:
        row = await self._db.fetch_one(
            "SELECT value FROM settings WHERE key=?", (key,))
        if not row:
            return None
        return crypto.decrypt(row["value"]) if key in SECRET_KEYS else row["value"]

    async def set(self, key: str, value: str) -> None:
        stored = crypto.encrypt(value) if key in SECRET_KEYS else value
        await self._db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, stored))

    async def set_many(self, values: dict[str, str]) -> None:
        """All keys in one transaction: a form is saved whole or not at all."""
        await self._db.execute_many([
            ("INSERT INTO settings (key, value) VALUES (?, ?) "
             "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
             (key, crypto.encrypt(value) if key in SECRET_KEYS else value))
            for key, value in values.items()])

    async def encrypt_legacy_secrets(self) -> int:
        """The one-time migration of the secret keys into the cipher (stage 3Б). Idempotent. Returns the number of values
        encrypted."""
        migrated = 0
        for key in SECRET_KEYS:
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
        # A secret is read by its key alone, never in bulk.
        return {r["key"]: r["value"] for r in rows if r["key"] not in SECRET_KEYS}

    # --- saved_paths (a table of its own, the same owner) ------------------

    async def list_paths(self) -> list[dict]:
        return await self._db.fetch_all(
            "SELECT id, path FROM saved_paths ORDER BY id")

    async def add_path(self, path: str) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO saved_paths (path) VALUES (?)", (path,))

    async def remove_path(self, path_id: int) -> None:
        await self._db.execute(
            "DELETE FROM saved_paths WHERE id=?", (path_id,))
