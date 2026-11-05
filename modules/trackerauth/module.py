"""Модуль trackerauth — fetch-прокси к трекерам (решения Р-1, Р-4, Р-5).

Единственный интерфейс — query `trackerauth.fetch`: выполнить
авторизованный HTTP-запрос к трекеру. Сессии (requests.Session +
cloudscraper) не покидают модуль; куки персистятся в tracker_sessions
и переживают рестарт — лишних логинов трекер не видит («уважать
других»). Протухание детектится провайдером по ответу: релогин и один
повтор запроса; потребители никогда не получают страницу логина.

Вход привязан к учётным данным, под которыми получен (отпечаток в
`tracker_sessions.account_fingerprint`): смена логина или пароля делает
прежнюю сессию неприменимой и там, и в кэше памяти. Без этого смена
учётки оставалась незамеченной — сессия жива, протухания нет, релогин не
запускается, запросы идут под прежним аккаунтом. Клирэнс антибот-защиты
и UA от привязки свободны: они про клиента, а не про учётку.

Антибот-челлендж (Cloudflare) — третье состояние, помимо «живая сессия»
и «протухла»: сайт не пускает клиента вовсе, сессия при этом цела.
Распознаёт его сама сессия (`cf_challenge.arm`) и поднимает
`ChallengeSeen` вместо ответа — заглушку защиты не может принять за
страницу ни один путь модуля, включая логин провайдера. Проходится
настоящим браузером (cf_challenge.solve), результат — кука
`cf_clearance` и UA, которым она выдана; оба ложатся в сессию и в БД,
после чего операция повторяется один раз. Браузер поднимается только по
факту челленджа, а не на каждый запрос.

UA — свойство сессии, а не места вызова: провайдер объявляет свой
(`user_agent`), прохождение челленджа перезаписывает его тем, под
которым выдан клирэнс. Клирэнс без «своего» UA недействителен, поэтому
единственная точка установки — единственная защита от расхождения.

Rate-limit логинов: не чаще одного на трекер за LOGIN_MIN_INTERVAL —
даже наш собственный баг не сможет долбить трекер входами. Симметрично
ограничено и прохождение челленджа (CHALLENGE_MIN_INTERVAL): браузер
дорог, а зациклиться на нём легко.

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

from . import cf_challenge
from .providers import PROVIDERS, TrackerLoginError
from .repository import TrackerAuthRepository, account_fingerprint

LOGIN_MIN_INTERVAL = 300.0      # секунд между логинами на один трекер
CHALLENGE_MIN_INTERVAL = 60.0   # секунд между прохождениями челленджа


class TrackerauthModule(BaseModule):
    name = "trackerauth"

    def __init__(self, bus, db: Database) -> None:
        self.repo = TrackerAuthRepository(db)
        # Ключ включает отпечаток учётки: сессия, полученная под другими
        # учётными данными, по новому ключу просто не находится.
        self._sessions: dict[tuple[str, str, str | None],
                             requests.Session] = {}
        self._last_login: dict[str, float] = {}
        self._last_challenge: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("trackerauth.fetch", self.on_fetch)
        self.handle("trackerauth.credentials.get", self.on_credentials_get)
        self.handle("trackerauth.credentials.set", self.on_credentials_set)

    async def on_start(self) -> None:
        # Этап 3Б: одноразово зашифровать секреты, лежащие в открытом виде.
        migrated = await self.repo.encrypt_legacy_secrets()
        if migrated:
            self.log.info("зашифровано legacy-секретов в БД: %d", migrated)

    # --- учётные данные (владелец таблицы auth, Р-22) -------------------------------

    async def on_credentials_get(self, env: Envelope) -> dict:
        """Креды сервиса для вкладки настроек БЕЗ пароля (Этап 3,
        docs/security.md): наружу — только логин/URL и флаг has_password
        (задан ли пароль). Сам пароль никогда не покидает бэкенд."""
        row = await self.repo.get_credentials(env.payload["service"])
        if not row:
            return {"credentials": None}
        return {"credentials": {
            "username": row["username"], "url": row["url"],
            "has_password": bool(row["password"])}}

    async def on_credentials_set(self, env: Envelope) -> dict:
        p = env.payload
        service = p["service"]
        # пустой пароль = «не менять» (фронт не присылает текущий) — Этап 3
        password = p.get("password") or None
        await self.repo.upsert_credentials(service, p.get("username"),
                                           password, p.get("url"))
        # Инвалидация в ОБОИХ слоях: кэш в памяти и куки в БД. Сброс
        # только памяти оставлял прежний вход в БД, откуда он и
        # восстанавливался — запросы продолжали идти под прежним
        # аккаунтом. Клирэнс и UA сохраняются (они про клиента).
        for key in [k for k in self._sessions if k[0] == service]:
            self._sessions.pop(key, None)
        dropped = await self.repo.drop_account_cookies(service)
        if dropped:
            self.log.info("%s: учётные данные изменены — куки авторизации "
                          "сброшены (%d домен(ов)), нужен вход", service,
                          dropped)
        self.publish_event("trackerauth.credentials.changed",
                           {"service": service})
        return {"ok": True}

    # --- сессии и логин ------------------------------------------------------------

    async def _credentials(self, service: str) -> dict:
        row = await self.repo.get_credentials(service)
        if not row:
            raise TrackerLoginError(f"в БД нет учётных данных для {service}")
        return row

    async def _get_session(self, service: str, url: str) -> requests.Session:
        """Сессия под ТЕКУЩУЮ учётку сервиса. Вход, полученный под другими
        учётными данными, не применяется: иначе смена логина в настройках
        остаётся незамеченной (прежняя сессия жива, протухания нет)."""
        provider = PROVIDERS[service]
        domain = provider.normalize_domain(url)
        fingerprint = account_fingerprint(
            await self.repo.get_credentials(service))
        key = (service, domain, fingerprint)
        if key in self._sessions:
            return self._sessions[key]

        session = (provider.make_session()
                   if hasattr(provider, "make_session") else requests.Session())
        cf_challenge.arm(session)
        if provider.user_agent:
            session.headers["User-Agent"] = provider.user_agent
        cookies, user_agent, foreign = await self.repo.load_session(
            service, domain, fingerprint)
        if foreign:
            self.log.info("сессия %s/%s получена под другой учётной записью — "
                          "куки авторизации не применяются, нужен вход",
                          service, domain)
        if cookies:
            for name, value in cookies.items():
                session.cookies.set(name, value, domain=domain)
            if not foreign:
                self.log.info("сессия %s/%s восстановлена из БД",
                              service, domain)
        if user_agent:
            # без «своего» UA восстановленный cf_clearance недействителен
            session.headers["User-Agent"] = user_agent
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
        # Клирэнс антибот-защиты переживает смену логина: он про клиента,
        # а не про учётку. Иначе каждый релогин тянул бы за собой лишний
        # прогон браузера через челлендж.
        clearance = session.cookies.get(cf_challenge.CLEARANCE_COOKIE)
        session.cookies.clear()
        if clearance:
            session.cookies.set(cf_challenge.CLEARANCE_COOKIE, clearance,
                                domain=domain)
        fingerprint = account_fingerprint(credentials)
        self.log.info("логин на %s/%s под учётной записью %s", service, domain,
                      credentials.get("username") or "?")
        await self._with_clearance(
            service, url, session, provider,
            lambda: asyncio.to_thread(provider.login, session,
                                      credentials, url))
        # Отметку rate-limit ставим ТОЛЬКО после успешного логина: иначе
        # неудачная попытка на одном зеркале (таймаут/сетевая ошибка)
        # «съедала» общий по сервису лимит и блокировала логин на другом
        # доступном зеркале того же трекера — перебор зеркал терял смысл.
        self._last_login[service] = time.monotonic()
        await self.repo.save_cookies(service, domain, session, logged_in=True,
                                     fingerprint=fingerprint)

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

            def fetch():
                return self._do_request(session, provider, p)

            resp = await self._with_clearance(service, url, session,
                                              provider, fetch)
            if provider.is_logged_out(resp):
                self.log.info("сессия %s протухла — релогин и повтор", service)
                await self._login(service, url, session)
                resp = await self._with_clearance(service, url, session,
                                                  provider, fetch)
                if provider.is_logged_out(resp):
                    raise TrackerLoginError(
                        f"{service}: после релогина всё ещё разлогинены")
            domain = provider.normalize_domain(url)
            await self.repo.save_cookies(service, domain, session,
                                     logged_in=False)
            return self._pack(resp)

    # --- антибот-челлендж ------------------------------------------------------------

    async def _with_clearance(self, service: str, url: str,
                              session: requests.Session, provider, operation):
        """Операция на трекере: увиденный челлендж проходится, операция
        повторяется один раз. Точка одна — иначе путь, забывший про
        челлендж, снова читал бы заглушку как ответ сайта."""
        try:
            return await operation()
        except cf_challenge.ChallengeSeen:
            await self._pass_challenge(service, url, session, provider)
        try:
            return await operation()
        except cf_challenge.ChallengeSeen as exc:
            raise cf_challenge.ChallengeError(
                f"{service}: челлендж пройден, но сайт всё ещё "
                "не пускает") from exc

    async def _pass_challenge(self, service: str, url: str,
                              session: requests.Session, provider) -> None:
        """Пройти челлендж браузером и перенести результат в сессию.

        Rate-limit — как у логина: браузер дорог, а повтор челленджа на
        каждый запрос означал бы, что клирэнс не работает; в этом случае
        честнее упасть с внятной ошибкой, чем молча жечь ресурсы.
        """
        domain = provider.normalize_domain(url)
        since_last = time.monotonic() - self._last_challenge.get(service, -1e9)
        if since_last < CHALLENGE_MIN_INTERVAL:
            raise cf_challenge.ChallengeError(
                f"{service}: челлендж проходили {since_last:.0f} с назад — "
                f"повторный раньше {CHALLENGE_MIN_INTERVAL:.0f} с запрещён "
                "(rate-limit)")
        target = getattr(provider, "challenge_url", lambda u: u)(url)
        self.log.info("%s: антибот-челлендж — проходим браузером", service)
        user_agent, cookies = await cf_challenge.solve(target)
        for name, value in cookies.items():
            session.cookies.set(name, value, domain=domain)
        session.headers["User-Agent"] = user_agent
        self._last_challenge[service] = time.monotonic()
        await self.repo.save_cookies(service, domain, session,
                                     logged_in=False, user_agent=user_agent)
        self.log.info("%s: челлендж пройден, клирэнс получен", service)

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
