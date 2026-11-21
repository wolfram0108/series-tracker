"""HTTP authentication: login / logout / who am I.

The check of the password is made by the module auth (over the bus); here are the session in the signed cookie (set by
SessionMiddleware) and the guard against a guess by IP (the rules of both are in `calc/gateway/auth.py`). The «lock» itself (the
demand of a session on the other requests) is AuthGateMiddleware (see module.py)."""
from __future__ import annotations

import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from calc.gateway.auth import PASSWORD_MIN, Attempts, after_failure, ban_remaining, password_long_enough
from core.module import BusRequestError
from core.reasons import reason
from core.topics import AUTH_EXISTS, AUTH_LOGIN, AUTH_PASSWORD_SET


def build_router(gw):
    r = APIRouter()
    # The attempts of each address, in memory (a one-process application needs no store of its own). The real IP behind a
    # reverse proxy is given by uvicorn --proxy-headers (stage 2).
    attempts: dict[str, Attempts] = {}

    @r.post("/api/login")
    async def login(request: Request):
        now = time.monotonic()
        ip = request.client.host if request.client else "?"
        wait = ban_remaining(attempts.get(ip, Attempts()), now)
        if wait > 0:
            return JSONResponse(
                {"success": False,
                 "error": reason("too_many_attempts", seconds=int(wait) + 1)},
                status_code=429)
        data = await request.json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        try:
            reply = await gw.reply_of(AUTH_LOGIN, {"username": username, "password": password})
        except BusRequestError:
            return JSONResponse(
                {"success": False, "error": reason("auth_unavailable")},
                status_code=503)
        if not reply.ok:
            attempts[ip] = after_failure(attempts.get(ip, Attempts()), now)
            return JSONResponse(
                {"success": False, "error": reason("login_wrong")},
                status_code=401)
        attempts.pop(ip, None)
        request.session["user"] = username
        return {"success": True, "username": username}

    @r.get("/api/auth/status")
    async def auth_status(request: Request):
        """The public status for the onboarding: whether the administrator is set up, his name and whether the current client is signed in.
        By it the front chooses the setup or login modal and shows the name of the administrator (admin) on the login screen in place of the
        field for the login (the application is single-user)."""
        admin = ""
        try:
            reply = await gw.reply_of(AUTH_EXISTS, {})
            configured = reply.exists
            admin = reply.username
        except BusRequestError:
            configured = False
        user = request.session.get("user")
        return {"configured": configured,
                "authenticated": bool(user),
                "username": user or "",
                "admin": admin}

    @r.post("/api/setup")
    async def setup(request: Request):
        """The first setup of the administrator. Available ONLY while there is no administrator; after it is made — 403 (it cannot
        be made again bypassing the login). Success signs in at once (the session)."""
        try:
            exists = (await gw.reply_of(AUTH_EXISTS, {})).exists
        except BusRequestError:
            return JSONResponse(
                {"success": False, "error": reason("auth_unavailable")},
                status_code=503)
        if exists:
            return JSONResponse(
                {"success": False, "error": reason("admin_configured")},
                status_code=403)
        data = await request.json()
        username = (data.get("username") or "").strip()
        password = data.get("password") or ""
        if not username or not password_long_enough(password):
            return JSONResponse(
                {"success": False,
                 "error": reason("setup_incomplete", min=PASSWORD_MIN)},
                status_code=400)
        reply = await gw.reply_of(AUTH_PASSWORD_SET, {"username": username, "password": password})
        if not reply.ok:
            return JSONResponse(
                {"success": False, "error": reason("admin_create_failed")},
                status_code=400)
        request.session["user"] = username
        return {"success": True, "username": username}

    @r.post("/api/auth/password")
    async def change_password(request: Request):
        """The change of the password by the administrator who is signed in (the section «Аккаунт» of the settings).
        Behind the «lock» (a session is needed): checks the length of the new password (≥8) and renews the hash. The name does not change — the
        session stays valid.

        The current password is NOT asked: the active session already confirms the owner (a deliberate decision for a single-user application —
        not to make him recall the old password when changing it from under a login)."""
        user = request.session.get("user")
        if not user:
            return JSONResponse(
                {"success": False, "error": reason("login_required")},
                status_code=401)
        data = await request.json()
        new = data.get("new") or ""
        if not password_long_enough(new):
            return JSONResponse(
                {"success": False,
                 "error": reason("new_password_short", min=PASSWORD_MIN)},
                status_code=400)
        reply = await gw.reply_of(AUTH_PASSWORD_SET, {"username": user, "password": new})
        if not reply.ok:
            return JSONResponse(
                {"success": False, "error": reason("password_change_failed")},
                status_code=400)
        return {"success": True}

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
