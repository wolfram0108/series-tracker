"""The API of the application 布布 (the YunDuo family): JSON over HTTP for the catalogue, one protobuf request for the
stream of an episode.

The base of the API is `api` of the profile, or `<host>/api.php/web`. Every request carries the agent of the client and
two headers the profile gives as they are (`X-Client`, `web-sign`). An answer of the catalogue is `{code, msg, data}`
with code 200 on success.

The search is `GET /search/index?wd=<words>&pg=<page>`: `data` lists the cards, `pageCount` and `total` count them. The
card is `GET /vod/get_detail?vod_id=<card>`: `data` is the card (or a list with it) with its lines in `vod_play_from`
(codes apart by `$$$`) and their episodes in `vod_play_url` (`<label>$<token>#...` for each line); `vodplayer` names the
lines (`from`, `show`).

The stream of an episode is `POST /decode/url` with a protobuf: 1 the token, 2 the code of the line, 3 the time in
milliseconds, 4 a nonce (16 random bytes, hex), 5 the signature, 6 `dev` of the profile, 7 the number 1. The signature is
SHA-256, in upper case, of `finger=<id>&id=<dev>&nonce=<nonce>&sk=<sk>&time=<time>&v=1`. The answer is a protobuf: 1 is 1
on success, 2 the words of the service, 3 the address. An address in a refusal is no stream. The stream is asked with the
agent of the client and the origin of its address as the referer.

The protocol follows the client of the application as the owner's prototype reads it (source-prototypes/chinese_sources,
sourcekit/yunduo.py); the vector of its signature is in the tests.
"""
from __future__ import annotations

import hashlib
import logging
import secrets
import time
from typing import Callable
from urllib.parse import urlsplit

import httpx

from core.reasons import reason

from . import AppCardData, AppError, AppLine, AppPage, AppProfile, AppStream, listed_lines
from .appdrama import field, fields

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "Mozilla/5.0"
PARSED = 1


def signature(settings: dict, stamp: int, nonce: str) -> str:
    said = f"finger={settings['id']}&id={settings['dev']}&nonce={nonce}&sk={settings['sk']}&time={stamp}&v=1"
    return hashlib.sha256(said.encode()).hexdigest().upper()


def stream_request(settings: dict, line: str, token: str, stamp: int, nonce: str) -> bytes:
    return b"".join((field(1, token), field(2, line), field(3, stamp), field(4, nonce),
                     field(5, signature(settings, stamp, nonce)), field(6, settings["dev"]), field(7, 1)))


def stream_answer(raw: bytes) -> tuple[int | None, str, str]:
    """(the code, the words, the address) of the answer about the stream of an episode."""
    found = dict(fields(raw))
    words, address = found.get(2, b""), found.get(3, b"")
    code = found.get(1)
    return (code if isinstance(code, int) else None, words.decode("utf-8", "replace") if isinstance(words, bytes) else "",
            address.decode("utf-8", "replace") if isinstance(address, bytes) else "")


class YunDuoClient:
    """The requests of one application of the family."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time, nonce: Callable[[], str] = lambda: secrets.token_hex(16)) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock, self._nonce = clock, nonce
        # The name of a line on its card -> the code the application knows it by, as the cards read gave them.
        self._codes: dict[str, str] = {}

    def _base(self) -> str:
        return (self._settings.get("api") or self._settings["host"].rstrip("/") + "/api.php/web").rstrip("/")

    def headers(self) -> dict[str, str]:
        return {"User-Agent": self._settings.get("ua") or AGENT, "X-Client": self._settings["x_client"],
                "web-sign": self._settings["web_sign"]}

    async def _asked(self, method: str, path: str, **sent) -> httpx.Response:
        began = time.monotonic()
        try:
            response = await self._http().request(method, self._base() + path, timeout=REQUEST_TIMEOUT_S,
                                                  follow_redirects=True, **sent)
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        logger.info("приложение %s, %s: статус %s (%.1f с)", self.profile.name, path, response.status_code,
                    time.monotonic() - began)
        if response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        return response

    async def call(self, path: str, params: dict[str, str]) -> dict:
        """An answer of the catalogue, whole: its `data` and what stands beside it."""
        response = await self._asked("GET", path, params=params, headers=self.headers())
        try:
            answer = response.json()
        except ValueError as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path)) from exc
        if not isinstance(answer, dict):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path))
        if str(answer.get("code")) != "200":
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=str(answer.get("msg") or "")[:300]))
        return answer

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        answer = await self.call("/vod/get_detail", {"vod_id": str(card_id)})
        record = answer.get("data")
        if isinstance(record, list):
            record = record[0] if record else None
        if not isinstance(record, dict) or not str(record.get("vod_play_from") or "").strip():
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
        body = stream_request(self._settings, self._codes.get(line, line), token, int(self._clock() * 1000), self._nonce())
        response = await self._asked("POST", "/decode/url", content=body, headers={
            **self.headers(), "Content-Type": "application/x-protobuf", "Accept": "application/x-protobuf"})
        try:
            said, words, address = stream_answer(response.content)
        except (ValueError, IndexError) as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="/decode/url")) from exc
        if said != PARSED or not address.startswith(("http://", "https://")):
            # No words is a failure of the way; a refusal in words is the answer of the service about the episode.
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=words[:300]),
                           transient=not words.strip())
        origin = urlsplit(address)
        return AppStream(address, {"User-Agent": self.headers()["User-Agent"], "Referer": f"{origin.scheme}://{origin.netloc}"})

    async def search_page(self, query: str, page: int) -> AppPage:
        answer = await self.call("/search/index", {"wd": query, "pg": str(page)})
        found = [each for each in answer.get("data") or [] if isinstance(each, dict)]
        pages, total = answer.get("pageCount"), answer.get("total")
        return AppPage([{"id": each.get("vod_id"), "name": str(each.get("vod_name") or ""), "sub": "",
                         "year": str(each.get("vod_year") or ""), "area": str(each.get("vod_area") or ""),
                         "class": str(each.get("vod_class") or ""), "remarks": str(each.get("vod_remarks") or ""),
                         "blurb": str(each.get("vod_blurb") or ""), "pic": str(each.get("vod_pic") or "")} for each in found],
                       int(pages) if str(pages).isdigit() else None, int(total) if str(total).isdigit() else None)
