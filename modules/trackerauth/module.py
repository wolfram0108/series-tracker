"""Module trackerauth: the fetch proxy to the trackers (decisions Р-1, Р-4, Р-5).

Queries and commands (the answers are the models of core/contracts/trackerauth.py, handed over as they are):
  trackerauth.fetch FetchRequest -> FetchReply: an authorised HTTP request to a tracker. The sessions (requests.Session +
      cloudscraper) never leave the module; the cookies persist in tracker_sessions and survive a restart, so the tracker
      sees no needless logins ("respect the others"). An expired session is detected by the provider from the answer: a
      login and one repeat of the request; consumers never get the login page.
  trackerauth.credentials.get ServiceRef -> CredentialsReply: the login and address of a service and whether a password is
      set, never the password (docs/security.md).
  trackerauth.secret.get ServiceRef -> SecretReply: the password, only for the eye of its field in the settings; on the bus
      it is masked in every dump (core/secrets.py).
  trackerauth.credentials.set CredentialsSet -> CredentialsSaved: written whole; an empty password leaves the stored one.
Events out: trackerauth.credentials.changed {service} (the sessions of the service are dropped).

A login is tied to the credentials it was got under (a fingerprint in `tracker_sessions.account_fingerprint`): a change of the
login or the password makes the earlier session inapplicable, both there and in the memory cache. Without it a change of the
account went unnoticed: the session is alive, nothing expires, no login starts, and the requests go on under the old account.
The rules of the binding are pure and live in calc/trackerauth/session.py. The clearance of the anti-bot protection and the UA
are free of the binding: they are about the client, not the account.

An anti-bot challenge (Cloudflare) is a third state beside "live session" and "expired": the site does not let the client in at
all while the session is intact. The session itself recognises it (`cf_challenge.arm`) and raises `ChallengeSeen` instead of an
answer, so no path of the module, the login of a provider included, can take the stub of the protection for a page. It is passed
by a real browser (cf_challenge.solve); the result is the `cf_clearance` cookie and the UA it was issued for, both go into the
session and the DB, and the operation is repeated once. The browser starts only when a challenge is seen, not on every request.

The UA is a property of the session, not of the call site: a provider declares its own (`user_agent`), and passing a challenge
overwrites it with the one the clearance was issued for. A clearance without "its" UA is invalid, so the one place that sets
the UA is the one protection against a mismatch.

Rate limit of logins: at most one per tracker per LOGIN_MIN_INTERVAL, so that even a bug of ours cannot hammer a tracker with
logins. Passing a challenge is limited the same way (CHALLENGE_MIN_INTERVAL): the browser is expensive and a loop on it is easy.

All network calls are blocking requests in to_thread with timeouts (Р-5): one tracker that hangs does not freeze the process.
"""
from __future__ import annotations

import asyncio
import base64
import time

import requests

from calc.trackerauth.session import account_fingerprint
from core.contracts import (Credentials, CredentialsChanged, CredentialsReply, CredentialsSaved, CredentialsSet, FetchReply,
                            FetchRequest, SecretReply, ServiceRef)
from core.module import BaseModule
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason
from core.topics import (TRACKERAUTH_CREDENTIALS_CHANGED, TRACKERAUTH_CREDENTIALS_GET, TRACKERAUTH_CREDENTIALS_SET,
                         TRACKERAUTH_FETCH, TRACKERAUTH_SECRET_GET)

from . import cf_challenge
from .providers import PROVIDERS, TrackerLoginError
from .repository import TrackerAuthRepository

LOGIN_MIN_INTERVAL = 300.0      # seconds between logins to one tracker
CHALLENGE_MIN_INTERVAL = 60.0   # seconds between passes of the challenge


class TrackerauthModule(BaseModule):
    name = "trackerauth"

    def __init__(self, bus, db: Database) -> None:
        self.repo = TrackerAuthRepository(db)
        # The key holds the fingerprint of the account: a session got under other credentials is simply not found under
        # the new key.
        self._sessions: dict[tuple[str, str, str | None],
                             requests.Session] = {}
        self._last_login: dict[str, float] = {}
        self._last_challenge: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle(TRACKERAUTH_FETCH, self.on_fetch)
        self.handle(TRACKERAUTH_CREDENTIALS_GET, self.on_credentials_get)
        self.handle(TRACKERAUTH_CREDENTIALS_SET, self.on_credentials_set)
        self.handle(TRACKERAUTH_SECRET_GET, self.on_secret_get)

    async def on_start(self) -> None:
        # Stage 3Б: the secrets stored in plain text are encrypted once.
        migrated = await self.repo.encrypt_legacy_secrets()
        if migrated:
            self.log.info("зашифровано legacy-секретов в БД: %d", migrated)

    # --- the credentials (the owner of the table auth, Р-22) ---------------------------

    async def on_credentials_get(self, env: Envelope) -> CredentialsReply:
        """The credentials of a service for the tab of the settings WITHOUT the password (Stage 3, docs/security.md): outward
        only the login, the address and the flag has_password. The password itself never leaves the backend this way."""
        row = await self.repo.get_credentials(ServiceRef.model_validate(env.payload).service)
        if not row:
            return CredentialsReply()
        return CredentialsReply(credentials=Credentials(
            username=row["username"], url=row["url"], has_password=bool(row["password"])))

    async def on_secret_get(self, env: Envelope) -> SecretReply:
        """The password of a service, for the one who asked to see it (the eye of its field in the settings): empty when there is
        none. It goes to the window only on that request, never with the credentials; on the bus it is masked in every dump
        (core/secrets.py)."""
        row = await self.repo.get_credentials(ServiceRef.model_validate(env.payload).service)
        return SecretReply(secret=(row or {}).get("password") or "")

    async def on_credentials_set(self, env: Envelope) -> CredentialsSaved:
        command = CredentialsSet.model_validate(env.payload)
        service = command.service
        # An empty password means "do not change" (the front end never sends the current one), Stage 3.
        await self.repo.upsert_credentials(service, command.username, command.password or None, command.url)
        # Invalidation in BOTH layers: the memory cache and the cookies in the DB. Dropping only the memory left the earlier
        # login in the DB, from where it was restored and the requests went on under the old account. The clearance and the UA
        # stay (they are about the client).
        for key in [k for k in self._sessions if k[0] == service]:
            self._sessions.pop(key, None)
        dropped = await self.repo.drop_account_cookies(service)
        if dropped:
            self.log.info("%s: учётные данные изменены — куки авторизации "
                          "сброшены (%d домен(ов)), нужен вход", service,
                          dropped)
        self.publish_event(TRACKERAUTH_CREDENTIALS_CHANGED, CredentialsChanged(service=service).model_dump(mode="json"))
        return CredentialsSaved(ok=True)

    # --- the sessions and the login ----------------------------------------------------

    async def _credentials(self, service: str) -> dict:
        row = await self.repo.get_credentials(service)
        if not row:
            raise TrackerLoginError(reason("tracker_no_credentials", service=service))
        return row

    async def _get_session(self, service: str, url: str) -> requests.Session:
        """The session under the CURRENT account of the service. A login got under other credentials is not applied: otherwise
        a change of the login in the settings goes unnoticed (the earlier session is alive, nothing expires)."""
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
            # without "its" UA a restored cf_clearance is invalid
            session.headers["User-Agent"] = user_agent
        self._sessions[key] = session
        return session

    async def _login(self, service: str, url: str,
                     session: requests.Session) -> None:
        """A login under the rate limit (a protection of the account and of the other's service)."""
        provider = PROVIDERS[service]
        domain = provider.normalize_domain(url)
        since_last = time.monotonic() - self._last_login.get(service, -1e9)
        if since_last < LOGIN_MIN_INTERVAL:
            raise TrackerLoginError(reason("tracker_login_rate_limited", service=service,
                                           seconds=round(since_last),
                                           interval=round(LOGIN_MIN_INTERVAL)))
        credentials = await self._credentials(service)
        # The clearance of the anti-bot protection survives a change of the login: it is about the client, not the account.
        # Otherwise every re-login would drag a needless run of the browser through the challenge.
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
        # The rate-limit mark is set ONLY after a successful login: otherwise a failed attempt on one mirror (a timeout, a
        # network error) ate the limit shared by the service and blocked the login on another reachable mirror of the same
        # tracker, and trying the mirrors made no sense.
        self._last_login[service] = time.monotonic()
        await self.repo.save_cookies(service, domain, session, logged_in=True,
                                     fingerprint=fingerprint)

    # --- fetch ----------------------------------------------------------------------

    async def on_fetch(self, env: Envelope) -> FetchReply:
        request = FetchRequest.model_validate(env.payload)
        service = request.service
        if service not in PROVIDERS:
            raise TrackerLoginError(reason("tracker_unknown", service=service))
        url = request.url
        lock = self._locks.setdefault(service, asyncio.Lock())
        async with lock:  # one operation per tracker at a time, so the service is not pressed
            session = await self._get_session(service, url)
            provider = PROVIDERS[service]

            def fetch():
                return self._do_request(session, provider, request)

            resp = await self._with_clearance(service, url, session,
                                              provider, fetch)
            if provider.is_logged_out(resp):
                self.log.info("сессия %s протухла — релогин и повтор", service)
                await self._login(service, url, session)
                resp = await self._with_clearance(service, url, session,
                                                  provider, fetch)
                if provider.is_logged_out(resp):
                    raise TrackerLoginError(reason("tracker_still_logged_out", service=service))
            domain = provider.normalize_domain(url)
            await self.repo.save_cookies(service, domain, session,
                                     logged_in=False)
            return self._pack(resp)

    # --- the anti-bot challenge --------------------------------------------------------

    async def _with_clearance(self, service: str, url: str,
                              session: requests.Session, provider, operation):
        """An operation on a tracker: a challenge that is seen is passed, and the operation is repeated once. There is one
        place for it, or a path that forgot the challenge would again read the stub as the answer of the site."""
        try:
            return await operation()
        except cf_challenge.ChallengeSeen:
            await self._pass_challenge(service, url, session, provider)
        try:
            return await operation()
        except cf_challenge.ChallengeSeen as exc:
            raise cf_challenge.ChallengeError(
                reason("tracker_challenge_refused", service=service)) from exc

    async def _pass_challenge(self, service: str, url: str,
                              session: requests.Session, provider) -> None:
        """Pass the challenge with the browser and carry the result into the session.

        The rate limit is the login's: the browser is expensive, and a challenge repeated on every request would mean the
        clearance does not work; then it is more honest to fail with a plain error than to burn resources silently."""
        domain = provider.normalize_domain(url)
        since_last = time.monotonic() - self._last_challenge.get(service, -1e9)
        if since_last < CHALLENGE_MIN_INTERVAL:
            raise cf_challenge.ChallengeError(
                reason("tracker_challenge_rate_limited", service=service,
                       seconds=round(since_last), interval=round(CHALLENGE_MIN_INTERVAL)))
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
                          request: FetchRequest) -> requests.Response:
        headers = provider.request_headers(request.url)
        headers.update(request.headers or {})
        params = dict(request.params or {})
        if getattr(provider, "needs_credential_params", False):
            # the secret (a token) is mixed in here and never leaves the module
            credentials = await self._credentials(provider.service)
            params.update(provider.credential_params(credentials))

        def call() -> requests.Response:
            return session.request(
                request.method, request.url,
                params=params or None, data=request.data,
                headers=headers, timeout=float(request.timeout))

        return await asyncio.to_thread(call)

    @staticmethod
    def _pack(resp: requests.Response) -> FetchReply:
        content_type = resp.headers.get("Content-Type", "")
        body = ({"text": resp.text} if content_type.startswith("text/") or "json" in content_type
                else {"content_b64": base64.b64encode(resp.content).decode()})
        return FetchReply(status=resp.status_code, final_url=resp.url, content_type=content_type, **body)
