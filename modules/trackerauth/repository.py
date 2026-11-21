"""trackerauth repository: its own tables, tracker_sessions and auth.

All of the module's SQL lives here (decision Р-7): how the data is laid out
is the module's private business; only domain-level methods go outward.

Secrets (the password in auth, session cookies) are stored encrypted in the DB
(core.crypto, Stage 3Б); methods take and return plain text.
"""
from __future__ import annotations

import json
import time

import requests

from calc.trackerauth.session import applicable_cookies, client_cookies
from core import crypto
from core.db import Database

from . import cf_challenge

# The clearance belongs to the client, not the account: it must survive a
# login change, or every change would force an extra browser run.
CLIENT_COOKIES = frozenset({cf_challenge.CLEARANCE_COOKIE})


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
                "password": crypto.decrypt(row["password"], f"auth:{service}"),
                "url": row["url"]}

    async def upsert_credentials(self, service: str, username: str | None,
                                 password: str | None,
                                 url: str | None = None) -> None:
        # password=NULL keeps the existing one (COALESCE): the front end never sends
        # the current password, so an empty field must not wipe it (Stage 3А).
        # A non-empty password is encrypted before writing (Stage 3Б).
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
        """Session cookies, User-Agent, and a flag for a foreign account.

        Auth cookies are returned ONLY if the login was done under the same
        credentials that are in the settings now. Otherwise an account change
        would go unnoticed: the old session stays alive, no expiry is detected,
        no re-login is triggered, and requests keep going out under the old
        account, with its limits and rights.

        The clearance and the UA are about the client, not the account, so they
        are always returned. The UA comes back with the cookies not for show:
        the clearance is valid only together with the UA it was issued under;
        apart, they are useless.
        """
        row = await self._db.fetch_one(
            "SELECT cookies_json, user_agent, account_fingerprint "
            "FROM tracker_sessions WHERE service=? AND domain=?",
            (service, domain))
        if not row:
            return {}, None, False
        # The rule of the binding (an unknown account is no mismatch, finding 65) is calc/trackerauth/session.py.
        stored = json.loads(crypto.decrypt(row["cookies_json"], f"tracker_sessions:{service}:{domain}"))
        cookies, foreign = applicable_cookies(stored, row["account_fingerprint"], fingerprint, CLIENT_COOKIES)
        return cookies, row["user_agent"], foreign

    async def save_cookies(self, service: str, domain: str,
                           session: requests.Session, *,
                           logged_in: bool,
                           user_agent: str | None = None,
                           fingerprint: str | None = None) -> None:
        # NULL keeps the existing value (COALESCE): a plain request must not overwrite
        # the UA from the challenge or the login account fingerprint.
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
        """Drop the service's auth cookies, keeping the client ones.

        Called on a credentials change: the previous login no longer applies, and
        the DB must reflect it, otherwise the session is restored from there and
        requests keep going out under the old account (no expiry, no re-login).
        The clearance and the UA stay: they are about the client, not the account.
        """
        rows = await self._db.fetch_all(
            "SELECT domain, cookies_json FROM tracker_sessions "
            "WHERE service=?", (service,))
        for row in rows:
            kept = client_cookies(
                json.loads(crypto.decrypt(row["cookies_json"], f"tracker_sessions:{service}:{row['domain']}")), CLIENT_COOKIES)
            await self._db.execute(
                "UPDATE tracker_sessions SET cookies_json=?, "
                "account_fingerprint=NULL, updated_at=? "
                "WHERE service=? AND domain=?",
                (crypto.encrypt(json.dumps(kept)), _now(), service,
                 row["domain"]))
        return len(rows)

    async def encrypt_legacy_secrets(self) -> int:
        """One-off migration: encrypt secrets stored in plain text (before Stage 3Б).
        Idempotent: already encrypted values are skipped. Returns the number of
        re-encrypted records."""
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
