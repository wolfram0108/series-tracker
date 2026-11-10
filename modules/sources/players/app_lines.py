"""The lines of a video application (modules/sources/apps): one player per application, each line of its cards a player
of the table of its own.

A card of an application lists its episodes in lines (`4K`, `专线`, `蓝光①`…), each line of a player of the application
with its way to the stream: a line of type 0 lists the playlists themselves, any other gives a token that a parser of the
application turns into the address of the stream (`parseUrl` of the line names the parsers it allows, in the order the
application lists its parsers). The link of a row is the card with the line and the token in its fragment; the token is
the application's own name of the video.

The frame of every episode is measured in the read of its card, as the frame of every row of every source is: its stream
now, its playlist, the head of its first segment (players/hls.py). A card holds thousands of episodes, so they are asked
by many at once: a gate for each parser of the application and for each host of its files, apart from the pace of the
other sites.

A row of a line is of the player `<application>:<line>` (its label the name of the line): the composition switches a line
off, ranks it and keeps its variants as it does any player. The registry finds the application by the part before `:`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from urllib.parse import parse_qs, quote, urldefrag, urlsplit

import httpx

from core.reasons import reason

from ..apps import SCHEME
from ..apps.app99 import AppError, App99Client
from core.ffprobe import film_stream, streams_of

from ..policy import settle
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .hls import MEASURE_TIMEOUT_S, Measure, ffprobe_gate, measure_head, measured
from .vk_files import pick_file

logger = logging.getLogger("sources")

# The parses asked of one parser of an application at once, and the asks in flight to one host of its files.
PARSER_GATE = 16
HOST_GATE = 16
_DIRECT = re.compile(r"\.(m3u8|mp4)(?:$|\?)", re.I)


def is_file(address: str) -> bool:
    """A stream that is one file (an MP4), not a playlist: its frame is read from the file, a server serves it in parts."""
    return urlsplit(address).path.lower().endswith(".mp4")


async def measure_file(address: str, headers: dict[str, str]) -> Answer:
    sent = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    async with ffprobe_gate():
        streams = await streams_of(address, input_args=["-headers", sent] if sent else [], timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    if film is None or not film.get("height"):
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no picture in the file"))
    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (int(film["height"]),), files=((int(film["height"]), address),)))


def line_player_name(app: str, line: str) -> str:
    return f"{app}:{line}"


class AppPlayer:
    """The player of one application: it resolves and measures the episodes of every line of its cards."""

    series_scope = False
    # A row is of a line of this player (`<name>:<line>`), not of the player itself.
    lines = True
    stream_headers: dict[str, str] = {}

    def __init__(self, client: App99Client, measure: Measure | None = None) -> None:
        self.client = client
        self.name, self.label = client.profile.name, client.profile.label
        self._measure = measure or measure_head
        self._parsers: dict[str, asyncio.Semaphore] = {}
        self._hosts: dict[str, asyncio.Semaphore] = {}

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

    def line(self, code: str, label: str) -> "LinePlayer":
        return LinePlayer(self, code, label)

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

    async def address(self, line: str, item: str) -> str:
        """The address of the stream of an episode now: the token itself when it is one, else what the parsers the line
        allows make of it, the first that gives an address; each refusal is in the log."""
        player = await self._player_of(line)
        if item.startswith(("http://", "https://")) and (player.get("type") == 0 or _DIRECT.search(item)):
            return item
        refusals = []
        for parser in await self._parsers_of(player):
            try:
                async with self._parsers.setdefault(str(parser), asyncio.Semaphore(PARSER_GATE)):
                    return await self.client.parse(parser, item)
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
        try:
            address = await self.address(line, item)
            headers = await self.headers_of(line)
        except AppError as exc:
            kind = Kind.NO_ANSWER if exc.transient else Kind.NO_FILE
            return Answer(200, reading=Reading(kind, 200, message=exc.reason))
        host = urlsplit(address).hostname or ""
        async with self._hosts.setdefault(host, asyncio.Semaphore(HOST_GATE)):
            if is_file(address):
                if not measure:
                    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, address),)))
                return await measure_file(address, headers)
            return await measured(http, address, "", self._measure if measure else None, headers)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error)
        if answer.status == 404:
            return Reading(Kind.NOT_FOUND, answer.status)
        if answer.status == 200:
            return Reading(Kind.NOT_PLAYER, 200, message="not a playlist")
        return Reading(Kind.HTTP_ERROR, answer.status)

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """The playlist of the episode now (its frame was measured on the table), with the headers of its line."""
        found = self.parts(url)
        located = _Located(self)
        verdict = await settle(located, PlayerRef(self.name, url), http, urldefrag(url).url, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None or found is None:
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=verdict.reading.message
                                           or verdict.reading.kind.value), verdict.exhausted)
        progressive = is_file(picked[1])
        return {"stream_url": picked[1], "headers": await self.headers_of(found[0]), "progressive": progressive,
                "playlist": not progressive}


class LinePlayer:
    """A line of the application as a player of the table: its rows are its own, asked of the application's player."""

    series_scope = False

    def __init__(self, app: AppPlayer, code: str, label: str) -> None:
        self.app, self.code = app, code
        self.name, self.label = line_player_name(app.name, code), label
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
    """The application's player asked for the stream of an episode: the playlist is read, its picture not measured."""

    series_scope = False

    def __init__(self, player: AppPlayer) -> None:
        self.player = player
        self.name, self.label = player.name, player.label

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self.player.fetch(ref, http, page_url, measure=False)

    def read(self, answer: Answer) -> Reading:
        return self.player.read(answer)
