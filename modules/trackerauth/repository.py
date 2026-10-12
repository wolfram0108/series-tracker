"""Репозиторий trackerauth: свои таблицы — tracker_sessions и auth.

Весь SQL модуля живёт здесь (решение Р-7): «как лежит» — приватное
дело модуля, наружу — только методы предметного уровня.

Секреты (пароль в auth, куки сессий) хранятся в БД зашифрованными
(core.crypto, Этап 3Б); методы отдают/принимают открытый текст.
"""
from __future__ import annotations

import hashlib
import json
import time

import requests

from core import crypto
from core.db import Database

from . import cf_challenge

# Про клиента, а не про учётку: клирэнс антибот-защиты переживает смену
# логина, иначе каждая смена тянула бы лишний прогон браузера.
CLIENT_COOKIES = frozenset({cf_challenge.CLEARANCE_COOKIE})


def account_fingerprint(credentials: dict | None) -> str | None:
    """Отпечаток учётных данных, под которыми получен вход на трекер.

    Пароль входит хешем, а не открытым текстом. Смена логина ИЛИ пароля
    даёт другой отпечаток — прежний вход становится неприменимым, и
    следующий запрос входит заново.
    """
    if not credentials:
        return None
    raw = (f"{credentials.get('username') or ''}\0"
           f"{credentials.get('password') or ''}")
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


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
        if username is not None:
            username = username.strip()
        await self._db.execute(
            "INSERT INTO auth (auth_type, username, password, url) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(auth_type) DO UPDATE SET "
            "username=excluded.username, "
            "password=COALESCE(excluded.password, auth.password), "
            "url=excluded.url",
            (service, username, crypto.encrypt(password), url))

    async def load_session(self, service: str, domain: str,
                           fingerprint: str | None = None
                           ) -> tuple[dict, str | None, bool]:
        """Куки сессии, User-Agent и признак чужой учётки.

        Куки авторизации выдаются ТОЛЬКО если вход выполнен под теми же
        учётными данными, что лежат в настройках сейчас. Иначе смена
        учётки оставалась бы незамеченной: прежняя сессия жива,
        протухания нет, релогин не запускается — и запросы идут под
        прежним аккаунтом, с его лимитами и правами.

        Клирэнс антибот-защиты и UA — про клиента, а не про учётку,
        поэтому выдаются всегда. UA возвращается вместе с куками не для
        красоты: клирэнс действителен только в паре с тем UA, под
        которым выдан, — врозь они бесполезны.
        """
        row = await self._db.fetch_one(
            "SELECT cookies_json, user_agent, account_fingerprint "
            "FROM tracker_sessions WHERE service=? AND domain=?",
            (service, domain))
        if not row:
            return {}, None, False
        cookies = json.loads(crypto.decrypt(row["cookies_json"]))
        # NULL — «неизвестно, под какой учёткой получена» (запись старше
        # привязки), и это НЕ то же, что «получена под другой». Куки
        # отбрасываются только при явном несовпадении: иначе терялась бы
        # рабочая сессия там, где автоматический вход невозможен —
        # форма входа RuTracker содержит капчу (находка 65).
        stored = row["account_fingerprint"]
        foreign = stored is not None and stored != fingerprint
        if foreign:
            cookies = {name: value for name, value in cookies.items()
                       if name in CLIENT_COOKIES}
        return cookies, row["user_agent"], foreign

    async def save_cookies(self, service: str, domain: str,
                           session: requests.Session, *,
                           logged_in: bool,
                           user_agent: str | None = None,
                           fingerprint: str | None = None) -> None:
        # NULL → не менять существующее (COALESCE): обычный запрос не
        # должен затирать ни UA от челленджа, ни отпечаток учётки входа.
        now = _now()
        cookies = crypto.encrypt(json.dumps(session.cookies.get_dict()))
        await self._db.execute(
            "INSERT INTO tracker_sessions "
            "(service, domain, cookies_json, last_login_at, updated_at, "
            "user_agent, account_fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(service, domain) DO UPDATE SET "
            "cookies_json=excluded.cookies_json, "
            "updated_at=excluded.updated_at, "
            "user_agent=COALESCE(excluded.user_agent, tracker_sessions.user_agent), "
            "account_fingerprint=COALESCE(excluded.account_fingerprint, "
            "tracker_sessions.account_fingerprint)"
            + (", last_login_at=excluded.last_login_at" if logged_in else ""),
            (service, domain, cookies, now if logged_in else None, now,
             user_agent, fingerprint))

    async def drop_account_cookies(self, service: str) -> int:
        """Выбросить куки авторизации сервиса, сохранив клиентские.

        Вызывается при смене учётных данных: прежний вход больше не
        применим, и БД обязана это отразить — иначе сессия
        восстанавливается оттуда и запросы идут под прежним аккаунтом
        (протухания нет, релогин не запускается). Клирэнс и UA остаются:
        они про клиента, а не про учётку.
        """
        rows = await self._db.fetch_all(
            "SELECT domain, cookies_json FROM tracker_sessions "
            "WHERE service=?", (service,))
        for row in rows:
            cookies = json.loads(crypto.decrypt(row["cookies_json"]))
            kept = {name: value for name, value in cookies.items()
                    if name in CLIENT_COOKIES}
            await self._db.execute(
                "UPDATE tracker_sessions SET cookies_json=?, "
                "account_fingerprint=NULL, updated_at=? "
                "WHERE service=? AND domain=?",
                (crypto.encrypt(json.dumps(kept)), _now(), service,
                 row["domain"]))
        return len(rows)

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
