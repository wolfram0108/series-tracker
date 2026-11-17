"""Alloha without a browser: one open of the player of one episode, as the player itself makes it (the owner's prototype,
source-prototypes/alloha/прототип-alloha: session.py, transport.py, download.py; the protocol after the HTTP client of
YummyTV).

The player asks its host for the files of the episode (`POST /bnsi/movies/<id>`) signed by a header `Borth`: the
fingerprint of a browser, then a tail the player computes from the `viewporti` of its own document, fresh for every open.
The fingerprint and the User-Agent are a client profile captured from the real player (`alloha_profile.py` refreshes
it on a refusal; `ST_ALLOHA_FINGERPRINT`, `ST_ALLOHA_USER_AGENT` are optional starting values). Every request goes out
with the TLS of that browser (curl_cffi, the Chrome of the User-Agent).

The CDN of the files gives them only to a player that plays: the answer of the host names a WebSocket where the player
says it started (`playback_start`, `resumed`) and keeps saying it plays (`playing`, every 30 s); the server answers with
`edge_hash`, the value of `Accepts-Controls` every request to the CDN carries, and may give a new one at any time. The
player's reports (`/events`, `/stat`) go as the player sends them. A request of the CDN carries `Authorizations`, a key
of the current script of the player.

A session opened to read (`answer`) asks for the files and closes; a session that downloads (`transfer`) keeps its
socket and its reports for as long as it takes the segments.
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from core import clock
from core.reasons import reason

from .contract import TransferRefused

LANGUAGE = "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7"
# Safeguards of one request of the host or the CDN and of the first `edge_hash`, as the prototype held them.
REQUEST_TIMEOUT_S = 15.0
EDGE_TIMEOUT_S = 2 * REQUEST_TIMEOUT_S + 1
# The beats of the protocol of the player, not waits: `playing` on the socket, a heartbeat of its reports, and a batch of
# three heartbeats sent to `/events`.
PLAYING_EVERY_S = 30.0
HEARTBEAT_EVERY_S = 10.0
HEARTBEATS_A_BATCH = 3
# The owner's limit per download (17.11.2026).
CONNECTIONS = 2
_STARTUP = ("ad_block_start", "view_start", "startup", "play", "pause", "playback_ready", "ad_block_end", "play",
            "first_frame", "file_load_complete")
_CLIENT_ENV = {"connectionType": "4g", "downlink": 10, "pixelRatio": 1, "screenW": 1920, "screenH": 1080}


class AllohaRefused(TransferRefused):
    """A step of the open or of the transfer did not come through, in words for the log and the card. `status` is the
    HTTP status a host answered, `final` a refusal that is the source's last word on the file (it is not there)."""

    def __init__(self, words: str, *, status: int | None = None, final: bool = False,
                 profile_rejected: bool = False) -> None:
        super().__init__(words, kind="final" if final else "broken", status=status)
        self.profile_rejected = profile_rejected


@dataclass(frozen=True)
class Profile:
    """The client the player is opened as: the fingerprint of `Borth` and the User-Agent of the same browser."""
    fingerprint: str
    user_agent: str


def profile_of(environ: Mapping[str, str]) -> Profile | None:
    """The initial profile the environment holds; None without one (the player can capture it). A fingerprint of another form is a
    fault of the environment and stops the start."""
    fingerprint, agent = environ.get("ST_ALLOHA_FINGERPRINT", "").strip(), environ.get("ST_ALLOHA_USER_AGENT", "").strip()
    if not fingerprint and not agent:
        return None
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint) or "Chrome/" not in agent:
        raise ValueError("ST_ALLOHA_FINGERPRINT must be 64 hex digits and ST_ALLOHA_USER_AGENT a Chrome User-Agent")
    return Profile(fingerprint, agent)


def borth_tail(seed: str) -> str:
    """The tail of `Borth` the player computes from the `viewporti` of its document (prototype session.borth_tail)."""
    def regroup(source: str, bucket_of: Callable[[int], int], order) -> str:
        indices = [bucket_of(i) for i in range(len(source))]
        buckets, offset = {}, 0
        for bucket in order:
            count = indices.count(bucket)
            buckets[bucket] = iter(source[offset:offset + count])
            offset += count
        return "".join(next(buckets[bucket]) for bucket in indices)

    if not seed:
        return seed
    bits = (len(seed) - 1).bit_length()
    first = regroup(seed, int.bit_length, range(bits, -1, -1))
    second = regroup(first, lambda i: bits if i == 0 else (i & -i).bit_length() - 1, range(bits + 1))
    modulus = len(seed) + 1
    while modulus < 2 or any(modulus % i == 0 for i in range(2, math.isqrt(modulus) + 1)):
        modulus += 1
    result, cursor, used = [""] * len(seed), 0, set()
    for char in second:
        while True:
            cursor = (cursor + 2) % modulus
            if cursor < len(seed) and cursor not in used:
                used.add(cursor)
                result[cursor] = char
                break
    return "".join(result)


def chrome_profile(user_agent: str) -> str:
    """The newest TLS profile of Chrome curl_cffi has that is not newer than the Chrome of the User-Agent."""
    from curl_cffi.requests.impersonate import BrowserType
    found = re.search(r"Chrome/(\d+)", user_agent)
    major = int(found.group(1)) if found else 0
    known = [(int(each.value.removeprefix("chrome")), each.value) for each in BrowserType
             if re.fullmatch(r"chrome\d+", each.value)]
    fitting = [each for each in known if each[0] <= major] or known
    return max(fitting)[1]


def _meta(page: str, name: str) -> str | None:
    found = re.search(rf"""<meta\b[^>]*\bname=["']{re.escape(name)}["'][^>]*>""", page, re.IGNORECASE)
    content = re.search(r"""\bcontent=["']([^"']*)["']""", found.group(0)) if found else None
    return content.group(1) if content else None


def _active_id(page: str) -> str | None:
    found = re.search(r"""const\s+fileList\s*=\s*JSON\.parse\(\s*(["'`])(.*?)\1\s*\)""", page, re.S)
    if not found:
        return None
    quote, literal = found.groups()
    try:
        text = json.loads('"' + literal + '"') if quote == '"' else literal.replace('\\"', '"').replace("\\\\", "\\")
        return str(json.loads(text)["active"]["id"])
    except (ValueError, KeyError, TypeError):
        return None


def _user_param(page: str) -> dict[str, str]:
    block = re.search(r"userParam\s*=\s*\{([^}]*)\}", page)
    found = {}
    for field in ("token", "domain"):
        value = re.search(field + r""":\s*'([^']*)'""", block.group(1)) if block else None
        if value:
            found[field] = value.group(1)
    return found


@dataclass(frozen=True)
class Track:
    """A stream of the answer: the voice-over (`audio_id`, `label`), the height and its addresses (mirrors)."""
    audio_id: str
    label: str
    height: int
    urls: tuple[str, ...]


def tracks_of(data: Any) -> list[Track]:
    """Every stream the answer of the host lists, the voice-over the player marks as chosen first."""
    sources = data.get("hlsSource") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return []
    ordered = sorted((each for each in sources if isinstance(each, dict)), key=lambda each: each.get("default") is not True)
    found = []
    for index, track in enumerate(ordered):
        for height, value in (track.get("quality") or {}).items():
            urls = tuple(each.strip() for each in str(value).split(" or ") if each.strip())
            if str(height).isdigit() and urls:
                found.append(Track(str(track.get("audioId", index)), str(track.get("label") or ""), int(height), urls))
    return found


class AllohaSession:
    """One open of the player of one episode. `http` is how its requests go out (curl_cffi with the TLS of the browser of
    the profile); `media` makes a client of its own for each connection of a download (a client speaks HTTP/2 and would
    carry every request of a download over one connection). Both are given by the caller so that a stand serves them."""

    def __init__(self, profile: Profile, http, *, media: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.profile, self.http, self._clock = profile, http, clock
        self._media = media
        self.origin = self.iframe = self.authorizations = ""
        self.edge = ""
        self.view: dict[str, str] = {}
        self.active_id = ""
        self.socket_url = self.socket_key = ""
        self._ws = None
        self._tasks: list[asyncio.Task] = []
        self._edge_came = asyncio.Event()
        self.error: str | None = None
        self.profile_rejected = False
        self.bytes_loaded = 0
        self._started = time.monotonic()
        self._track: Track | None = None
        self.duration = 0.0

    # --- requests ---------------------------------------------------------------------------------------------------

    def _base(self) -> dict[str, str]:
        return {"User-Agent": self.profile.user_agent, "Accept-Language": LANGUAGE}

    def media_client(self):
        """A client for one connection of a download: a new one, or the session's own when it makes none."""
        return self._media() if self._media is not None else self.http

    async def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None, client=None, **kw):
        try:
            return await (client or self.http).request(method, url, headers={**self._base(), **(headers or {})},
                                                       timeout=REQUEST_TIMEOUT_S, **kw)
        except Exception as exc:  # noqa: BLE001 - every failure of the way is a refusal with its kind
            raise AllohaRefused(reason("host_request_failed", host=urlsplit(url).hostname,
                                       cause=type(exc).__name__)) from exc

    async def _ok(self, method: str, url: str, **kw):
        response = await self.request(method, url, **kw)
        if response.status_code >= 400:
            blocked = response.headers.get("x-vd") or ""
            words = (reason("host_status_blocked", host=urlsplit(url).hostname, status=response.status_code, blocked=blocked)
                     if blocked else reason("host_status", host=urlsplit(url).hostname, status=response.status_code))
            raise AllohaRefused(words, status=response.status_code,
                                final=response.status_code in (404, 410) and not blocked,
                                profile_rejected=response.status_code == 403 and blocked in {"session_blocked", "client_blocked"})
        return response

    # --- the open -----------------------------------------------------------------------------------------------------

    async def document(self, iframe: str, referer: str) -> str:
        """The document of the player as the site that embeds it asks for it."""
        self.iframe = iframe
        parts = urlsplit(iframe)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        response = await self._ok("GET", iframe, headers={"Referer": referer})
        return response.text

    async def answer(self, page: str) -> dict:
        """The answer of the host to the ask of the files of the episode the document shows, signed afresh."""
        seed, active = _meta(page, "viewporti"), _active_id(page)
        if not seed or not active:
            raise AllohaRefused(reason("player_doc_no_viewport"))
        self.active_id, self.view = active, _user_param(page)
        token = parse_qs(urlsplit(self.iframe).query).get("token", [""])[0]
        response = await self._ok(
            "POST", f"{self.origin}/bnsi/movies/{active}",
            data={"token": token, "av1": "false", "autoplay": "0", "audio": "", "subtitle": ""},
            headers={"Borth": f"{self.profile.fingerprint}|{borth_tail(seed)}", "Origin": self.origin,
                     "Referer": self.iframe, "X-Requested-With": "XMLHttpRequest"})
        try:
            data = json.loads(response.content)
        except ValueError as exc:
            raise AllohaRefused(reason("player_answer_not_json")) from exc
        if not isinstance(data, dict):
            raise AllohaRefused(reason("player_answer_not_object"))
        self.socket_url, self.socket_key = str(data.get("pnr") or ""), str(data.get("pnk") or "")
        return data

    async def authorize(self, page: str) -> None:
        """`Authorizations`: the one long key of the current script of the player."""
        script = next((each for each in re.findall(r"""<script\b[^>]*\bsrc=["']([^"']+)["']""", page)
                       if re.search(r"/app\.[^/]+\.js", each)), None)
        if script is None:
            raise AllohaRefused(reason("player_doc_no_script"))
        text = (await self._ok("GET", urljoin(self.iframe, script), headers={"Referer": self.iframe})).text
        keys = set(re.findall(r"""['"]([A-Za-z0-9]{340,420})['"]""", text))
        if len(keys) != 1:
            raise AllohaRefused(reason("player_script_keys", count=len(keys)))
        self.authorizations = "Bearer " + keys.pop()

    # --- playing ------------------------------------------------------------------------------------------------------

    def media_headers(self) -> dict[str, str]:
        """The headers of a request of the CDN, with the `Accepts-Controls` the socket gave last."""
        found = re.search(r"Chrome/(\d+)", self.profile.user_agent)
        major = found.group(1) if found else "0"
        return {**self._base(), "Origin": self.origin, "Referer": self.origin + "/", "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "cross-site", "Sec-CH-UA-Mobile": "?0",
                "Sec-CH-UA-Platform": '"Windows"',
                "Sec-CH-UA": f'"Chromium";v="{major}", "Google Chrome";v="{major}", "Not_A Brand";v="24"',
                "Authorizations": self.authorizations, "Accepts-Controls": self.edge or self.profile.fingerprint}

    def _said(self, kind: str) -> str:
        track = self._track
        position = min(time.monotonic() - self._started, self.duration) if self.duration else 0
        return json.dumps({"type": kind, "current_time": int(position), "resolution": str(track.height if track else ""),
                           "track_id": track.audio_id if track else "", "speed": 1, "subtitle": -1,
                           "ts": int(self._clock() * 1000)})

    async def play(self, track: Track, connect: Callable[..., Awaitable[Any]]) -> None:
        """The socket opened, the start said, the first `edge_hash` taken; then the socket is kept saying it plays."""
        if not self.socket_url or not self.socket_key:
            raise AllohaRefused(reason("player_answer_no_socket"))
        self._track, self._started = track, time.monotonic()
        url = self.socket_url + ("&" if "?" in self.socket_url else "?") + urlencode(
            {"sid": self.socket_key, "v": "2.1", "t": int(self._clock() * 1000)})
        try:
            self._ws = await connect(url, headers={**self._base(), "Origin": self.origin})
        except Exception as exc:  # noqa: BLE001 - a socket that did not open is a refusal of the way
            raise AllohaRefused(reason("socket_not_opened", cause=type(exc).__name__)) from exc
        for kind in ("playback_start", "resumed"):
            await self._ws.send_str(self._said(kind))
        self._tasks.append(asyncio.ensure_future(self._listen()))
        try:
            await asyncio.wait_for(self._edge_came.wait(), EDGE_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            raise AllohaRefused(self.error or reason("socket_no_edge", seconds=f"{EDGE_TIMEOUT_S:.0f}"),
                                profile_rejected=self.profile_rejected) from exc
        if self.error:
            raise AllohaRefused(self.error, profile_rejected=self.profile_rejected)
        self._tasks.append(asyncio.ensure_future(self._keep_playing()))

    async def _listen(self) -> None:
        try:
            while True:
                message = await self._ws.recv_str()
                try:
                    payload = json.loads(message)
                except ValueError:
                    continue
                if isinstance(payload, dict) and payload.get("edge_hash"):
                    self.edge = str(payload["edge_hash"])
                    self._edge_came.set()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - a socket that closed ends the transfer: no old edge is used blindly
            code = getattr(self._ws, "close_code", None)
            self.profile_rejected = code == 4005
            self.error = reason("socket_closed", cause=type(exc).__name__, close_code=code)
            self._edge_came.set()

    async def _keep_playing(self) -> None:
        async def say() -> None:
            await self._ws.send_str(self._said("playing"))
        try:
            await clock.every(PLAYING_EVERY_S, say)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = reason("socket_failed", cause=type(exc).__name__)

    # --- the reports of the player ------------------------------------------------------------------------------------

    def _event(self, kind: str, paused: bool = False, **fields) -> dict:
        now = int(self._clock() * 1000)
        position = min(time.monotonic() - self._started, self.duration) if self.duration else 0.0
        return {"url": None, "manifestUrl": None, "currentTime": position, "duration": self.duration,
                "bufferLength": None, "paused": paused, "audioTrack": self._track.label if self._track else "",
                "quality": self._track.height if self._track else 0, "subtitle": None, "event_type": kind, "at": now,
                "since": now - self._init_at, **fields}

    async def _report(self, path: str, data: dict) -> None:
        await self._ok("POST", self.origin + path, data=data, headers={"Origin": self.origin, "Referer": self.iframe})

    async def _events(self, events: list[dict]) -> None:
        payload = {"token": self.view.get("token", ""), "domain": self.view.get("domain", ""), "isTrailer": False,
                   "dropped": 0, "playerInitAt": self._init_at, "clientSessionId": self._view_id,
                   "clientRequestId": self._request_id, "env": _CLIENT_ENV, "events": events}
        await self._report("/events", {"token": self.view.get("token", ""),
                                       "payload": json.dumps(payload, ensure_ascii=False)})

    async def report(self, duration: float) -> None:
        """The reports of a player that starts: the startup events, the two statistics, then a heartbeat every 10 s."""
        if not self.view.get("token"):
            raise AllohaRefused(reason("player_doc_no_events_token"))
        self.duration = duration
        self._view_id, self._request_id = uuid.uuid4().hex, uuid.uuid4().hex
        self._init_at = int(self._clock() * 1000)
        await self._events([self._event(kind, paused=index < 7) for index, kind in enumerate(_STARTUP)])
        for percent in (None, 0):
            data = {"id": self.active_id, "token": self.view.get("token", ""), "domain": self.view.get("domain", ""),
                    "url": self.iframe, "type": "mgpo", "ab": "false", "info[wasm]": "true", "info[sw]": "false",
                    "info[webSocket]": "true", "info[resolution][screenWidth]": _CLIENT_ENV["screenW"],
                    "info[resolution][screenHeight]": _CLIENT_ENV["screenH"],
                    "info[resolution][windowWidth]": _CLIENT_ENV["screenW"],
                    "info[resolution][windowHeight]": _CLIENT_ENV["screenH"],
                    "info[resolution][devicePixelRatio]": _CLIENT_ENV["pixelRatio"], "info[platform]": "Win32"}
            if percent is not None:
                data["percent"] = percent
            await self._report("/stat", data)
        self._tasks.append(asyncio.ensure_future(self._heartbeats()))

    async def _heartbeats(self) -> None:
        pending: list[dict] = []
        before = {"bytes": 0, "position": 0.0}

        async def beat() -> None:
            event = self._event("heartbeat", bytesLoadedTotal=self.bytes_loaded,
                                bytesLoadedDelta=self.bytes_loaded - before["bytes"], isBuffering=False,
                                isSeeking=False, playbackRate=1)
            event["playedSecondsDelta"] = event["currentTime"] - before["position"]
            before.update(bytes=self.bytes_loaded, position=event["currentTime"])
            pending.append(event)
            if len(pending) == HEARTBEATS_A_BATCH:
                await self._events(list(pending))
                pending.clear()
        try:
            await clock.every(HEARTBEAT_EVERY_S, beat)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.error = reason("player_report_failed", cause=str(exc))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._ws is not None:
            try:
                await self._ws.close(timeout=2)
            except Exception:  # noqa: BLE001 - a socket already gone is closed
                pass
