"""The lines of a video application (modules/sources/apps): one player per application, each admitted line of its cards a
player of the table of its own.

A card of an application lists its episodes in lines, each line of a player of the application with its way to the
stream: a line of type 0 lists the playlists themselves, any other gives a token that a parser of the application turns
into the address of the stream (`parseUrl` of the line names the parsers it allows, in the order the application lists
its parsers). A line is a player only when the registry of the application admits it (apps/lines.py): its first, central
and last episode were downloaded whole and read back. Its player is `<application>:<code>`, named by its code (`CO4K`);
the name the application gives the line is a field of its rows.

The link of a row is the card with the line and the token in its fragment; the token is the application's own name of the
video. The address a parser gave serves the asks of the episode for an hour (`PARSE_TERM_S`); an address no longer served
is parsed anew, once. What an address serves is told by the start of the answer, not by the form of the address nor by
the type the host announces (hosts serve playlists as `image/png` and MP4 as `image/jpeg`): `#EXTM3U` is a playlist, the
signature of a container (MP4, MPEG transport stream, Matroska, FLV) is a file, anything else is no stream, and the row
says what the host served instead.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote, urldefrag, urlsplit

import httpx

from core.ffprobe import film_stream, probe
from core.http_limits import AnswerTooLarge
from core.reasons import reason

from ..apps import SCHEME
from ..apps.app99 import AppError, App99Client
from ..apps.lines import LineRegistry
from ..policy import settle
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .hls import MEASURE_TIMEOUT_S, Measure, is_playlist, measure_head, measured
from .http import USER_AGENT
from .vk_files import pick_file

logger = logging.getLogger("sources")

# What a parser gave serves the asks of the episode for an hour (the owner's term): the download of an episode the read
# of its card measured takes the same address instead of a parse of its own.
PARSE_TERM_S = 3600.0
# Enough of the start of an answer to tell a playlist from a file by its signature.
PEEK_BYTES = 4096
_DIRECT = re.compile(r"\.(m3u8|mp4)(?:$|\?)", re.I)
TS_PACKET = 188


def container_of(head: bytes) -> str | None:
    """The container a file starts with, by its signature; None when the bytes start no media file."""
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:1] == b"\x47" and (len(head) <= TS_PACKET or head[TS_PACKET:TS_PACKET + 1] == b"\x47"):
        return "mpegts"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if head[:3] == b"FLV":
        return "flv"
    return None


def _excerpt(head: bytes) -> str:
    text = head[:200].decode("utf-8", "replace")
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())[:80]


async def served_as(http: httpx.AsyncClient, address: str, headers: dict[str, str]) -> str | Answer:
    """What the host serves at the address of a stream: "playlist", "file", or the answer that says it serves none (its
    status, its failure, or what it served instead of a stream). Only the head of the answer is asked."""
    try:
        asked = {"User-Agent": USER_AGENT, **headers, "Range": f"bytes=0-{PEEK_BYTES - 1}"}
        async with http.stream("GET", address, headers=asked, follow_redirects=True,
                               timeout=MEASURE_TIMEOUT_S) as response:
            if response.status_code not in (200, 206):
                return Answer(response.status_code, "", final_url=str(response.url))
            kind = response.headers.get("content-type", "")
            head = b""
            async for chunk in response.aiter_bytes():
                head += chunk
                if len(head) >= PEEK_BYTES:
                    break
    except AnswerTooLarge as exc:
        return Answer(None, error=f"{type(exc).__name__}: the host ignores the range of a head")
    except httpx.HTTPError as exc:
        return Answer(None, error=f"{type(exc).__name__}: {urlsplit(address).hostname}")
    if is_playlist(head[:PEEK_BYTES].decode("utf-8", "replace")):
        return "playlist"
    if container_of(head) is not None:
        return "file"
    return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=(
        f"the host {urlsplit(address).hostname} served no stream: {kind or 'no type'}, «{_excerpt(head)}»")))


async def measure_file_frame(address: str, headers: dict[str, str]) -> Answer:
    """The frame of a file at its address, as ffprobe reads it with the headers of its line; what ffprobe said when it
    could not read it."""
    sent = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    streams, said = await probe(address, input_args=["-user_agent", USER_AGENT, *(["-headers", sent] if sent else [])],
                                timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    if film is None or not film.get("height"):
        cause = said.replace(address, urlsplit(address).hostname or "").strip() or "no picture in the file"
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=cause))
    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (int(film["height"]),), files=((int(film["height"]), address),)))


def line_player_name(app: str, line: str) -> str:
    return f"{app}:{line}"


class AppPlayer:
    """The player of one application: it resolves and measures the episodes of its admitted lines."""

    series_scope = False
    # A row is of a line of this player (`<name>:<line>`), not of the player itself.
    lines = True
    stream_headers: dict[str, str] = {}

    def __init__(self, client: App99Client, measure: Measure | None = None,
                 measure_file: Callable[[str, dict[str, str]], Awaitable[Answer]] | None = None, *,
                 registry: LineRegistry | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.client = client
        self.name, self.label = client.profile.name, client.profile.label
        self.registry = registry if registry is not None else LineRegistry.load(self.name)
        self._measure = measure or measure_head
        self._measure_file = measure_file or measure_file_frame
        self._clock = clock
        # (line, token) -> (address a parser gave, when): what was parsed serves the asks of the episode within the hour.
        self._parsed: dict[tuple[str, str], tuple[str, float]] = {}

    # --- the link of a row ------------------------------------------------------------------------------------

    def link_of(self, card_url: str, line: str, item: str) -> str:
        return f"{card_url}#line={quote(line, safe='')}&item={quote(item, safe='')}"

    def parts(self, url: str) -> tuple[str, str] | None:
        """(line, token) of a link of this application; None for any other address."""
        split = urlsplit(url)
        if split.scheme != SCHEME or (split.hostname or "") != self.name:
            return None
        fields = parse_qs(split.fragment, keep_blank_values=True)
        line, item = (fields.get("line") or [""])[0], (fields.get("item") or [""])[0]
        return (line, item) if line and item else None

    def owns(self, url: str) -> bool:
        return self.parts(url) is not None

    def video_of(self, url: str) -> str:
        """The token: the application's own name of the video, whichever card lists it."""
        found = self.parts(url)
        return found[1] if found else ""

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        """No page embeds the player: the site of the application makes its links from the cards."""
        return None

    def line(self, code: str) -> "LinePlayer":
        return LinePlayer(self, code)

    def line_players(self) -> list["LinePlayer"]:
        """The players of the admitted lines, by their codes."""
        return [self.line(code) for code in sorted(self.registry.admitted)]

    # --- the stream of an episode ------------------------------------------------------------------------------

    async def _player_of(self, line: str) -> dict:
        players = (await self.client.system()).get("player") or {}
        found = next((each for key, each in players.items() if str(each.get("code") or key) == line), None)
        return found if isinstance(found, dict) else {}

    async def _parsers_of(self, player: dict) -> list:
        """The parsers a line allows, in the order the application lists them; every one when the line names none."""
        parsers = [each.get("id") for each in (await self.client.system()).get("parser_api") or [] if isinstance(each, dict)]
        allowed = [part.strip() for part in str(player.get("parseUrl") or "").split(",") if part.strip()]
        return [each for each in parsers if not allowed or str(each) in allowed]

    def _kept(self, line: str, item: str) -> str | None:
        found = self._parsed.get((line, item))
        if found is None or self._clock() - found[1] >= PARSE_TERM_S:
            return None
        return found[0]

    async def address(self, line: str, item: str) -> str:
        """The address of the stream of an episode: the token itself when it is one, the address a parser gave for it
        within the hour, else what the parsers the line allows make of it now, the first that gives an address; each
        refusal is in the log."""
        player = await self._player_of(line)
        if item.startswith(("http://", "https://")) and (player.get("type") == 0 or _DIRECT.search(item)):
            return item
        kept = self._kept(line, item)
        if kept is not None:
            return kept
        refusals = []
        for parser in await self._parsers_of(player):
            try:
                got = await self.client.parse(parser, item)
                self._parsed[(line, item)] = (got, self._clock())
                return got
            except AppError as exc:
                logger.info("приложение %s, линия %s: парсер %s не дал адреса — %s", self.name, line, parser, exc.reason)
                if exc.transient:
                    raise
                refusals.append(str(parser))
        raise AppError(reason("app_no_parser", app=self.label, line=line, tried=", ".join(refusals) or "—"))

    async def headers_of(self, line: str) -> dict[str, str]:
        """The headers the application's player of the line sends for its stream (its `headers`, a JSON object)."""
        raw = (await self._player_of(line)).get("headers") or "{}"
        try:
            found = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            return {}
        return {str(key): str(value) for key, value in found.items()} if isinstance(found, dict) else {}

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, *, measure: bool = True) -> Answer:
        found = self.parts(ref.url)
        if found is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a link of the application"))
        line, item = found
        was_kept = self._kept(line, item) is not None
        try:
            address = await self.address(line, item)
            headers = await self.headers_of(line)
        except AppError as exc:
            return Answer(200, reading=Reading(Kind.NO_ANSWER if exc.transient else Kind.NO_FILE, 200, message=exc.reason))
        answer = await self._stream_answer(http, address, headers, measure)
        if was_kept and self.read(answer).kind is not Kind.PLAYABLE:
            # The address kept from an earlier parse is no longer served: the episode is parsed anew, once.
            self._parsed.pop((line, item), None)
            try:
                address = await self.address(line, item)
            except AppError as exc:
                return Answer(200, reading=Reading(Kind.NO_ANSWER if exc.transient else Kind.NO_FILE, 200,
                                                   message=exc.reason))
            answer = await self._stream_answer(http, address, headers, measure)
        return answer

    async def _stream_answer(self, http: httpx.AsyncClient, address: str, headers: dict[str, str],
                             measure: bool) -> Answer:
        """What the host serves at the address of a stream: a file or a playlist, measured or only seen to be there."""
        served = await served_as(http, address, headers)
        if isinstance(served, Answer):
            return served
        if served == "file":
            if not measure:
                return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, address),)))
            return await self._measure_file(address, headers)
        return await measured(http, address, "", self._measure if measure else None, headers)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error)
        if answer.status == 404:
            return Reading(Kind.NOT_FOUND, answer.status, message="the host answered 404")
        if answer.status == 200:
            return Reading(Kind.NOT_PLAYER, 200, message="the host served no playlist")
        return Reading(Kind.HTTP_ERROR, answer.status, message=f"the host answered {answer.status}")

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """The stream of the episode now — a playlist or a file, as its host serves it — with the headers of its line."""
        found = self.parts(url)
        verdict = await settle(_Located(self), PlayerRef(self.name, url), http, urldefrag(url).url, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None or found is None:
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=verdict.reading.message
                                           or verdict.reading.kind.value), verdict.exhausted)
        headers = await self.headers_of(found[0])
        served = await served_as(http, picked[1], headers)
        if isinstance(served, Answer):
            cause = self.read(served)
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=cause.message or cause.kind.value),
                                    True)
        progressive = served == "file"
        return {"stream_url": picked[1], "headers": headers, "progressive": progressive, "playlist": not progressive}


class LinePlayer:
    """An admitted line of the application as a player of the table, named by its code: its rows are its own, asked of the
    application's player."""

    series_scope = False

    def __init__(self, app: AppPlayer, code: str) -> None:
        self.app, self.code = app, code
        self.name, self.label = line_player_name(app.name, code), code
        self.stream_headers = app.stream_headers

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        return None

    def video_of(self, url: str) -> str:
        return self.app.video_of(url)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self.app.fetch(ref, http, page_url)

    def read(self, answer: Answer) -> Reading:
        return self.app.read(answer)


class _Located:
    """The application's player asked for the stream of an episode: the playlist or file is seen, its picture not measured."""

    series_scope = False

    def __init__(self, player: AppPlayer) -> None:
        self.player = player
        self.name, self.label = player.name, player.label

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self.player.fetch(ref, http, page_url, measure=False)

    def read(self, answer: Answer) -> Reading:
        return self.player.read(answer)
