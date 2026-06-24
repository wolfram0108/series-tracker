"""HTTP-аутентификация: вход / выход / кто-я.

Проверку пароля делает модуль auth (через шину); здесь — сессия в
подписанной куке (ставит SessionMiddleware) и защита от перебора по IP.
Сам «замок» (требование сессии на остальных запросах) — в
AuthGateMiddleware (см. module.py)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from core import BusRequestError

# Защита от перебора: на IP — окно неудач и временный бан (in-memory;
# одно-процессное приложение, отдельного хранилища не нужно). Реальный IP
# за reverse-proxy даёт uvicorn --proxy-headers (Этап 2).
_MAX_FAILS = 5
_WINDOW_SECONDS = 300.0
_BAN_SECONDS = 300.0


class _RateLimiter:
    def __init__(self) -> None:
        self._fails: dict[str, list[float]] = {}   # ip -> метки неудач
        self._ban: dict[str, float] = {}           # ip -> забанен до (monotonic)

    def ban_remaining(self, ip: str, now: float) -> float:
        return max(0.0, self._ban.get(ip, 0.0) - now)

    def record_fail(self, ip: str, now: float) -> None:
        fails = [t for t in self._fails.get(ip, []) if now - t < _WINDOW_SECONDS]
        fails.append(now)
        self._fails[ip] = fails
        if len(fails) >= _MAX_FAILS:
            self._ban[ip] = now + _BAN_SECONDS
            self._fails.pop(ip, None)

    def reset(self, ip: str) -> None:
        self._fails.pop(ip, None)
        self._ban.pop(ip, None)


def build_router(gw):
    r = APIRouter()
    limiter = _RateLimiter()

    @r.post("/api/login")
    async def login(request: Request):
        now = time.monotonic()
        ip = request.client.host if request.client else "?"
        wait = limiter.ban_remaining(ip, now)
        if wait > 0:
            return JSONResponse(
                {"success": False,
                 "error": f"Слишком много попыток. Повторите через {int(wait) + 1} с."},
                status_code=429)
        data = await request.json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        try:
            reply = await gw.request(
                "auth.login", {"username": username, "password": password},
                timeout=10)
        except BusRequestError:
            return JSONResponse(
                {"success": False, "error": "Сервис аутентификации недоступен."},
                status_code=503)
        if not reply.get("ok"):
            limiter.record_fail(ip, now)
            return JSONResponse(
                {"success": False, "error": "Неверный логин или пароль."},
                status_code=401)
        limiter.reset(ip)
        request.session["user"] = username
        return {"success": True, "username": username}

    @r.post("/api/logout")
    async def logout(request: Request):
        request.session.clear()
        return {"success": True}

    @r.get("/api/me")
    async def me(request: Request):
        user = request.session.get("user")
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        return {"authenticated": True, "username": user}

    return r
