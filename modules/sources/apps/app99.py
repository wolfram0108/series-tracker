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
import re
import secrets
import time
import zlib
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from core.reasons import reason

from ..players.http import retry_after

from . import (AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream, LEARNED_FOR_MIN_S, Lease, learned_for,
               session_ended, whoever_asks)

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 20.0
DEFAULT_AGENT = "okhttp/3.14.9"
# The size of a page of the search, as the application asks for it.
SEARCH_LIMIT = 50
# A card lists its lines apart by `$$$` and the episodes of a line apart by `#`.
LIST_SEPARATOR = "$$$"
ITEM_SEPARATOR = "#"
_DIRECT = re.compile(r"\.(m3u8|mp4)(?:$|\?)", re.I)


def parser_message(words: object) -> str:
    """A server-side parser error may contain a long signed URL before its useful refusal message."""
    return re.sub(r'''https?://[^\s`"'<>]+''', lambda match: urlsplit(match[0]).hostname or "[host]",
                  str(words or ""))[:500]


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
    """One application of the family: its session, opened once and again when the application refuses a request of it; the
    system of the application (its parsers) is asked again once its term is over, under the same guest."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient],
                 clock: Callable[[], float] = time.time) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http, self._clock = http, clock
        self._key = self._settings["uuid"].replace("-", "").encode()
        self._token = ""
        self._system: dict | None = None
        self._opening = asyncio.Lock()
        self._generation = 0
        self._system_lease = Lease(learned_for(self._settings), clock)

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
                                               headers=headers, timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=8.0))
        except httpx.HTTPError as exc:
            logger.info("приложение %s, %s: нет ответа — %s (%.1f с)", self.profile.name, path, type(exc).__name__,
                        time.monotonic() - began)
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        if response.status_code != 200:
            logger.info("приложение %s, %s: статус %d (%.1f с)", self.profile.name, path, response.status_code,
                        time.monotonic() - began)
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429,
                           status=response.status_code, retry_after=retry_after(response))
        try:
            answer = unseal(response.text, self._key)
        except (ValueError, UnicodeError, TypeError) as exc:
            logger.info("приложение %s, %s: ответ не расшифрован (%.1f с)", self.profile.name, path, time.monotonic() - began)
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path)) from exc
        logger.info("приложение %s, %s: код %s %s (%.1f с)", self.profile.name, path, answer.get("code"),
                    parser_message(answer.get("msg")), time.monotonic() - began)
        return answer

    def _system_request(self) -> dict:
        settings = self._settings
        return {"v": settings["versionName"], "n": settings["name"], "s": settings["buildSignature"], "pl": "1",
                "apiVersion": "v2"}

    async def _system_renewed(self) -> bool:
        """The system of the application asked again under the guest of the session. False when the application refused it
        (the session is then opened anew); an application that did not answer leaves the system as it was for a short term."""
        try:
            system = await self._post("/app/systemInit", self._system_request())
            self._refused(system, "/app/systemInit")
        except AppError as exc:
            if not exc.transient:
                return False
            logger.info("приложение %s: система не обновлена, остаётся прежняя — %s", self.profile.name, exc.reason)
            self._system_lease.renew(LEARNED_FOR_MIN_S)
            return True
        self._system = system
        self._system_lease.renew()
        return True

    async def _open(self) -> dict:
        """The session: the system of the application, then the guest login whose token goes with the requests."""
        async with self._opening:
            if self._system is not None and (self._system_lease.live or await self._system_renewed()):
                return self._system
            settings = self._settings
            self._token = ""
            system = await self._post("/app/systemInit", self._system_request())
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
            self._system_lease.renew()
            self._generation += 1
            return system

    def _refused(self, answer: dict, path: str) -> None:
        if answer.get("code") != 0:
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=parser_message(answer.get("msg"))))

    async def call(self, path: str, fields: dict, *, renew: bool = True) -> dict:
        """One request in the session. A refusal of a request of the catalog opens the session again once (a guest
        session ends at the application's will), and so does the end of the session said in so many words (HTTP 401, its
        code or its words) to a request of any kind: the same guest, the same UUID. A quota or a VIP check is no end of the
        session and opens nothing; a parser that refuses a link says so of the link (`renew` false): its refusal is the
        answer at once. The second refusal is the answer."""
        await self._open()
        generation = self._generation
        status = None
        try:
            answer = await self._post(path, fields)
        except AppError as exc:
            if exc.status != 401:
                raise
            answer, status = {}, 401  # HTTP 401: the token of the guest ended
        else:
            if answer.get("code") == 0:
                return answer
        words = str(answer.get("msg") or "")
        if not session_ended(answer.get("code"), words, status) and (not renew or whoever_asks(words)):
            self._refused(answer, path)
        logger.info("приложение %s: отказ на %s (%s) — запрос повторяется после проверки сессии", self.profile.name, path,
                    answer.get("msg") or status)
        # Several catalogue requests can be refused under the same expired session. A late refusal must not
        # discard the replacement another request has already opened, or each card starts another guest login.
        if self._generation == generation:
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
            # The parser answers with the code of success and no address: its refusal is in `msg`, often empty.
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=str(parser),
                                  message=parser_message(answer.get("msg"))))
        return address

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def _line_player(self, line: str) -> dict:
        players = (await self.system()).get("player") or {}
        found = next((each for key, each in players.items() if str(each.get("code") or key) == line), None)
        return found if isinstance(found, dict) else {}

    async def card(self, card_id: str) -> AppCardData:
        """The card: its names and year, its lines as `play_from` lists them (codes, apart by `$$$`) with their
        episodes from `play_url` (`<label>$<token>#...` for each line), each line with its name, type and parsers."""
        data = await self.detail(card_id)
        players = {str(each.get("code") or key): each for key, each in ((await self.system()).get("player") or {}).items()
                   if isinstance(each, dict)}
        codes = str(data.get("play_from") or "").split(LIST_SEPARATOR)
        lists = str(data.get("play_url") or "").split(LIST_SEPARATOR)
        lines = []
        for code, listed in zip(codes, lists):
            items = []
            for place, item in enumerate(listed.split(ITEM_SEPARATOR), start=1):
                label, _, token = item.partition("$")
                if token.strip():
                    items.append(AppItem(place, label.strip(), token.strip()))
            shown = players.get(code.strip(), {})
            lines.append(AppLine(code.strip(), str(shown.get("name") or ""), shown.get("type") == 0,
                                 str(shown.get("parseUrl") or ""), tuple(items)))
        names = [str(data.get("name") or "").strip(), *(part.strip() for part in str(data.get("sub") or "").split(","))]
        return AppCardData(list(dict.fromkeys(name for name in names if name)), str(data.get("year") or "").strip(), lines)

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        """The stream of an episode: the token itself for a line that lists its playlists, else what the parsers the
        line allows make of it, in the order the application lists its parsers, the first that gives an address."""
        player = await self._line_player(line)
        headers = _headers_of(player)
        if token.startswith(("http://", "https://")) and (player.get("type") == 0 or _DIRECT.search(token)):
            return AppStream(token, headers)
        parsers = [each.get("id") for each in (await self.system()).get("parser_api") or [] if isinstance(each, dict)]
        allowed = [part.strip() for part in str(player.get("parseUrl") or "").split(",") if part.strip()]
        refusals, errors = [], []
        for parser in [each for each in parsers if not allowed or str(each) in allowed]:
            try:
                return AppStream(await self.parse(parser, token), headers)
            except AppError as exc:
                logger.info("приложение %s, линия %s: парсер %s не дал адреса — %s", self.profile.name, line, parser,
                            exc.reason)
                if exc.transient:
                    raise
                refusals.append(str(parser))
                errors.append(exc)
        # Exhaustion of one parser must not hide a working alternative listed by this line. Only when all allowed
        # parsers explicitly exhausted their daily quota does the whole line carry that refusal.
        if errors and all(error.daily_quota for error in errors):
            raise errors[-1]
        raise AppError(reason("app_no_parser", app=self.profile.label, line=line, tried=", ".join(refusals) or "—",
                              **({"last_error": errors[-1].reason} if errors else {})))

    async def search_page(self, query: str, page: int) -> AppPage:
        answer = await self.search(query, page)
        pages, total = answer.get("page_count"), answer.get("total_count", answer.get("count"))
        return AppPage([each for each in answer.get("data") or [] if isinstance(each, dict)],
                       int(pages) if str(pages).isdigit() else None, int(total) if str(total).isdigit() else None)


def _headers_of(player: dict) -> dict[str, str]:
    """The headers the application's player of a line sends for its stream (its `headers`, a JSON object)."""
    raw = player.get("headers") or "{}"
    try:
        found = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return {}
    return {str(key): str(value) for key, value in found.items()} if isinstance(found, dict) else {}

