"""The API of the applications of the AppGet family (茉莉): JSON over HTTP, the answers AES-CBC under the keys of the profile.

The hosts of the API are what the published list of the application says (`site`, one address a line) or the one host
of the profile (`url`); a host that does not answer gives way to the next. A request is `POST
<host>/api.php/getappapi.index/<method>` with the fields as JSON and headers that describe the device and the time; an
answer is `{code, msg, data}` with `data` AES-CBC (PKCS#7, base64) under `dataKey` and `dataIv`. Code 1001 asks a person
to pass a check: the application is then not read.

The card of a title (`vodDetail`) lists its lines (`vod_play_list`), each with its player (`player_info`: `show`, its
name; `parse`, the address of its parser) and its episodes (`urls`: `name`, `url` — the token of the episode or the
playlist itself —, `from`, the code of the line, and `parse_api_url`, the address that gives the stream of the episode).
Two lines of a card may have one code (a line through a parser and the same resource without one): the second is named
`<code>.2`. The stream of an episode is asked at its `parse_api_url`: a playlist or a file by its form, else the answer
of the parser — the playlist itself, or JSON that names it (`url`, or `data.url`). An episode whose address is no address
is parsed by the application (`vodParse`, signed by the time under the keys).

The token of an episode is its `url`; the address of its parser is kept with the card it was read with and asked of the
card again when the client no longer holds it. The codec follows the client of the application as the owner's prototype
reads it (source-prototypes/chinese_sources, sourcekit/client.py, QijiSource); its vectors are in the tests.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Callable
from urllib.parse import quote_plus, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7

from core.reasons import reason

from . import AppCardData, AppError, AppItem, AppLine, AppPage, AppProfile, AppStream, advertised_headers, parsed_address

logger = logging.getLogger("sources")

REQUEST_TIMEOUT_S = 30.0
AGENT = "okhttp/3.14.9"
PREFIX = "/api.php/getappapi.index/"
CAPTCHA = 1001
# The cards whose parsers the client holds: the ones being read or downloaded now, not every card ever read.
CARDS_HELD = 16
_MEDIA = re.compile(r"\.(m3u8|mp4|mkv)$", re.I)


def encrypt(text: str, key: str, iv: str) -> str:
    """AES-CBC under the key, PKCS#7, base64."""
    padder = PKCS7(128).padder()
    padded = padder.update(text.encode()) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def decrypt(blob: str, key: str, iv: str) -> str:
    decryptor = Cipher(algorithms.AES(key.encode()), modes.CBC(iv.encode())).decryptor()
    padded = decryptor.update(base64.b64decode(blob)) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return (unpadder.update(padded) + unpadder.finalize()).decode("utf-8")


def lines_of(detail: dict) -> list[tuple[AppLine, dict]]:
    """The lines of a card in its order, each with what the stream of its episodes is asked with: the parser of the line
    (`parse`: an address, or the name the application parses by) and, by the token of each episode, the address of its
    parser and its own token. A code a card gives twice names its second line `<code>.2` (and so on): a line is one of
    its card by its code."""
    found, seen = [], {}
    for group in detail.get("vod_play_list") or []:
        info = group.get("player_info") or {}
        urls = [each for each in group.get("urls") or [] if isinstance(each, dict) and str(each.get("url") or "").strip()]
        codes = {str(each["from"]) for each in urls if each.get("from")}
        code = str(info.get("from") or group.get("from") or (next(iter(codes)) if len(codes) == 1 else "") or info.get("id") or "")
        if not code or not urls:
            continue
        seen[code] = seen.get(code, 0) + 1
        name = code if seen[code] == 1 else f"{code}.{seen[code]}"
        items = tuple(AppItem(place, str(each.get("name") or each.get("title") or "").strip(), str(each["url"]).strip())
                      for place, each in enumerate(urls, start=1))
        parse = str(info.get("parse") or "").strip()
        asked = {"parse": parse, "headers": advertised_headers(info), "episodes": {str(each["url"]).strip(): (str(each.get("parse_api_url") or "").strip(),
                                                                       str(each.get("token") or "")) for each in urls}}
        found.append((AppLine(name, str(info.get("show") or ""), not parse, "", items), asked))
    return found


class AppGetClient:
    """The requests of one application of the family."""

    def __init__(self, profile: AppProfile, http: Callable[[], httpx.AsyncClient], *,
                 clock: Callable[[], float] = time.time) -> None:
        self.profile = profile
        self._settings = profile.settings
        self._http = http
        self._clock = clock
        self._hosts: list[str] = []
        self._finding = asyncio.Lock()
        # card -> line -> what the stream of its episodes is asked with (`lines_of`), as the card gave it.
        self._parsers: OrderedDict[str, dict[str, dict]] = OrderedDict()

    # --- the request ------------------------------------------------------------------------------------------------

    def headers(self, signed: bool = False) -> dict[str, str]:
        stamp = str(int(self._clock()))
        headers = {"User-Agent": self._settings.get("ua") or AGENT,
                   "Content-Type": "application/x-www-form-urlencoded" if signed else "application/json; charset=utf-8",
                   "app-user-device-id": self._settings.get("deviceId", ""),
                   "app-version-code": self._settings.get("version", ""),
                   "app-ui-mode": "light", "app-api-verify-time": stamp}
        if self._settings.get("token"):
            headers["app-user-token"] = self._settings["token"]
        if signed:
            headers["app-api-verify-sign"] = encrypt(stamp, self._settings["dataKey"], self._settings["dataIv"])
        return headers

    async def _found(self) -> list[str]:
        """The hosts of the API: the one of the profile, or the list the application publishes."""
        async with self._finding:
            if self._hosts:
                return self._hosts
            if self._settings.get("url"):
                listed = [self._settings["url"]]
            else:
                try:
                    response = await self._http().get(self._settings["site"], timeout=REQUEST_TIMEOUT_S)
                except httpx.HTTPError as exc:
                    raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
                listed = response.content.decode("utf-8-sig", "replace").splitlines() if response.status_code == 200 else []
            hosts = [each.strip().rstrip("/") for each in listed if each.strip().startswith(("http://", "https://"))]
            if not hosts:
                raise AppError(reason("app_unreachable", app=self.profile.label, error="no host listed"), True)
            self._hosts = hosts
            return hosts

    async def call(self, method: str, payload: dict | str, *, signed: bool = False) -> dict:
        """One request; the answer decrypted. A host that does not answer gives way to the next one listed; a refusal
        of the application is an error with its message."""
        body = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        failure: AppError | None = None
        for host in await self._found():
            began = time.monotonic()
            try:
                response = await self._http().post(host + PREFIX + method, content=body.encode(),
                                                   headers=self.headers(signed), timeout=REQUEST_TIMEOUT_S)
            except httpx.HTTPError as exc:
                failure = AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True)
                continue
            if response.status_code != 200:
                failure = AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                                   response.status_code >= 500 or response.status_code == 429)
                if failure.transient:
                    continue
                raise failure
            try:
                envelope = response.json()
                code, message, data = envelope.get("code"), str(envelope.get("msg") or envelope.get("message") or ""), envelope.get("data")
            except (ValueError, AttributeError) as exc:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step=method)) from exc
            logger.info("приложение %s, %s: код %s %s (%.1f с)", self.profile.name, method, code, message[:80],
                        time.monotonic() - began)
            if code == CAPTCHA:
                raise AppError(reason("app_refused", app=self.profile.label, step=method, message="captcha required"))
            if not isinstance(data, str) or not data:
                raise AppError(reason("app_refused", app=self.profile.label, step=method, message=message[:300]))
            try:
                return json.loads(decrypt(data, self._settings["dataKey"], self._settings["dataIv"]))
            except (ValueError, TypeError) as exc:
                raise AppError(reason("app_answer_broken", app=self.profile.label, step=method)) from exc
        raise failure or AppError(reason("app_unreachable", app=self.profile.label, error="no host"), True)

    # --- what every protocol gives (apps/__init__.py) -------------------------------------------------------------

    async def _detail(self, card_id: str) -> tuple[dict, list[tuple[AppLine, dict]]]:
        detail = await self.call("vodDetail", {"vod_id": str(card_id)})
        if not isinstance(detail.get("vod"), dict) or not isinstance(detail.get("vod_play_list"), list):
            raise AppError(reason("app_title_absent", app=self.profile.label, card=str(card_id)))
        found = lines_of(detail)
        self._parsers[str(card_id)] = {line.code: asked for line, asked in found}
        self._parsers.move_to_end(str(card_id))
        while len(self._parsers) > CARDS_HELD:
            self._parsers.popitem(last=False)
        return detail["vod"], found

    async def card(self, card_id: str) -> AppCardData:
        vod, found = await self._detail(card_id)
        names = [str(vod.get("vod_name") or "").strip(), *(part.strip() for part in str(vod.get("vod_sub") or "").split(","))]
        year = str(vod.get("vod_year") or "").strip()
        return AppCardData(list(dict.fromkeys(name for name in names if name)), "" if year == "0" else year,
                           [line for line, _parsers in found])

    async def _asked_with(self, line: str, token: str, card: str) -> tuple[str, str, str, dict[str, str]]:
        """(the parser of the line, the address of the parser of the episode, the token of the episode, the headers the
        player of the line names for its stream), as the card of the episode says them: held from the read of the card,
        else the card is asked again."""
        held = self._parsers.get(str(card), {}).get(line)
        if (held is None or token not in held["episodes"]) and card:
            await self._detail(card)
            held = self._parsers.get(str(card), {}).get(line)
        if held is None:
            return "", "", "", {}
        address, own = held["episodes"].get(token, ("", ""))
        return held["parse"], address, own, held["headers"]

    async def resolve(self, line: str, token: str, card: str = "") -> AppStream:
        agent = {"User-Agent": self._settings.get("ua") or AGENT}
        parse, address, own, player = await self._asked_with(line, token, card)
        address = address or token
        if address.startswith(("http://", "https://")):
            # A playlist or a file by the path of the address is the stream; any other address is a parser, asked. The
            # stream is asked with what the player of the line names for it.
            if _MEDIA.search(urlsplit(address).path):
                return AppStream(address, {**agent, **player})
            return await self._parsed(line, address, agent, player)
        # No address of a parser: the application parses the episode itself, by the name of the parser of its line.
        asked = (f"parse_api={parse}&url="
                 f"{quote_plus(encrypt(token, self._settings['dataKey'], self._settings['dataIv']))}&token={own}")
        answer = (await self.call("vodParse", asked, signed=True)).get("json")
        try:
            answer = json.loads(answer) if isinstance(answer, str) else answer
        except ValueError:
            answer = None
        target, words = parsed_address(answer)
        if not target:
            # No address and no words is a failure of the way; a refusal in words is the parser's answer about the episode.
            raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=words[:300]),
                           transient=not words.strip())
        return AppStream(target, {**player, **advertised_headers(answer)})

    async def _parsed(self, line: str, address: str, agent: dict[str, str], player: dict[str, str]) -> AppStream:
        """What the parser of an episode answers: the playlist itself (the address is then the stream), or JSON that
        names the stream. A refusal in words is the parser's answer about the episode; none at all is a failure of the
        way."""
        began = time.monotonic()
        try:
            response = await self._http().get(address, headers=agent, timeout=REQUEST_TIMEOUT_S, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise AppError(reason("app_unreachable", app=self.profile.label, error=type(exc).__name__), True) from exc
        head = response.content[:64].lstrip(b"\xef\xbb\xbf").lstrip()
        logger.info("приложение %s, парсер линии %s: статус %s, %d байт (%.1f с)", self.profile.name, line,
                    response.status_code, len(response.content), time.monotonic() - began)
        if response.status_code == 200 and head.startswith(b"#EXTM3U"):
            return AppStream(address, {**agent, **player})
        try:
            said = response.json()
        except ValueError:
            said = None
        target, message = parsed_address(said) if response.status_code == 200 else ("", "")
        if target:
            # The stream a parser names is asked as the player of the line and the parser say: the agent of the
            # application is of its API (a CDN of a stream refuses it for the segments: 403).
            return AppStream(target, {**player, **advertised_headers(said)})
        if response.status_code != 200:
            raise AppError(reason("app_status", app=self.profile.label, status=response.status_code),
                           response.status_code >= 500 or response.status_code == 429)
        raise AppError(reason("app_no_stream", app=self.profile.label, parser=line, message=message[:300]),
                       transient=not message.strip())

    async def search_page(self, query: str, page: int) -> AppPage:
        found = await self.call(self._settings.get("search") or "searchList", {"type_id": 0, "keywords": query, "page": page})
        listed = found.get("search_list")
        if not isinstance(listed, list):
            raise AppError(reason("app_answer_broken", app=self.profile.label, step="searchList"))
        return AppPage([{"id": each.get("vod_id"), "name": str(each.get("vod_name") or ""), "sub": "",
                         "year": str(each.get("vod_year") or ""), "area": str(each.get("vod_area") or ""),
                         "class": str(each.get("type_name") or ""), "remarks": str(each.get("vod_remarks") or ""),
                         "blurb": str(each.get("vod_blurb") or ""), "pic": str(each.get("vod_pic") or "")}
                        for each in listed if isinstance(each, dict)])
