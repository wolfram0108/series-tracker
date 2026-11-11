"""The API of the application 咕噜: protobuf over one HTTP endpoint, a session key agreed by ECDH, every request AES-GCM.

A session begins with a handshake: the client sends its public key of the curve P-256 (a protobuf, zlib, AES-CBC under a
key it makes and names in a header) and gets the number of the session and the public key of the server; the key of the
session is made of the shared secret (HMAC-SHA-256 with the number of the session, then with the label of the protocol).
A request after it is a protobuf `{time, major, minor, device, payload}`, raw deflate, AES-GCM under the key of the
session; the answer the same way, with the payload in its field 4 or a refusal in words in its field 3.

The boot of the application (request 1.0) lists its players (field 4: the code of a line, its name, the numbers of the
parsers it allows, in order) and its parsers (field 7: a number, the address a token is appended to, the key of the
answer that holds the stream). The card of a title (request 3.62) lists its lines (field 75: the code and the episodes —
a token and a label each). The stream of an episode is its token when it is an address the line has no parser for (or a
playlist or a file by its form); else what the parsers of the line answer, the first that gives an address.

The protocol follows the client of the application as the owner's prototype reads it (source-prototypes/chinese_sources,
sourcekit/gulu.py); the tests hold a model of the server by this description.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import struct
import time
import zlib
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.padding import PKCS7

from core.reasons import reason

from . import AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream, advertised_headers, parsed_address
from .appdrama import field, fields

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "Dalvik/2.1.0 (Linux; U; Android 11; Pixel 4)"
SESSION_LABEL = b"v2-session\x01"
BOOT, SEARCH, DETAIL = (1, 0), (3, 61), (3, 62)
_MEDIA = re.compile(r"\.(m3u8|mp4|mkv)$", re.I)


def message(*parts: tuple[int, int | str | bytes]) -> bytes:
    return b"".join(field(number, value) for number, value in parts)


def first(data: bytes, number: int):
    """The first field of a protobuf with the number, as it is (a number or bytes); None without one."""
    return next((value for found, value in fields(data) if found == number), None)


def text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else ""


def packed(value) -> list[int]:
    """The numbers of a packed field (varints one after another)."""
    found, number, shift = [], 0, 0
    for byte in value if isinstance(value, bytes) else b"":
        number |= (byte & 127) << shift
        shift += 7
        if not byte & 128:
            found.append(number)
            number = shift = 0
    return found


def cbc(data: bytes, key: bytes, iv: bytes, *, decrypt: bool = False) -> bytes:
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    if decrypt:
        decryptor = cipher.decryptor()
        plain = decryptor.update(data) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        return unpadder.update(plain) + unpadder.finalize()
    padder = PKCS7(128).padder()
    encryptor = cipher.encryptor()
    return encryptor.update(padder.update(data) + padder.finalize()) + encryptor.finalize()


def session_key(shared_x: bytes, session: str) -> bytes:
    """The key of a session of the shared secret and the number of the session, as the application derives it."""
    first_pass = hmac.new(session.encode(), shared_x, hashlib.sha256).digest()
    return hmac.new(first_pass, SESSION_LABEL, hashlib.sha256).digest()


def sealed(key: bytes, plain: bytes, nonce: bytes) -> bytes:
    """A request or an answer under the key of the session: raw deflate, AES-GCM, the nonce in front."""
    packer = zlib.compressobj(6, wbits=-15)
    return nonce + AESGCM(key).encrypt(nonce, packer.compress(plain) + packer.flush(), None)


def opened(key: bytes, data: bytes) -> bytes:
    return zlib.decompress(AESGCM(key).decrypt(data[:12], data[12:], None), -15)


def players_of(boot: bytes) -> dict[str, dict]:
    """The lines the application knows, by code: the name of each and the numbers of the parsers it allows, in order."""
    return {text(first(each, 3)): {"name": text(first(each, 4)) or text(first(each, 2)), "parsers": packed(first(each, 8))}
            for number, each in fields(boot) if number == 4 and isinstance(each, bytes) and first(each, 3)}


def parsers_of(boot: bytes) -> dict[int, dict]:
    """The parsers of the application, by number: the address a token is appended to and the key of the stream in the
    answer."""
    return {first(each, 1): {"name": text(first(each, 2)), "address": text(first(each, 3)), "key": text(first(each, 10)) or "url"}
            for number, each in fields(boot) if number == 7 and isinstance(each, bytes) and isinstance(first(each, 1), int)}


def lines_of(detail: bytes, players: dict[str, dict]) -> list[AppLine]:
    lines = []
    for number, each in fields(detail):
        if number != 75 or not isinstance(each, bytes):
            continue
        code = text(first(each, 1))
        items = []
        for inner, episode in fields(each):
            token, label = (first(episode, 3), first(episode, 4)) if inner == 2 and isinstance(episode, bytes) else (None, None)
            if token is not None and label is not None:
                items.append(AppItem(len(items) + 1, text(label).strip(), text(token).strip()))
        known = players.get(code, {})
        if code and items:
            lines.append(AppLine(code, known.get("name", ""), not known.get("parsers"),
                                 ",".join(str(parser) for parser in known.get("parsers", [])), tuple(items)))
    return lines


def cards_of(found: bytes) -> list[dict]:
    """The cards of a page of the search: the messages that hold a number, a year and a name, wherever they are nested."""
    cards: dict[int, dict] = {}

    def walk(data: bytes, depth: int) -> None:
        if depth > 4:
            return
        try:
            inner = fields(data)
        except (IndexError, ValueError, struct.error):
            return
        by = dict(inner)
        if isinstance(by.get(1), int) and isinstance(by.get(2), int) and 1900 <= by[2] <= 2100 and isinstance(by.get(3), bytes):
            name = text(by[3])
            if name and "\x00" not in name and "�" not in name:
                cards[by[1]] = {"id": by[1], "name": name, "sub": "", "year": str(by[2]), "area": "", "class": "",
                                "remarks": "", "blurb": "", "pic": ""}
                return
        for _number, value in inner:
            if isinstance(value, bytes):
                walk(value, depth + 1)

    walk(found, 0)
    return list(cards.values())


class GuluClient:
    """The session with the application and its requests."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock = clock
        self._key = b""
        self._session = ""
        self._device = self._settings.get("device") or secrets.token_hex(8)
        self._boot = b""
        self._opening = asyncio.Lock()

    # --- the session ------------------------------------------------------------------------------------------------

    async def _send(self, body: bytes, headers: dict[str, str]) -> bytes:
        try:
            response = await self._http().post(self._settings["endpoint"], content=body, timeout=REQUEST_TIMEOUT_S, headers={
                "Content-Type": "application/x-protobuf", "User-Agent": AGENT, "X-Player-Page-Protection": "1", **headers})
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        if response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        return response.content

    async def _open(self) -> None:
        async with self._opening:
            if self._key:
                return
            private = ec.generate_private_key(ec.SECP256R1())
            public = private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
            wrap = secrets.token_hex(16).encode()
            hello = message((1, public), (2, "1.0.0"), (3, message((1, 1), (2, 1), (3, 1))))
            iv = secrets.token_bytes(16)
            reply = await self._send(base64.b64encode(iv + cbc(zlib.compress(hello, 5), wrap, iv)),
                                     {"X-Handshake-Key": wrap.decode()})
            try:
                raw = base64.b64decode(reply)
                answer = zlib.decompress(cbc(raw[16:], wrap, raw[:16], decrypt=True))
                session, peer = text(first(answer, 1)), first(answer, 2)
                shared = private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), peer))
            except (ValueError, TypeError, zlib.error) as exc:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step="handshake")) from exc
            self._session, self._key = session, session_key(shared, session)
            app = message((1, "咕噜咕噜"), (2, "2.1.2"), (3, "com.jymqfh.xee"), (4, self._device), (5, "212"))
            device = message((1, self._device), (2, 0), (3, "11"), (4, "Pixel 4"),
                             (5, "google/redfin/redfin:11/RQ3A.211001.001"), (6, "google"), (7, "redfin"), (8, 0),
                             (9, "unknown"), (10, "unknown"), (11, 0), (12, 30))
            try:
                self._boot = await self._asked(BOOT, message((1, "v1"), (2, "Android 11"), (3, "gulu"), (4, app), (5, device)))
            except AppError:
                self._key = b""
                raise

    async def _asked(self, method: tuple[int, int], payload: bytes) -> bytes:
        stamp = int(self._clock() * 1000)
        plain = message((1, stamp), (2, method[0]), (3, method[1]), (4, self._device), (5, b""), (6, payload), (7, stamp))
        began = time.monotonic()
        reply = await self._send(message((1, stamp), (2, sealed(self._key, plain, secrets.token_bytes(12)))),
                                 {"X-Session-Id": self._session})
        try:
            data = first(reply, 2)
            raw = opened(self._key, data) if isinstance(data, bytes) and len(data) >= 28 else None
        except (InvalidTag, zlib.error, IndexError, ValueError):
            raw = None
        if raw is None:
            # The server no longer knows the session (or never answered in its form): the next request opens a new one.
            self._key = b""
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=f"{method[0]}.{method[1]}"), True)
        found = first(raw, 4)
        logger.info("приложение %s, запрос %d.%d: %s (%.1f с)", self.profile.name, method[0], method[1],
                    "ответ" if found is not None else "отказ", time.monotonic() - began)
        if not isinstance(found, bytes):
            raise AppError(reason("app_refused", app=self.profile.label, step=f"{method[0]}.{method[1]}",
                                  message=text(first(raw, 3))[:300]))
        return found

    async def call(self, method: tuple[int, int], payload: bytes) -> bytes:
        """One request in the session; a session the server dropped is opened anew, once."""
        for attempt in (1, 2):
            await self._open()
            try:
                return await self._asked(method, payload)
            except AppError as exc:
                if self._key or attempt == 2:
                    raise
                logger.info("приложение %s: сессия открывается заново — %s", self.profile.name, exc.reason)
        raise AssertionError("unreachable")

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        if not str(card_id).isdigit():
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        detail = await self.call(DETAIL, message((1, int(card_id)), (4, "APP_PLATFORM_ANDROID_TV"), (5, 0)))
        name = text(first(detail, 5)).strip()
        lines = lines_of(detail, players_of(self._boot))
        if not name and not lines:
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        return AppCardData([name] if name else [], "", lines)

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        await self._open()
        allowed = players_of(self._boot).get(line, {}).get("parsers", [])
        if token.startswith(("http://", "https://")) and (not allowed or _MEDIA.search(urlsplit(token).path)):
            return AppStream(token, {})
        parsers = parsers_of(self._boot)
        said, passing = "", False
        for number in allowed:
            parser = parsers.get(number)
            if parser is None or not parser["address"].startswith(("http://", "https://")):
                continue
            began = time.monotonic()
            try:
                response = await self._http().get(parser["address"] + quote(token, safe=""), timeout=REQUEST_TIMEOUT_S,
                                                  headers={"User-Agent": AGENT}, follow_redirects=True)
                answer = response.json() if response.status_code == 200 else None
            except httpx.HTTPError as exc:
                said, passing = type(exc).__name__, True
                continue
            except ValueError:
                answer = None
            target, words = parsed_address(answer, parser["key"])
            logger.info("приложение %s, линия %s: парсер %s — статус %s, %s (%.1f с)", self.profile.name, line, number,
                        response.status_code, "адрес" if target else f"без адреса: {words[:120] or '—'}",
                        time.monotonic() - began)
            if target:
                return AppStream(target, advertised_headers(answer))
            said = words or f"status {response.status_code}"
            passing = passing or response.status_code >= 500 or response.status_code == 429 or not words.strip()
        if not allowed:
            raise AppError(reason("app_no_parser", app=self.profile.label, line=line, tried="—"))
        raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=said[:300]), transient=passing)

    async def search_page(self, query: str, page: int) -> AppPage:
        found = await self.call(SEARCH, message((1, query), (2, page), (5, message((1, 1), (2, 0), (3, "vod_hits_week")))))
        return AppPage(cards_of(found))
