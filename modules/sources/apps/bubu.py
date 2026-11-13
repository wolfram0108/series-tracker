"""The API of the Android client of 布布 (Bubu, 1.6.1), which is not the web client of YunDuo (apps/yunduo.py): GET requests whose
headers carry the build and a signature, and answers AES-GCM sealed under one of the keys of the build.

A request is `GET <host>/api.php/app<path>` with `x-aid`, `x-ave`, `x-avn`, the time and a nonce, signed by SHA-256 (hex,
upper case) of `finger=…&id=…&nonce=…&sk=…&time=…&v=…`; the nonce is the first eight letters of the SHA-256 of the device
and eight random digits, the device a guest the profile names. An answer is the nonce of 12 bytes, then the ciphertext
and its tag; the number of the key among those of the build (`response_keys`) comes in the header `mcg821-a`. Inside is
JSON `{code, msg, data}`: code 200 for the search and the card, 1 for the stream of an episode.

The card is `/vod/get_detail` (`vod_play_from` the codes of the lines apart by `$$$`, `vod_play_url` their episodes as
`<label>$<token>#...`); the stream of an episode is `/decode/url/` with its token and the code of its line.

The message LOGIN_USERNAME_EMPTY of the service was seen for a request signed by a profile of a repackaged build, not for a
missing account: it is shown as the refusal it is and never makes the client try another device or profile.

The protocol follows the client of the owner's prototype (source-prototypes/chinese_sources, sourcekit/bubu.py).
"""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
import uuid
from typing import Callable

import httpx
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from core.reasons import reason

from ..players.http import retry_after

from . import AppCardData, AppError, AppLine, AppPage, AppProfile, AppStream, listed_lines, parsed_address

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "okhttp/4.12.0"
PLAYBACK_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/151.0.0.0 Safari/537.36")
KEY_HEADER = "mcg821-a"
SEARCH_LIMIT = 15
CATALOG, STREAM = "200", "1"


class BubuClient:
    """The requests of the Android client of the application."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time,
                 nonce: Callable[[], str] = lambda: "".join(secrets.choice("0123456789") for _ in range(8))) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock, self._nonce = clock, nonce
        # The device of a guest is made once and kept by the profile (`device_id`); without one the process holds its own.
        self._device = self._settings.get("device_id") or str(uuid.uuid4())
        self._keys: list[str] = json.loads(self._settings.get("response_keys") or "[]")
        # The name of a line on its card -> the code the application knows it by, as the cards read gave them.
        self._codes: dict[str, str] = {}

    # --- the wire ---------------------------------------------------------------------------------------------

    def headers(self) -> dict[str, str]:
        said = self._settings
        stamp = int(self._clock() * 1000)
        nonce = hashlib.sha256(self._device.encode()).hexdigest()[:8].upper() + self._nonce()
        signed = f"finger={said['finger']}&id={said['id']}&nonce={nonce}&sk={said['sk']}&time={stamp}&v={said['v']}"
        return {"User-Agent": said.get("ua") or AGENT, "Accept": "application/json", "Accept-Encoding": "gzip",
                "x-aid": said["id"], "x-ave": str(said["v"]), "x-avn": said["avn"], "x-time": str(stamp), "x-nonc": nonce,
                "x-sign": hashlib.sha256(signed.encode()).hexdigest().upper(), "x-device-id": self._device,
                "x-device-brand": said.get("device_brand") or "unknown", "x-device-model": said.get("device_model") or "unknown",
                "x-update-id": said.get("update_id") or "embedded"}

    def _opened(self, response: httpx.Response, path: str) -> dict:
        """The JSON an answer seals, or the failure of opening it: the key is the one the header numbers."""
        number = response.headers.get(KEY_HEADER, "").strip()
        body = response.content
        try:
            if not number.isdigit() or int(number) >= len(self._keys) or len(body) < 28:
                raise ValueError("no key or no sealed text")
            key = bytes.fromhex(self._keys[int(number)])
            if len(key) != 16:
                raise ValueError("a key of another length")
            payload = json.loads(AESGCM(key).decrypt(body[:12], body[12:], None))
        except (ValueError, UnicodeError, TypeError, InvalidTag) as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path)) from exc
        if not isinstance(payload, dict):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path))
        return payload

    async def _asked(self, path: str, params: dict) -> tuple[int, dict]:
        """(the status, the opened answer) of a request; a refusal by status that carries no sealed answer is an error."""
        began = time.monotonic()
        try:
            response = await self._http().get(self._settings["host"].rstrip("/") + "/api.php/app" + path, params=params,
                                              headers=self.headers(), timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=8.0))
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        logger.info("приложение %s, %s: статус %s (%.1f с)", self.profile.name, path, response.status_code, time.monotonic() - began)
        if response.status_code >= 400 and KEY_HEADER not in response.headers:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429,
                           status=response.status_code, retry_after=retry_after(response))
        return response.status_code, self._opened(response, path)

    async def call(self, path: str, params: dict) -> dict:
        """An answer of the catalogue, whole: its code is that of the catalogue."""
        status, answer = await self._asked(path, params)
        if str(answer.get("code")) != CATALOG or status >= 400:
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=str(answer.get("msg") or "")[:300]))
        return answer

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        answer = await self.call("/vod/get_detail", {"vod_id": str(card_id)})
        record = answer.get("data")
        if isinstance(record, list):
            record = record[0] if record else None
        if not isinstance(record, dict) or str(record.get("vod_id")) != str(card_id) \
                or not str(record.get("vod_play_from") or "").strip():
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        shown = {str(each.get("from")): str(each.get("show") or "") for each in answer.get("vodplayer") or []
                 if isinstance(each, dict)}
        names = [str(record.get("vod_name") or "").strip(), *(part.strip() for part in str(record.get("vod_sub") or "").split(","))]
        year = str(record.get("vod_year") or "").strip()
        listed = listed_lines(str(record.get("vod_play_from") or ""), str(record.get("vod_play_url") or ""))
        self._codes.update({name: code for name, code, _items in listed})
        lines = [AppLine(name, shown.get(code, ""), False, "", items) for name, code, items in listed]
        return AppCardData(list(dict.fromkeys(name for name in names if name)), "" if year == "0" else year, lines)

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        # A line is asked by the code the application knows it by; a client that no longer holds the card asks it again.
        if line not in self._codes and card:
            await self.card(card)
        status, answer = await self._asked("/decode/url/", {"url": token, "vodFrom": self._codes.get(line, line),
                                                            "_t": int(self._clock() * 1000)})
        target, words = parsed_address(answer)
        if target and status < 400 and str(answer.get("code")) == STREAM:
            return AppStream(target, {"User-Agent": self._settings.get("playback_ua") or PLAYBACK_AGENT})
        # No words is a failure of the way; a refusal in words is the answer of the service about the episode.
        raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=words[:300]),
                       transient=not words.strip())

    async def search_page(self, query: str, page: int) -> AppPage:
        answer = await self.call("/search/index", {"wd": query, "page": page, "limit": SEARCH_LIMIT})
        found = answer.get("data")
        if not isinstance(found, list):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="/search/index"))
        pages, total = answer.get("pageCount"), answer.get("total")
        return AppPage([{"id": each.get("vod_id"), "name": str(each.get("vod_name") or ""), "sub": "",
                         "year": str(each.get("vod_year") or ""), "area": str(each.get("vod_area") or ""),
                         "class": str(each.get("vod_class") or ""), "remarks": str(each.get("vod_remarks") or ""),
                         "blurb": str(each.get("vod_blurb") or ""), "pic": str(each.get("vod_pic") or "")}
                        for each in found if isinstance(each, dict)],
                       int(pages) if str(pages).isdigit() else None, int(total) if str(total).isdigit() else None)
