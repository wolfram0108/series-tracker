"""The API of the application 米诺 (the AppMN family): JSON over HTTP, a guest session for the stream of an episode.

Every request carries the agent of the client and the device of the profile (`X-Device-Id`); an answer is `{code, msg,
data}` with code 1 on success. The search (`GET /api/v1/search?wd=<words>&page=<page>`: `data.hits`, `total_pages`,
`total`) and the card (`GET /api/v1/video/detail?id=<card>`: `data.vod_info` with its lines in `vod_url_with_player`,
each `{code, name, url}` with the episodes of the line as `<label>$<token>#...`) are asked without a session.

The stream of an episode takes a session and two requests. The session is `POST /api/v1/security/init` with what the
client says of itself (the profile): `data.session_id` and `data.expires_in`, seconds; it is kept until shortly before
its end and opened anew then. `POST /api/v1/vod/ticket {token}` with `X-Session-Id` gives a ticket: its part before the
first dot is JSON in base64url, and its `data` names the parser of the episode (`parse_api`) and what to give it
(`url`). A ticket refused for a session that ended is asked again once under a new session. The parser is asked by
`parse_api` — relative to the host with `url` appended, or absolute with `url` in place of `{md5}` or appended — and
answers `{code, msg, url}`: an address in a refusal is no stream. Nothing of a ticket is changed, and no other device,
account or line is tried on a refusal (a quota of a parser is its answer about the episode).

The protocol follows the client of the application as the owner's prototype reads it (source-prototypes/chinese_sources,
sourcekit/appmn.py).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Callable
from urllib.parse import quote, urlsplit

import httpx

from core.reasons import reason

from . import AppCardData, AppError, AppLine, AppPage, AppProfile, AppStream, listed_lines, parsed_address

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "okhttp/4.12.0"
ACCEPTED = "1"
# A session is opened anew this long before its end, and no later than a tenth of its life before it.
SESSION_MARGIN_S = 30.0
# How the service says a ticket was asked under a session that ended (the client of the application reads the same).
_SESSION_ENDED = re.compile(r"session.{0,40}(?:expired|invalid|过期|无效)|(?:会话|会话ID).{0,20}(?:过期|无效)", re.I)


def ticket_data(ticket: str) -> dict:
    """What a ticket names for its episode: the JSON before its first dot, base64url, its `data`."""
    encoded = ticket.split(".", 1)[0]
    claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    if not isinstance(claims, dict) or not isinstance(claims.get("data"), dict):
        raise ValueError("a ticket without data")
    return claims["data"]


def parser_address(host: str, data: dict) -> str:
    """The address the parser of an episode is asked at, as its ticket says."""
    api, token = data.get("parse_api"), data.get("url")
    if not isinstance(api, str) or not isinstance(token, str) or not api or not token:
        raise ValueError("a ticket without a parser")
    api = api.replace("\\u00026", "&").replace("\x02", "")
    if api.startswith("/"):
        return host.rstrip("/") + api + quote(token, safe="")
    target = api.replace("{md5}", token) if "{md5}" in api else api + (quote(token, safe="") if "url=" in api else token)
    if urlsplit(target).scheme not in ("http", "https"):
        raise ValueError("a parser that is no address")
    return target


class AppMnClient:
    """The requests of one application of the family."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock = clock
        self._opening = asyncio.Lock()
        self._session: tuple[str, float] | None = None  # the session and the time it is used until

    def headers(self, session: str = "") -> dict[str, str]:
        return {"User-Agent": self._settings.get("ua") or AGENT, "X-Device-Id": self._settings["device_id"],
                **({"X-Session-Id": session} if session else {})}

    async def call(self, path: str, *, params: dict[str, str] | None = None, payload: dict | None = None,
                   session: str = "") -> dict:
        """An answer of the API as it is: `{code, msg, data}`; what its code means is the caller's to read."""
        began = time.monotonic()
        try:
            if payload is None:
                response = await self._http().get(self._settings["host"].rstrip("/") + path, params=params,
                                                  headers=self.headers(session), timeout=REQUEST_TIMEOUT_S)
            else:
                response = await self._http().post(self._settings["host"].rstrip("/") + path, json=payload,
                                                   headers=self.headers(session), timeout=REQUEST_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        try:
            answer = response.json()
        except ValueError:
            answer = None
        logger.info("приложение %s, %s: статус %s, код %s (%.1f с)", self.profile.name, path, response.status_code,
                    answer.get("code") if isinstance(answer, dict) else "—", time.monotonic() - began)
        if not isinstance(answer, dict):
            if response.status_code != 200:
                raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                               response.status_code >= 500 or response.status_code == 429)
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=path))
        return answer

    def _data(self, answer: dict, path: str):
        if str(answer.get("code")) != ACCEPTED:
            raise AppError(reason("app_refused", app=self.profile.label, step=path, message=str(answer.get("msg") or "")[:300]))
        return answer.get("data")

    async def session(self, *, after: str = "") -> str:
        """The session of the guest: the one held while it lasts, else a new one. `after` names a session the service
        said has ended: it is not given again."""
        async with self._opening:
            if self._session is not None and self._session[0] != after and self._session[1] > self._clock():
                return self._session[0]
            said = self._settings
            info = self._data(await self.call("/api/v1/security/init", payload={
                "platform": "android", "version": said.get("version", ""), "device_id": said["device_id"],
                "client_pub": said["client_pub"], "device_brand": said.get("device_brand", ""),
                "device_manufacturer": said.get("device_manufacturer", ""), "device_model": said.get("device_model", ""),
                "sig_src": said.get("sig_src", ""), "so_version": int(said["so_version"]) if said.get("so_version", "").isdigit() else 0,
            }), "/api/v1/security/init")
            if not isinstance(info, dict) or not info.get("session_id"):
                raise AppError(reason("app_answer_broken", app=self.profile.label, step="/api/v1/security/init"))
            life = float(info.get("expires_in") or 0)
            self._session = (str(info["session_id"]), self._clock() + max(0.0, life - min(SESSION_MARGIN_S, life / 10)))
            return self._session[0]

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        data = self._data(await self.call("/api/v1/video/detail", params={"id": str(card_id)}), "/api/v1/video/detail")
        record = data.get("vod_info") if isinstance(data, dict) else None
        groups = record.get("vod_url_with_player") if isinstance(record, dict) else None
        if not isinstance(groups, list):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        groups = [each for each in groups if isinstance(each, dict)]
        listed = listed_lines("$$$".join(str(each.get("code") or "") for each in groups),
                              "$$$".join(str(each.get("url") or "") for each in groups))
        shown = {str(each.get("code") or ""): str(each.get("name") or "") for each in groups}
        names = [str(record.get("vod_name") or "").strip(), *(part.strip() for part in str(record.get("vod_sub") or "").split(","))]
        year = str(record.get("vod_year") or "").strip()
        return AppCardData(list(dict.fromkeys(name for name in names if name)), "" if year == "0" else year,
                           [AppLine(name, shown.get(code, ""), False, "", items) for name, code, items in listed])

    async def _ticket(self, token: str) -> dict:
        session = await self.session()
        answer = await self.call("/api/v1/vod/ticket", payload={"token": token}, session=session)
        if _SESSION_ENDED.search(str(answer.get("msg") or "")):
            answer = await self.call("/api/v1/vod/ticket", payload={"token": token}, session=await self.session(after=session))
        if str(answer.get("code")) != ACCEPTED:
            words = str(answer.get("msg") or "")
            raise AppError(reason("app_no_stream", app=self.profile.label, parser="ticket", message=words[:300]),
                           transient=not words.strip())
        try:
            return ticket_data(str((answer.get("data") or {}).get("ticket") or ""))
        except (ValueError, AttributeError, TypeError) as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="/api/v1/vod/ticket")) from exc

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        try:
            address = parser_address(self._settings["host"], await self._ticket(token))
        except ValueError as exc:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="/api/v1/vod/ticket")) from exc
        began = time.monotonic()
        try:
            response = await self._http().get(address, headers=self.headers(), timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        try:
            said = response.json()
        except ValueError:
            said = None
        target, words = parsed_address(said)
        logger.info("приложение %s, парсер линии %s: статус %s, %s (%.1f с)", self.profile.name, line, response.status_code,
                    "адрес" if target else f"без адреса: {words[:120] or '—'}", time.monotonic() - began)
        if target and response.status_code == 200:
            return AppStream(target, {"User-Agent": self.headers()["User-Agent"]})
        if not words.strip() and response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=words[:300]),
                       transient=not words.strip())

    async def search_page(self, query: str, page: int) -> AppPage:
        data = self._data(await self.call("/api/v1/search", params={"wd": query, "page": str(page)}), "/api/v1/search")
        found = [each for each in (data.get("hits") if isinstance(data, dict) else None) or [] if isinstance(each, dict)]
        pages, total = (data.get("total_pages"), data.get("total")) if isinstance(data, dict) else (None, None)
        return AppPage([{"id": each.get("vod_id"), "name": str(each.get("vod_name") or ""),
                         "sub": str(each.get("vod_sub") or ""), "year": str(each.get("vod_year") or ""),
                         "area": str(each.get("vod_area") or ""), "class": str(each.get("vod_class") or ""),
                         "remarks": str(each.get("vod_remarks") or ""), "blurb": str(each.get("vod_blurb") or ""),
                         "pic": str(each.get("vod_pic") or "")} for each in found],
                       int(pages) if str(pages).isdigit() else None, int(total) if str(total).isdigit() else None)
