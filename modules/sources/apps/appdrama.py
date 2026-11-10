"""The API of the applications of the AppDrama family (华谊, 橘汁): protobuf over HTTP, the fields of a request AES-ECB under
the data key, the device described in a header AES-CBC under the key of the parameters, signed by RSA.

The host of the API is what the navigator of the application says (`host`, a JSON `{domain}`), or the fallback host. A
session begins with the key of the zone (`/api/v5/find/app/zone`, signed by the public key of the profile); every request
after it is signed by that key. A request is the query of its fields with the time, AES-ECB under `dataKey`, prefixed
with a random string and cut into two fields of a protobuf with a random string, the time and a fake field. An answer is
a protobuf `{1: code, 2: msg, 3: data}`; code 200 is the answer, any other a refusal with its message. The card of a
title (`drama/getDetail`) lists its videos (field 29), each of a line (`source`, its name `source_cn`), with its title and
its token (`path`); the stream of an episode is `videoUsableUrl` of its line and token: an address and its headers.

The codec follows the client of the application as the owner's prototype reads it (source-prototypes/chinese_sources,
sourcekit/appdrama_codec.py); its vectors are in the tests.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import random
import string
import struct
import time
import uuid
from typing import Callable

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding as asymmetric
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from core.reasons import reason

from . import AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 20.0
SEARCH_PAGE = 50
AGENT = "okhttp/3.12.1"
SUCCESS = 200


# --- the codec ------------------------------------------------------------------------------------------------------

def _padded(data: bytes) -> bytes:
    padder = PKCS7(128).padder()
    return padder.update(data) + padder.finalize()


def ecb_encrypt(text: str, key: str) -> str:
    """AES-ECB under the key, PKCS#7, base64."""
    encryptor = Cipher(algorithms.AES(key.encode()), modes.ECB()).encryptor()
    return base64.b64encode(encryptor.update(_padded(text.encode())) + encryptor.finalize()).decode()


def cbc_encrypt_hex(text: str, key: str, iv: str) -> str:
    """AES-CBC under the key and IV, PKCS#7, hexadecimal."""
    encryptor = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).encryptor()
    return (encryptor.update(_padded(text.encode())) + encryptor.finalize()).hex()


def rsa_encrypt(text: str, public_key_b64: str) -> str:
    """RSA PKCS#1 v1.5 under a public key given as base64 of its DER."""
    key = serialization.load_der_public_key(base64.b64decode(public_key_b64))
    return base64.b64encode(key.encrypt(text.encode(), asymmetric.PKCS1v15())).decode()


def random_text(rng: random.Random, length: int) -> str:
    """Letters and digits, the last character `=`, as the client of the application makes its random strings."""
    return "".join(rng.choice(string.digits + string.ascii_letters) for _ in range(length - 1)) + "="


def _varint(value: int) -> bytes:
    if value < 0:
        value += 1 << 64
    out = b""
    while value > 127:
        out += bytes([128 | value & 127])
        value >>= 7
    return out + bytes([value & 127])


def field(number: int, value: int | str | bytes) -> bytes:
    """One field of a protobuf: a number as a varint, text or bytes by their length."""
    if isinstance(value, int):
        return _varint(number << 3) + _varint(value)
    data = value.encode() if isinstance(value, str) else value
    return _varint(number << 3 | 2) + _varint(len(data)) + data


def fields(data: bytes) -> list[tuple[int, int | bytes]]:
    """The fields of a protobuf as (number, value): a varint as a number, a fixed one too, a length as its bytes."""
    found, pos = [], 0
    while pos < len(data):
        tag, pos = _read_varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _read_varint(data, pos)
        elif wire == 2:
            length, pos = _read_varint(data, pos)
            value, pos = data[pos:pos + length], pos + length
        elif wire == 5:
            value, pos = struct.unpack("<I", data[pos:pos + 4])[0], pos + 4
        elif wire == 1:
            value, pos = struct.unpack("<Q", data[pos:pos + 8])[0], pos + 8
        else:
            break
        found.append((number, value))
    return found


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        byte = data[pos]
        result |= (byte & 127) << shift
        pos += 1
        if not byte & 128:
            return result, pos
        shift += 7


def _first(found: list[tuple[int, int | bytes]], number: int, default=None):
    value = next((each for num, each in found if num == number), default)
    if isinstance(value, bytes):
        try:
            return value.decode()
        except UnicodeDecodeError:
            return value
    return value


def answer_of(raw: bytes) -> tuple[int, str, bytes]:
    """(code, message, data) of an answer."""
    found = fields(raw)
    data = next((each for num, each in found if num == 3), b"")
    return int(_first(found, 1, 0) or 0), str(_first(found, 2, "") or ""), data if isinstance(data, bytes) else b""


def detail_of(data: bytes) -> dict:
    found = fields(data)
    videos = []
    for number, value in found:
        if number == 29 and isinstance(value, bytes):
            video = fields(value)
            videos.append({"title": str(_first(video, 2, "") or ""), "path": str(_first(video, 4, "") or ""),
                           "source": str(_first(video, 9, "") or ""), "source_cn": str(_first(video, 10, "") or ""),
                           "is_vip": bool(_first(video, 14, 0))})
    return {"id": _first(found, 4, 0), "intro": str(_first(found, 6, "") or ""), "name": str(_first(found, 9, "") or ""),
            "year": _first(found, 18, 0), "videos": videos}


def play_of(data: bytes) -> dict:
    found = fields(data)
    headers = {}
    for number, value in found:
        if number == 6 and isinstance(value, bytes):
            entry = fields(value)
            key = str(_first(entry, 1, "") or "")
            if key:
                headers[key] = str(_first(entry, 2, "") or "")
    return {"play_url": str(_first(found, 1, "") or ""), "headers": headers, "msg": str(_first(found, 9, "") or "")}


def page_of(data: bytes) -> list[dict]:
    cards = []
    for number, value in fields(data):
        if number == 1 and isinstance(value, bytes):
            bean = fields(value)
            cover = next((each for num, each in bean if num == 2 and isinstance(each, bytes)), b"")
            cards.append({"id": _first(bean, 3, 0), "area": str(_first(bean, 1, "") or ""),
                          "blurb": str(_first(bean, 4, "") or ""), "name": str(_first(bean, 5, "") or ""),
                          "remarks": str(_first(bean, 13, "") or ""), "year": _first(bean, 14, 0),
                          "pic": str(_first(fields(cover), 1, "") or "") if cover else ""})
    return cards


# --- the client -----------------------------------------------------------------------------------------------------

class AppDramaClient:
    """The session with one application of the family and its requests."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *, clock: Callable[[], float] = time.time,
                 rng: random.Random | None = None, uid: Callable[[], str] = lambda: uuid.uuid4().hex.upper()) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock, self._rng, self._uid = clock, rng or random.SystemRandom(), uid
        self._host = ""
        self._zone = ""
        self._opening = asyncio.Lock()

    # --- the request ------------------------------------------------------------------------------------------------

    def _stamp(self) -> int:
        return int(self._clock() * 1000)

    def _device(self) -> dict:
        uid, version = self._uid(), self._settings["version"]
        return {"country": "CN", "vName": version, "cpuId": "MT6893Z%2FCZA", "young": 0, "facturer": "Xiaomi",
                "pkg": self._settings["pkg"], "uuid": uid, "resolution": "1080x2272", "mac": "02%3A00%3A00%3A00%3A00%3A00",
                "abid": "397", "model": "M2012K11AC", "plat": "android", "udid": uid, "dpi": "440", "net": "1",
                "lang": "zh", "brand": "Xiaomi", "density": "2.75", "appName": self._settings["appName"],
                "cpu": "arm64-v8a", "chid": "10000", "carrier": "%E8%81%94%E9%80%9A", "_vOsCode": 33, "vOs": "13", "v": 1,
                "tenantId": "", "vApp": version.replace(".", ""), "device": 0, "androidID": "a1b2c3d4e5f67890"}

    def headers(self) -> dict[str, str]:
        """The headers of a request: the device, signed by the key of the zone (the public key before the session)."""
        device = self._device()
        stamp, nonce = self._stamp(), random_text(self._rng, 16)
        device["sig"] = rsa_encrypt(f"{stamp}{nonce}{device['vApp']}", self._zone or self._settings["publicKey"])
        signed = ecb_encrypt(f"{stamp}{nonce}", self._settings["dataIv"])
        device.update(random_str=nonce, timestamp=stamp, sig2=signed[:8], sig3=signed[8:])
        params = cbc_encrypt_hex(json.dumps(device, separators=(",", ":"), ensure_ascii=False), self._settings["paramsKey"],
                                 self._settings["paramsKey"])
        return {"User-Agent": AGENT, "Accept": "application/x-protobuf", "Content-Type": "application/x-protobuf",
                "publicParams": json.dumps({"paramsData": params}, separators=(",", ":"))}

    def body(self, params: dict) -> bytes:
        """The body of a request: its fields as a query with the time, AES-ECB under the data key, in a protobuf."""
        stamp, nonce, fake = self._stamp(), random_text(self._rng, 8), random_text(self._rng, 20)
        query = "&".join(f"{key}={value}" for key, value in params.items() if value is not None and str(value))
        combined = nonce + ecb_encrypt(f"{query}{stamp}", self._settings["dataKey"])
        return field(1, combined[:20]) + field(2, combined[20:]) + field(3, fake) + field(4, stamp) + field(5, nonce)

    async def _post(self, path: str, body: bytes) -> tuple[int, str, bytes]:
        began = time.monotonic()
        try:
            response = await self._http().post(self._host + path, content=body, headers=self.headers(),
                                               timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        if response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        try:
            code, message, data = answer_of(response.content)
        except (IndexError, struct.error) as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path)) from exc
        logger.info("приложение %s, %s: код %s %s (%.1f с)", self.profile.name, path, code, message,
                    time.monotonic() - began)
        return code, message, data

    async def _open(self) -> None:
        """The host the navigator names (the fallback when it names none) and the key of the zone."""
        async with self._opening:
            if self._zone:
                return
            self._host = await self._discover()
            stamp, nonce = self._stamp(), random_text(self._rng, 16)
            request = (field(1, stamp) + field(2, rsa_encrypt(f"{stamp}{nonce}", self._settings["publicKey"]))
                       + field(3, random_text(self._rng, 16)) + field(4, nonce) + field(5, random_text(self._rng, 16)))
            code, message, data = await self._post("/api/v5/find/app/zone", request)
            if code != SUCCESS:
                raise AppError(reason("app_refused", app=self.profile.label, step="zone", message=message[:300]))
            found = fields(data)
            self._zone = "".join(str(_first(found, number, "") or "") for number in (2, 3, 4, 5))

    async def _discover(self) -> str:
        try:
            response = await self._http().get(self._settings["host"], timeout=REQUEST_TIMEOUT_S)
            domain = response.json().get("domain") if response.status_code == 200 else None
        except (httpx.HTTPError, ValueError, AttributeError):
            domain = None
        return str(domain or self._settings["fallback"]).rstrip("/")

    async def call(self, path: str, params: dict) -> bytes:
        """One request in the session; a refusal is an error with the application's message."""
        await self._open()
        code, message, data = await self._post(path, self.body(params))
        if code != SUCCESS:
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=message[:300]))
        return data

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        """The card: its name and year, its lines in the order their first video comes, the episodes of each in the
        order they come, a token each (the VIP ones too: the application says itself whether it gives their stream)."""
        detail = detail_of(await self.call("/api/proto/v5/drama/getDetail", {"id": str(card_id)}))
        if not detail["videos"] and not detail["name"]:
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        lines: dict[str, list] = {}
        names: dict[str, str] = {}
        for video in detail["videos"]:
            code = video["source"]
            names.setdefault(code, video["source_cn"])
            items = lines.setdefault(code, [])
            items.append(AppItem(len(items) + 1, video["title"], video["path"]))
        year = str(detail["year"] or "").strip()
        return AppCardData([detail["name"]] if detail["name"] else [], "" if year == "0" else year,
                           [AppLine(code, names[code], False, "", tuple(items)) for code, items in lines.items()])

    async def resolve(self, line: str, token: str) -> AppStream:
        play = play_of(await self.call("/api/proto/v5/videoUsableUrl", {"vodPlayFrom": line, "playUrl": token}))
        if not play["play_url"].startswith(("http://", "https://")):
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=play["msg"][:300]))
        return AppStream(play["play_url"], play["headers"])

    async def search_page(self, query: str, page: int) -> AppPage:
        cards = page_of(await self.call("/api/proto/v5/drama/search", {"searchKeys": query, "page": str(page),
                                                                       "pagesize": str(SEARCH_PAGE)}))
        return AppPage([{**card, "year": str(card["year"] or ""), "sub": "", "class": ""} for card in cards])
