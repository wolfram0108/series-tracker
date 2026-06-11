"""Модуль trackerauth — fetch-прокси к трекерам (решения Р-1, Р-4, Р-5).

Единственный интерфейс — query `trackerauth.fetch`: выполнить
авторизованный HTTP-запрос к трекеру. Сессии (requests.Session +
cloudscraper) не покидают модуль; куки персистятся в tracker_sessions
и переживают рестарт — лишних логинов трекер не видит («уважать
других»). Протухание детектится провайдером по ответу: релогин и один
повтор запроса; потребители никогда не получают страницу логина.

Rate-limit логинов: не чаще одного на трекер за LOGIN_MIN_INTERVAL —
даже наш собственный баг не сможет долбить трекер входами.

Все сетевые вызовы — блокирующий requests в to_thread с таймаутами
(Р-5): зависимость одного трекера не замораживает процесс.
"""
from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import requests

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from .providers import PROVIDERS, TrackerLoginError
from .repository import TrackerAuthRepository

LOGIN_MIN_INTERVAL = 300.0  # секунд между логинами на один трекер


class TrackerauthModule(BaseModule):
    name = "trackerauth"

    def __init__(self, bus, db: Database) -> None:
        self.repo = TrackerAuthRepository(db)
        self._sessions: dict[tuple[str, str], requests.Session] = {}
        self._last_login: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("trackerauth.fetch", self.on_fetch)

    # --- сессии и логин ------------------------------------------------------------

    async def _credentials(self, service: str) -> dict:
        row = await self.repo.get_credentials(service)
        if not row:
            raise TrackerLoginError(f"в БД нет учётных данных для {service}")
        return row

    async def _get_session(self, service: str, url: str) -> requests.Session:
        provider = PROVIDERS[service]
        domain = provider.normalize_domain(url)
        key = (service, domain)
        if key in self._sessions:
            return self._sessions[key]

        session = (provider.make_session()
                   if hasattr(provider, "make_session") else requests.Session())
        cookies = await self.repo.load_cookies(service, domain)
        if cookies:
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=domain)
            self.log.info("сессия %s/%s восстановлена из БД", service, domain)
        self._sessions[key] = session
        return session

    async def _login(self, service: str, url: str,
                     session: requests.Session) -> None:
        """Логин с rate-limit (защита аккаунта и чужого сервиса)."""
        provider = PROVIDERS[service]
        domain = provider.normalize_domain(url)
        since_last = time.monotonic() - self._last_login.get(service, -1e9)
        if since_last < LOGIN_MIN_INTERVAL:
            raise TrackerLoginError(
                f"логин на {service} был {since_last:.0f} с назад — повторный "
                f"раньше {LOGIN_MIN_INTERVAL:.0f} с запрещён (rate-limit)")
        credentials = await self._credentials(service)
        self._last_login[service] = time.monotonic()
        session.cookies.clear()
        self.log.info("логин на %s/%s", service, domain)
        await asyncio.to_thread(provider.login, session, credentials, url)
        await self.repo.save_cookies(service, domain, session, logged_in=True)

    # --- fetch ----------------------------------------------------------------------

    async def on_fetch(self, env: Envelope) -> dict:
        """payload: {service, url, method?, params?, data?, timeout?}
        reply:   {status, final_url, content_type, text? | content_b64?}"""
        p = env.payload
        service = p["service"]
        if service not in PROVIDERS:
            raise TrackerLoginError(f"неизвестный трекер: {service}")
        url = p["url"]
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:  # одна операция на трекер за раз — не давим сервис
            session = await self._get_session(service, url)
            provider = PROVIDERS[service]
            resp = await self._do_request(session, provider, p)
            if provider.is_logged_out(resp):
                self.log.info("сессия %s протухла — релогин и повтор", service)
                await self._login(service, url, session)
                resp = await self._do_request(session, provider, p)
                if provider.is_logged_out(resp):
                    raise TrackerLoginError(
                        f"{service}: после релогина всё ещё разлогинены")
            domain = provider.normalize_domain(url)
            await self.repo.save_cookies(service, domain, session,
                                     logged_in=False)
            return self._pack(resp)

    async def _do_request(self, session: requests.Session, provider,
                          p: dict) -> requests.Response:
        headers = provider.request_headers(p["url"])
        headers.update(p.get("headers") or {})
        params = dict(p.get("params") or {})
        if getattr(provider, "needs_credential_params", False):
            # секрет (токен) подмешивается здесь и не покидает модуль
            credentials = await self._credentials(provider.service)
            params.update(provider.credential_params(credentials))

        def call() -> requests.Response:
            return session.request(
                p.get("method", "GET"), p["url"],
                params=params or None, data=p.get("data"),
                headers=headers, timeout=float(p.get("timeout", 25)))

        return await asyncio.to_thread(call)

    @staticmethod
    def _pack(resp: requests.Response) -> dict[str, Any]:
        content_type = resp.headers.get("Content-Type", "")
        out = {"status": resp.status_code, "final_url": resp.url,
               "content_type": content_type}
        if content_type.startswith("text/") or "json" in content_type:
            out["text"] = resp.text
        else:
            out["content_b64"] = base64.b64encode(resp.content).decode()
        return out
