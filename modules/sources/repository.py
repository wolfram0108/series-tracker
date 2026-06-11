"""Репозиторий sources: таблица trackers (зеркала, типы аутентификации)."""
from __future__ import annotations

import json
from urllib.parse import urlparse

from core.db import Database


class SourcesRepository:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._cache: list[dict] | None = None

    async def all_trackers(self) -> list[dict]:
        if self._cache is None:
            rows = await self._db.fetch_all("SELECT * FROM trackers")
            for row in rows:
                row["mirrors"] = json.loads(row["mirrors"] or "[]")
                row["ui_features"] = json.loads(row["ui_features"] or "{}")
            self._cache = rows
        return self._cache

    async def resolve(self, url: str) -> dict | None:
        """Определяет трекер по домену URL (с учётом www./dl.-префиксов)."""
        domain = urlparse(url).netloc.removeprefix("www.").removeprefix("dl.")
        for tracker in await self.all_trackers():
            if domain in tracker["mirrors"]:
                return tracker
        return None
