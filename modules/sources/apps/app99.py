"""The API of the applications of the App99 family (半日 and its kin): every request and answer is AES-CBC under the
device key, every request is signed, and a guest session is opened the way the application opens it.

A request is the JSON of its fields with the session token, the time and a nonce, encrypted with a fresh IV under the key
of the device (its UUID without dashes) and sent as base64 of IV and text; it is signed by SHA-256 of nonce, time, body
and token. An answer is base64 of IV and text under the same key, the text sometimes compressed by zlib; `code` 0 is the
answer, any other is a refusal with its `msg`. The session is `/app/systemInit` (the players of the lines and the parsers
of their links) and a guest login (`/app/userInfo`) whose token goes with every request after it.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
import zlib
from typing import Any, Callable

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from core.reasons import reason

from . import AppProfile

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 20.0
DEFAULT_AGENT = "okhttp/3.14.9"
# The size of a page of the search, as the application asks for it.
SEARCH_LIMIT = 50


class AppError(RuntimeError):
    """The application refused or could not be asked: a coded reason; `transient` marks causes that may pass."""

    def __init__(self, why: str, transient: bool = False) -> None:
        super().__init__(why)
        self.reason = why
        self.transient = transient


def encrypt(plain: bytes, key: bytes, iv: bytes) -> bytes:
    padder = PKCS7(128).padder()
    padded = padder.update(plain) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def decrypt(blob: bytes, key: bytes, iv: bytes) -> bytes:
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(blob) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def seal(payload: dict, key: bytes, iv: bytes) -> str:
    """The body of a request: base64 of the IV and the encrypted JSON."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(iv + encrypt(text, key, iv)).decode()


def unseal(body: str, key: bytes) -> dict:
    """An answer: base64 of the IV and the encrypted JSON, compressed or not."""
    blob = base64.b64decode(body)
    plain = decrypt(blob[16:], key, blob[:16])
    try:
        plain = zlib.decompress(plain)
    except zlib.error:
        pass  # the application compresses only some answers; a plain one is read as it is
    return json.loads(plain.decode("utf-8"))


def signature(nonce: str, stamp: str, body: str, token: str) -> str:
    return hashlib.sha256(f"{nonce}:{stamp}:{body}:{token}".encode()).hexdigest()


class App99Client:
    """One application of the family: its session, opened once and again when the application refuses a request of it."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient],
                 clock: Callable[[], float] = time.time) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http, self._clock = http, clock
        self._key = self._settings["uuid"].replace("-", "").encode()
        self._token = ""
        self._system: dict | None = None
        self._opening = asyncio.Lock()

    # --- the wire ---------------------------------------------------------------------------------------------

    async def _post(self, path: str, fields: dict) -> dict:
        stamp = str(int(self._clock() * 1000))
        nonce = base64.b64encode(secrets.token_bytes(16)).decode()
        body = seal({**fields, "token": self._token, "timestamp": stamp, "nonce": nonce}, self._key,
                    secrets.token_bytes(16))
        settings = self._settings
        headers = {"User-Agent": settings.get("ua") or DEFAULT_AGENT, "Content-Type": "application/json",
                   "Accept": "application/json", "client_type": "android", "uuid": settings["uuid"],
                   "timestamp": stamp, "nonce": nonce, "sign": signature(nonce, stamp, body, self._token),
                   "version": settings.get("version") or settings["versionName"], "api_version": "v1"}
        began = time.monotonic()
        try:
            response = await self._http().post(settings["host"].rstrip("/") + path, content=body.encode(),
                                               headers=headers, timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            logger.info("приложение %s, %s: нет ответа — %s (%.1f с)", self.profile.name, path, type(exc).__name__,
                        time.monotonic() - began)
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        if response.status_code != 200:
            logger.info("приложение %s, %s: статус %d (%.1f с)", self.profile.name, path, response.status_code,
                        time.monotonic() - began)
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        try:
            answer = unseal(response.text, self._key)
        except (ValueError, UnicodeError, TypeError) as exc:
            logger.info("приложение %s, %s: ответ не расшифрован (%.1f с)", self.profile.name, path, time.monotonic() - began)
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path)) from exc
        logger.info("приложение %s, %s: код %s %s (%.1f с)", self.profile.name, path, answer.get("code"),
                    answer.get("msg") or "", time.monotonic() - began)
        return answer

    async def _open(self) -> dict:
        """The session: the system of the application, then the guest login whose token goes with the requests."""
        async with self._opening:
            if self._system is not None:
                return self._system
            settings = self._settings
            self._token = ""
            system = await self._post("/app/systemInit", {
                "v": settings["versionName"], "n": settings["name"], "s": settings["buildSignature"], "pl": "1",
                "apiVersion": "v2"})
            self._refused(system, "/app/systemInit")
            stamp = int(self._clock() * 1000)
            login = settings.get("loginPath") or "/app/userInfo"
            guest = await self._post(login, {
                "os": "android", "name": "android", "device": "android", "brand": "android", "apiVersion": "v2",
                "did": settings["uuid"], "channel": "",
                "app": {"version": settings["versionName"], "name": settings["name"], "package": settings["package"],
                        "buildNumber": settings["buildNumber"], "buildSignature": settings["buildSignature"],
                        "install": stamp, "update": stamp}})
            self._refused(guest, login)
            info = guest.get("userInfo")
            if not isinstance(info, dict) or "user_token" not in info:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step=login))
            self._token = str(info["user_token"] or "")
            self._system = system
            return system

    def _refused(self, answer: dict, path: str) -> None:
        if answer.get("code") != 0:
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=str(answer.get("msg") or "")))

    async def call(self, path: str, fields: dict, *, renew: bool = True) -> dict:
        """One request in the session. A refusal of a request of the catalog opens the session again once (a guest
        session ends at the application's will); the second refusal is the answer. A parser that refuses a link says so
        of the link (`renew` false): its refusal is the answer at once."""
        await self._open()
        answer = await self._post(path, fields)
        if answer.get("code") == 0:
            return answer
        if not renew:
            self._refused(answer, path)
        logger.info("приложение %s: отказ на %s (%s) — сессия открывается заново", self.profile.name, path,
                    answer.get("msg"))
        self._system = None
        await self._open()
        answer = await self._post(path, fields)
        self._refused(answer, path)
        return answer

    # --- what the application answers -------------------------------------------------------------------------

    async def system(self) -> dict:
        return await self._open()

    async def search(self, query: str, page: int) -> dict:
        """A page of the search as the application gives it: `data` (the cards), `page_count`, `total_count`."""
        return await self.call("/vod/search", {"kw": query, "page": page, "limit": SEARCH_LIMIT,
                                               "orderBy": "vod_hits_month", "sort": "desc"})

    async def detail(self, card: str) -> dict:
        """The card of a title: its fields and its lines (`play_from`, `play_url`)."""
        answer = await self.call("/vod/detail", {"id": str(card), "eps": "1", "v": "2.0.0", "pl": 1})
        data = answer.get("data")
        if not isinstance(data, dict):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card)))
        return data

    async def parse(self, parser: Any, link: str) -> str:
        """The address of the stream a parser of the application makes of the link of an episode."""
        answer = await self.call("/app/vodParser", {"id": parser, "url": link}, renew=False)
        address = answer.get("data")
        if not isinstance(address, str) or not address.startswith(("http://", "https://")):
            # The parser answers with the code of success and its refusal in `msg` (a limit of parses for the day).
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=str(parser),
                                  message=str(answer.get("msg") or "")[:300]))
        return address
