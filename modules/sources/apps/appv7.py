"""The API of the applications of the AppV7 family (蒙太奇 Mtq, 小猪 Xiaozhu): one POST address, a JSON request sealed by
AES-CBC, a card whose lines each carry the way their stream is asked.

A request is `{method, data, csrf}`, sealed under the key `MD5(build_time + salt + package)` with a random IV that opens
the wire (`base64(IV + ciphertext)`), and goes with the headers of the build the profile names. An answer is JSON or the
container the client of the application unpacks (a run of numbers hiding the head of the sealed text); it is `{code,
msg, data}` with code 1 on success. `search` takes `{token, pg, text}`, `video_detail` takes `{token, id}` and the
identity of the card the profile keeps; the card lists its lines in `vod_url_with_player`, each `{code, name, url,
parse_api, parse_secret, headers}` with the episodes as `<label>$<token>#...`.

The stream of an episode is its token when the line has no parser and the token is an address; else the parser of the
line is asked with the token (a line that is `parse_secret` first has `secret_parse_api_url` seal the token) and answers
`{code, msg, url}`: an address in a refusal is no stream. The parser belongs to the line of the card, so the card read
last is kept for the resolve of its rows, for a term, and asked again when its term is over or a row's line is not in it. A
token of another application's episode (apps/routes.json) comes with no card: the parser of its line is the one the profile's
`resolver_card` (a card of this application that lists the line) gave last, asked again when its term is over.

The protocol follows the client of the application as the owner's prototype reads it (source-prototypes/chinese_sources,
sourcekit/mtq.py and sourcekit/appv7.py).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from typing import Callable
from urllib.parse import quote

import httpx

from core.reasons import reason

from ..players.http import retry_after

from . import (AppCardData, AppError, AppLine, AppPage, AppProfile, AppStream, LEARNED_FOR_MIN_S, Lease, advertised_headers,
               learned_for, listed_lines, parsed_address)
from .gulu import cbc

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "Dart/3.11 (dart:io)"
ACCEPTED = "1"
# The marker the client of the application finds the end of a container by (base64 of its own name for the key).
CONTAINER_MARK = "eGRkYXBwc2VjcmV0a2V5"
# The cards whose players are kept for the resolve of their rows.
CARDS_KEPT = 256
_SECRET = (True, 1, "1", "true")


def api_key(settings: dict) -> bytes:
    return hashlib.md5((settings["build_time"] + settings["salt"] + settings["package"]).encode()).digest()


def seal(settings: dict, body: dict, iv: bytes) -> bytes:
    """The wire of a request: the IV and the sealed body, base64."""
    raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.b64encode(iv + cbc(raw, api_key(settings), iv))


def unseal(settings: dict, text: str) -> object:
    """What an answer says: the JSON of it, or the JSON a container of the family seals."""
    whole, text = text, "".join(text.split())
    at = text.rfind(CONTAINER_MARK)
    if at < 0:
        # Plain JSON keeps its spaces: only a container is a run of characters that line breaks may cut.
        return json.loads(whole)
    length = int(base64.b64decode(text[at + len(CONTAINER_MARK):] + "==="))
    numbers = [int(each) for each in re.findall(r"0x(\d+?)(?=0x|$|[^\d])", base64.b64decode(text[:length] + "===").decode())]
    raw = bytes((value - (11 if place % 2 == 0 else 4 if place % 3 == 0 else 2 if place % 5 == 0 else 3)) & 255
                for place, value in enumerate(reversed(numbers)))
    raw += base64.b64decode(text[length:at] + "===")
    return json.loads(cbc(raw[16:], api_key(settings), raw[:16], decrypt=True))


class AppV7Client:
    """The requests of one application of the family."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time, iv: Callable[[], bytes] = lambda: os.urandom(16)) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock, self._iv = clock, iv
        # card -> (until when it is believed, the name of a line on it -> its player as the card gave it), the latest cards.
        self._players: OrderedDict[str, tuple[float, dict[str, dict]]] = OrderedDict()
        self._players_for = learned_for(self._settings)
        self._resolver_lease = Lease(self._players_for, clock)
        # line -> its player as the application published it last, on any card (the profile gives the first one): the
        # parser of a line is the application's own, so the token of another application's episode of the line is asked
        # of it without a card (apps/routes.json).
        self._lines: dict[str, dict] = dict(json.loads(self._settings.get("parsers") or "{}"))

    def _status(self, response: httpx.Response, words: str = "") -> AppError:
        """A refusal by status keeps what the application said with it: a daily quota is told by those words."""
        return AppError(reason("app_status", app=self.profile.label, status=response.status_code,
                               **({"message": words[:300]} if words.strip() else {})),
                        response.status_code >= 500 or response.status_code == 429,
                        status=response.status_code, retry_after=retry_after(response))

    async def call(self, method: str, data: dict) -> object:
        """The `data` of a successful answer of the API to `method`."""
        said = self._settings
        headers = {"User-Agent": said.get("ua") or AGENT, "version": said["version"], "version-number": said["version_number"],
                   "pk-id": said["package"], "build-time": said["build_time"], "platform": said.get("platform") or "android",
                   "platform-version": said["platform_version"], "Content-Type": "text/plain"}
        wire = seal(said, {"method": method, "data": data, "csrf": int(self._clock() * 1000)}, self._iv())
        began = time.monotonic()
        try:
            response = await self._http().post(said["api"], content=wire, headers=headers,
                                               timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=8.0))
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        logger.info("приложение %s, %s: статус %s (%.1f с)", self.profile.name, method, response.status_code, time.monotonic() - began)
        if response.status_code != 200:
            raise self._status(response)
        try:
            answer = unseal(said, response.text)
        except (ValueError, TypeError, UnicodeError, IndexError):
            answer = None
        if not isinstance(answer, dict):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step=method))
        if str(answer.get("code")) != ACCEPTED:
            raise AppError(reason("app_refused", app=self.profile.label, step=method, message=str(answer.get("msg") or "")[:300]))
        return answer.get("data")

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def card(self, card_id: str) -> AppCardData:
        identity = json.loads(self._settings.get("card_identity") or "{}")
        data = await self.call("video_detail", {"token": "", "id": int(card_id), **identity})
        record = data.get("vod_info") if isinstance(data, dict) else None
        groups = record.get("vod_url_with_player") if isinstance(record, dict) else None
        if not isinstance(groups, list) or str(record.get("vod_id")) != str(card_id):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        groups = [each for each in groups if isinstance(each, dict) and each.get("code")]
        listed = listed_lines("$$$".join(str(each["code"]) for each in groups), "$$$".join(str(each.get("url") or "") for each in groups))
        # `listed_lines` leaves out a line without an episode and names a repeated code `<code>.2`: the groups that stay are
        # those it kept, in its order.
        kept = [each for each in groups if listed_lines(str(each["code"]), str(each.get("url") or ""))]
        players = {name: {k: v for k, v in group.items() if k != "url"} for (name, _code, _items), group in zip(listed, kept)}
        self._players[str(card_id)] = (self._clock() + self._players_for, players)
        self._lines.update(players)
        self._players.move_to_end(str(card_id))
        while len(self._players) > CARDS_KEPT:
            self._players.popitem(last=False)
        names = [str(record.get("vod_name") or "").strip(), *(part.strip() for part in str(record.get("vod_sub") or "").split(","))]
        year = str(record.get("vod_year") or "").strip()
        return AppCardData(list(dict.fromkeys(name for name in names if name)), "" if year == "0" else year,
                           [AppLine(name, str(players[name].get("name") or ""), not self._parser(players[name]), "", items)
                            for name, _code, items in listed])

    @staticmethod
    def _parser(player: dict) -> str:
        address = player.get("parse_api")
        return address if isinstance(address, str) and address.startswith(("http://", "https://")) else ""

    def _held(self, card: str) -> dict[str, dict]:
        until, found = self._players.get(str(card), (0.0, {}))
        return found if self._clock() < until else {}

    async def _resolver_renewed(self) -> None:
        """The parsers the lines go by without a card of their own, as the card the profile names for it gives them now; a
        card that cannot be read leaves the parsers held, to be asked again soon."""
        try:
            await self.card(str(self._settings["resolver_card"]))
        except AppError as exc:
            logger.info("приложение %s: карточка разборщиков не прочитана, остаются прежние — %s", self.profile.name, exc.reason)
            self._resolver_lease.renew(LEARNED_FOR_MIN_S)
            return
        self._resolver_lease.renew()

    async def _player(self, line: str, card: str) -> dict:
        player = self._held(card).get(line) if card else None
        if player is None and card:
            await self.card(card)
            player = self._held(card).get(line)
        if player is None:
            if self._settings.get("resolver_card") and not self._resolver_lease.live:
                await self._resolver_renewed()
            player = self._lines.get(line)
        if player is None:
            raise AppError(reason("app_no_parser", app=self.profile.label, line=line, tried="—"))
        return player

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        player = await self._player(line, card)
        parser = self._parser(player)
        if not parser:
            # A line without a parser lists the addresses of its streams themselves.
            if token.startswith(("http://", "https://")):
                return AppStream(token, advertised_headers(player))
            raise AppError(reason("app_no_parser", app=self.profile.label, line=line, tried="—"))
        item = token
        if player.get("parse_secret") in _SECRET:
            item = await self.call("secret_parse_api_url", {"token": "", "url": token, "player_code": player.get("code")})
            if not isinstance(item, str) or not item:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step="secret_parse_api_url"))
        began = time.monotonic()
        try:
            response = await self._http().get(parser + quote(item, safe=""), headers={
                "User-Agent": self._settings.get("parser_agent") or AGENT, "Accept": "*/*"},
                timeout=httpx.Timeout(REQUEST_TIMEOUT_S, connect=8.0), follow_redirects=True)
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
            inner = said.get("data") if isinstance(said.get("data"), dict) else {}
            return AppStream(target, {**advertised_headers(player), **advertised_headers(inner), **advertised_headers(said)})
        if response.status_code != 200:
            raise self._status(response, words)
        raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=words[:300]),
                       transient=not words.strip())

    async def search_page(self, query: str, page: int) -> AppPage:
        data = await self.call("search", {"token": "", "pg": page, "text": query})
        found = [each for each in data if isinstance(each, dict)] if isinstance(data, list) else None
        if found is None:
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="search"))
        return AppPage([{"id": each.get("vod_id"), "name": str(each.get("vod_name") or ""),
                         "sub": str(each.get("vod_sub") or ""), "year": str(each.get("vod_year") or ""),
                         "area": str(each.get("vod_area") or ""), "class": str(each.get("vod_class") or ""),
                         "remarks": str(each.get("vod_remarks") or ""), "blurb": str(each.get("vod_blurb") or ""),
                         "pic": str(each.get("vod_pic") or "")} for each in found])
