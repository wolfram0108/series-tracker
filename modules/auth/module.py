"""Модуль auth — учётка администратора веб-интерфейса (один аккаунт).

Владеет таблицей admin_user (Р-7). Хранит argon2-хэш пароля; открытый
пароль не хранится и наружу не возвращается. HTTP-часть аутентификации
(сессия в куке, «замок» перед запросами, защита от перебора по IP) —
в gateway; этот модуль отвечает только за проверку и хранение пароля.

Queries:
  auth.login {username, password} -> {ok: bool}
      проверка пары логин/пароль по argon2-хэшу (постоянное время даже
      при неизвестном логине — не выдаём наличие пользователя по времени).
  auth.exists {} -> {exists: bool}
      настроен ли администратор (есть ли запись).
  auth.password.set {username, password} -> {ok: bool}
      создать/сменить учётку (хэширует пароль).

Bootstrap (on_start): если учётки ещё нет и заданы переменные окружения
ST_ADMIN_USER + ST_ADMIN_PASSWORD — создаёт администратора из них
(первичная настройка). Иначе предупреждает: вход невозможен, пока учётки нет.
"""
from __future__ import annotations

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from .repository import AuthRepository


class AuthModule(BaseModule):
    name = "auth"

    def __init__(self, bus, db: Database) -> None:
        self.repo = AuthRepository(db)
        self._ph = PasswordHasher()
        # фиктивный хэш для постоянного времени ответа при неизвестном логине
        self._dummy_hash = self._ph.hash("\x00timing-dummy")
        super().__init__(bus)

    def register(self) -> None:
        self.handle("auth.login", self.on_login, concurrent=True)
        self.handle("auth.exists", self.on_exists)
        self.handle("auth.password.set", self.on_password_set)

    async def on_start(self) -> None:
        if await self.repo.get() is not None:
            return
        user = os.environ.get("ST_ADMIN_USER", "").strip()
        password = os.environ.get("ST_ADMIN_PASSWORD", "")
        if user and password:
            await self.repo.set(user, self._ph.hash(password))
            self.log.info("администратор создан из окружения: %s", user)
        else:
            self.log.warning(
                "администратор не настроен: задайте ST_ADMIN_USER и "
                "ST_ADMIN_PASSWORD в .env и перезапустите — вход в "
                "веб-интерфейс невозможен, пока учётки нет")

    async def on_login(self, env: Envelope) -> dict:
        username = (env.payload.get("username") or "").strip()
        password = env.payload.get("password") or ""
        row = await self.repo.get()
        if not row or username != row["username"]:
            # сверяем фиктивный хэш — время ответа не зависит от того,
            # существует ли логин (защита от timing-перебора)
            self._safe_verify(self._dummy_hash, password)
            return {"ok": False}
        if not self._safe_verify(row["password_hash"], password):
            return {"ok": False}
        # прозрачный апгрейд параметров хэша, если они устарели
        if self._ph.check_needs_rehash(row["password_hash"]):
            await self.repo.set(username, self._ph.hash(password))
        return {"ok": True}

    async def on_exists(self, env: Envelope) -> dict:
        return {"exists": await self.repo.get() is not None}

    async def on_password_set(self, env: Envelope) -> dict:
        username = (env.payload.get("username") or "").strip()
        password = env.payload.get("password") or ""
        if not username or not password:
            return {"ok": False}
        await self.repo.set(username, self._ph.hash(password))
        self.log.info("учётка администратора обновлена: %s", username)
        return {"ok": True}

    def _safe_verify(self, password_hash: str, password: str) -> bool:
        try:
            self._ph.verify(password_hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
