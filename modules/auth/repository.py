"""The repository of auth: the table admin_user (one row, one administrator)."""
from __future__ import annotations

from core.db import Database


class AuthRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self) -> dict | None:
        return await self._db.fetch_one(
            "SELECT username, password_hash FROM admin_user WHERE id = 1")

    async def set(self, username: str, password_hash: str) -> None:
        await self._db.execute(
            "INSERT INTO admin_user (id, username, password_hash) "
            "VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET "
            "username = excluded.username, "
            "password_hash = excluded.password_hash",
            (username, password_hash))
