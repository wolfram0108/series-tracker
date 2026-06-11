"""Репозиторий trackerauth: свои таблицы — tracker_sessions и auth.

Весь SQL модуля живёт здесь (решение Р-7): «как лежит» — приватное
дело модуля, наружу — только методы предметного уровня.
"""
from __future__ import annotations

import json
import time

import requests

from core.db import Database


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TrackerAuthRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_credentials(self, service: str) -> dict | None:
        return await self._db.fetch_one(
            "SELECT username, password, url FROM auth WHERE auth_type=?",
            (service,))

    async def load_cookies(self, service: str, domain: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT cookies_json FROM tracker_sessions "
            "WHERE service=? AND domain=?", (service, domain))
        return json.loads(row["cookies_json"]) if row else None

    async def save_cookies(self, service: str, domain: str,
                           session: requests.Session, *,
                           logged_in: bool) -> None:
        now = _now()
        await self._db.execute(
            "INSERT INTO tracker_sessions "
            "(service, domain, cookies_json, last_login_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(service, domain) DO UPDATE SET "
            "cookies_json=excluded.cookies_json, updated_at=excluded.updated_at"
            + (", last_login_at=excluded.last_login_at" if logged_in else ""),
            (service, domain, json.dumps(session.cookies.get_dict()),
             now if logged_in else None, now))
