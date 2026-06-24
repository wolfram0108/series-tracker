"""Репозиторий trackerauth: свои таблицы — tracker_sessions и auth.

Весь SQL модуля живёт здесь (решение Р-7): «как лежит» — приватное
дело модуля, наружу — только методы предметного уровня.

Секреты (пароль в auth, куки сессий) хранятся в БД зашифрованными
(core.crypto, Этап 3Б); методы отдают/принимают открытый текст.
"""
from __future__ import annotations

import json
import time

import requests

from core import crypto
from core.db import Database


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class TrackerAuthRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def get_credentials(self, service: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT username, password, url FROM auth WHERE auth_type=?",
            (service,))
        if not row:
            return None
        return {"username": row["username"],
                "password": crypto.decrypt(row["password"]),
                "url": row["url"]}

    async def upsert_credentials(self, service: str, username: str | None,
                                 password: str | None,
                                 url: str | None = None) -> None:
        # password=NULL → не менять существующий (COALESCE): фронт не присылает
        # текущий пароль, пустое поле не должно его затирать (Этап 3А).
        # Непустой — шифруем перед записью (Этап 3Б).
        await self._db.execute(
            "INSERT INTO auth (auth_type, username, password, url) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(auth_type) DO UPDATE SET "
            "username=excluded.username, "
            "password=COALESCE(excluded.password, auth.password), "
            "url=excluded.url",
            (service, username, crypto.encrypt(password), url))

    async def load_cookies(self, service: str, domain: str) -> dict | None:
        row = await self._db.fetch_one(
            "SELECT cookies_json FROM tracker_sessions "
            "WHERE service=? AND domain=?", (service, domain))
        return json.loads(crypto.decrypt(row["cookies_json"])) if row else None

    async def save_cookies(self, service: str, domain: str,
                           session: requests.Session, *,
                           logged_in: bool) -> None:
        now = _now()
        cookies = crypto.encrypt(json.dumps(session.cookies.get_dict()))
        await self._db.execute(
            "INSERT INTO tracker_sessions "
            "(service, domain, cookies_json, last_login_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(service, domain) DO UPDATE SET "
            "cookies_json=excluded.cookies_json, updated_at=excluded.updated_at"
            + (", last_login_at=excluded.last_login_at" if logged_in else ""),
            (service, domain, cookies, now if logged_in else None, now))

    async def encrypt_legacy_secrets(self) -> int:
        """Одноразовая миграция: зашифровать секреты, лежащие в открытом
        виде (до Этапа 3Б). Идемпотентна — уже зашифрованные пропускает.
        Возвращает число перешифрованных записей."""
        migrated = 0
        for r in await self._db.fetch_all("SELECT auth_type, password FROM auth"):
            if r["password"] and not crypto.is_encrypted(r["password"]):
                await self._db.execute(
                    "UPDATE auth SET password=? WHERE auth_type=?",
                    (crypto.encrypt(r["password"]), r["auth_type"]))
                migrated += 1
        for r in await self._db.fetch_all(
                "SELECT service, domain, cookies_json FROM tracker_sessions"):
            if r["cookies_json"] and not crypto.is_encrypted(r["cookies_json"]):
                await self._db.execute(
                    "UPDATE tracker_sessions SET cookies_json=? "
                    "WHERE service=? AND domain=?",
                    (crypto.encrypt(r["cookies_json"]), r["service"], r["domain"]))
                migrated += 1
        return migrated
