"""Module auth: the account of the administrator of the web interface (one account).

Owns the table admin_user (Р-7). Keeps the argon2 hash of the password; the password itself is not kept and never leaves.
The HTTP part of the authentication (the session in the cookie, the "lock" before the requests, the guard against brute
force by IP) is in the gateway; this module only checks and keeps the password. It has no count to take out to `calc`: the
work is hashing (a hash and a check are about 100 ms each, with a salt that makes the hash differ on every call) and the
table. The hashing runs in the pool of computation (`compute.run`), not in the loop that serves every module and window.

Queries (the answer is the model, as it is):
  auth.login {username, password} -> LoginReply {ok}
      checks the pair against the argon2 hash (in constant time even for an unknown login: the existence of a user is not
      given away by the time). A password found right also renews a hash made with outdated parameters, in the table of the
      module itself (a query with a write of its own).
  auth.exists {} -> AdminState {exists, username}
      whether an administrator is set up (is there a record) and his name (for the screen of the login: the single-user
      application shows the account already made instead of a field for the login). username = "" when there is no record.

Command that answers:
  auth.password.set {username, password} -> PasswordSaved {ok}
      makes or changes the account (hashes the password); not ok when the name or the password is empty.

Bootstrap (on_start): if there is no account yet and ST_ADMIN_USER + ST_ADMIN_PASSWORD are set in the environment, the
administrator is made of them (the first setup). Otherwise a warning: no login is possible until there is an account.
"""
from __future__ import annotations

import os

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from core import compute
from core.contracts import AdminState, LoginReply, PasswordSaved
from core.db import Database
from core.envelope import Envelope
from core.module import BaseModule
from core.topics import AUTH_EXISTS, AUTH_LOGIN, AUTH_PASSWORD_SET

from .repository import AuthRepository


class AuthModule(BaseModule):
    name = "auth"

    def __init__(self, bus, db: Database) -> None:
        self.repo = AuthRepository(db)
        self._ph = PasswordHasher()
        # a dummy hash for an answer in constant time when the login is unknown
        self._dummy_hash = self._ph.hash("\x00timing-dummy")
        super().__init__(bus)

    def register(self) -> None:
        self.handle(AUTH_LOGIN, self.on_login, concurrent=True)
        self.handle(AUTH_EXISTS, self.on_exists)
        self.handle(AUTH_PASSWORD_SET, self.on_password_set)

    async def on_start(self) -> None:
        if await self.repo.get() is not None:
            return
        user = os.environ.get("ST_ADMIN_USER", "").strip()
        password = os.environ.get("ST_ADMIN_PASSWORD", "")
        if user and password:
            await self.repo.set(user, await compute.run(self._ph.hash, password))
            self.log.info("администратор создан из окружения: %s", user)
        else:
            self.log.warning(
                "администратор не настроен: задайте ST_ADMIN_USER и "
                "ST_ADMIN_PASSWORD в .env и перезапустите — вход в "
                "веб-интерфейс невозможен, пока учётки нет")

    async def on_login(self, env: Envelope) -> LoginReply:
        username = (env.payload.get("username") or "").strip()
        password = env.payload.get("password") or ""
        row = await self.repo.get()
        if not row or username != row["username"]:
            # the dummy hash is checked: the time of the answer does not depend on whether the login exists (the guard
            # against a brute force by time)
            await self._safe_verify(self._dummy_hash, password)
            return LoginReply(ok=False)
        if not await self._safe_verify(row["password_hash"], password):
            return LoginReply(ok=False)
        # a transparent upgrade of the parameters of the hash, if they are outdated
        if self._ph.check_needs_rehash(row["password_hash"]):
            await self.repo.set(username, await compute.run(self._ph.hash, password))
        return LoginReply(ok=True)

    async def on_exists(self, env: Envelope) -> AdminState:
        # the name is for the screen of the login (a single-user application: no field for the login, the administrator
        # already made is shown)
        row = await self.repo.get()
        return AdminState(exists=row is not None, username=row["username"] if row else "")

    async def on_password_set(self, env: Envelope) -> PasswordSaved:
        username = (env.payload.get("username") or "").strip()
        password = env.payload.get("password") or ""
        if not username or not password:
            return PasswordSaved(ok=False)
        await self.repo.set(username, await compute.run(self._ph.hash, password))
        self.log.info("учётка администратора обновлена: %s", username)
        return PasswordSaved(ok=True)

    async def _safe_verify(self, password_hash: str, password: str) -> bool:
        try:
            await compute.run(self._ph.verify, password_hash, password)
            return True
        except (VerifyMismatchError, InvalidHashError):
            return False
