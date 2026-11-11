"""The lines of a video application (modules/sources/apps): one player per application, each admitted line of its cards a
player of the table of its own.

A card of an application lists its episodes in lines, each line of a player of the application with its way to the
stream: a line of type 0 lists the playlists themselves, any other gives a token that a parser of the application turns
into the address of the stream (`parseUrl` of the line names the parsers it allows, in the order the application lists
its parsers). A line is a player only when the registry of the application admits it (apps/lines.py): its first, central
and last episode were downloaded whole and read back. Its player is `<application>:<code>`, named by its code (`CO4K`);
the name the application gives the line is a field of its rows.

The link of a row is the card with the line and the token in its fragment; the token is the application's own name of the
video. The address a parser gave serves the asks of the episode within the term of the module (term.py); an address no longer served
is parsed anew, once. What an address serves is told by the start of the answer, not by the form of the address nor by
the type the host announces (hosts serve playlists as `image/png` and MP4 as `image/jpeg`): `#EXTM3U` is a playlist, the
signature of a container (MP4, MPEG transport stream, Matroska, FLV) is a file, anything else is no stream, and the row
says what the host served instead.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qs, quote, urldefrag, urlsplit

import httpx

from core.ffprobe import film_stream, probe
from core.http_limits import AnswerTooLarge
from core.reasons import reason

from ..apps import SCHEME
from ..apps import AppClient, AppError, AppStream
from ..apps.lines import LineRegistry
from ..policy import settle
from ..term import Term
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .hls import MEASURE_TIMEOUT_S, Measure, container_of, is_playlist, measure_head, measured, waiting
from .http import USER_AGENT, retry_after
from .vk_files import pick_file

logger = logging.getLogger("sources")

# Enough of the start of an answer to tell a playlist from a file by its signature.
PEEK_BYTES = 4096
# The range asked of the address of a stream: a playlist of thousands of segments fits whole, a file is not taken.
PLAYLIST_BYTES = 8 * 1024 * 1024
_DIRECT = re.compile(r"\.(m3u8|mp4)(?:$|\?)", re.I)


def _excerpt(head: bytes) -> str:
    text = head[:200].decode("utf-8", "replace")
    return " ".join("".join(ch if ch.isprintable() else " " for ch in text).split())[:80]


@dataclass(frozen=True)
class Served:
    """What a host serves at the address of a stream: a playlist (`answer` is the playlist itself), a file, or no stream
    (`answer` is what the host said: its status, its failure, or what it served instead)."""
    kind: str
    answer: Answer = Answer(None)


async def served_as(http: httpx.AsyncClient, address: str, headers: dict[str, str]) -> Served:
    """What the host serves at the address of a stream, asked once: a playlist is read whole (a host that makes it anew
    at every request may refuse the next one), of a file only the head is taken."""
    try:
        asked = {"User-Agent": USER_AGENT, **headers, "Range": f"bytes=0-{PLAYLIST_BYTES - 1}"}
        async with http.stream("GET", address, headers=asked, follow_redirects=True,
                               timeout=waiting(MEASURE_TIMEOUT_S)) as response:
            if response.status_code not in (200, 206):
                return Served("", Answer(response.status_code, "", retry_after(response), final_url=str(response.url)))
            kind, final = response.headers.get("content-type", ""), str(response.url)
            head, playlist = b"", False
            async for chunk in response.aiter_bytes():
                head += chunk
                if not playlist and len(head) >= PEEK_BYTES:
                    playlist = is_playlist(head[:PEEK_BYTES].decode("utf-8", "replace"))
                    if not playlist:
                        break
            # A range the host kept to cuts a playlist longer than it: the whole of it is then asked by the measure.
            cut = response.status_code == 206 and len(head) >= PLAYLIST_BYTES
    except AnswerTooLarge as exc:
        return Served("", Answer(None, error=f"{type(exc).__name__}: the host ignores the range of a head"))
    except httpx.HTTPError as exc:
        return Served("", Answer(None, error=f"{type(exc).__name__}: {urlsplit(address).hostname}"))
    if is_playlist(head[:PEEK_BYTES].decode("utf-8", "replace")):
        return Served("playlist", Answer(None) if cut else Answer(200, head.decode("utf-8", "replace"), final_url=final))
    if container_of(head) is not None:
        return Served("file")
    return Served("", Answer(200, reading=Reading(Kind.NO_FILE, 200, message=(
        f"the host {urlsplit(address).hostname} served no stream: {kind or 'no type'}, «{_excerpt(head)}»"))))


async def measure_file_frame(address: str, headers: dict[str, str]) -> Answer:
    """The frame of a file at its address, as ffprobe reads it with the headers of its line; what ffprobe said when it
    could not read it."""
    sent = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    streams, said = await probe(address, input_args=["-user_agent", USER_AGENT, *(["-headers", sent] if sent else [])],
                                timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    if film is None or not film.get("height"):
        cause = said.replace(address, urlsplit(address).hostname or "").strip() or "no picture in the file"
        # A failure of the way (the connection, the time) is no answer about the file and is asked again.
        kind = Kind.NO_ANSWER if _ON_THE_WAY.search(cause) else Kind.NO_FILE
        return Answer(200, reading=Reading(kind, 200, message=cause))
    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (int(film["height"]),), files=((int(film["height"]), address),)))


# What ffprobe says when the way to the file failed, not the file: a connection cut or timed out, a server error of the
# host. A refused connection is the host saying no (a dead address an application gives) and stays the answer.
_ON_THE_WAY = re.compile(r"connection (reset|timed out)|timed out|input/output error|server returned 5\d\d|"
                         r"gave no answer|network is unreachable|name or service not known|failed to resolve", re.I)


# The player of the lines of the applications: a row of a line is of `line:<code>`, whichever application lists it.
LINES = "line"


def line_player_name(code: str) -> str:
    return f"{LINES}:{code}"


class AppPlayer:
    """The player of one application: it resolves and measures the episodes of its admitted lines."""

    series_scope = False
    stream_headers: dict[str, str] = {}

    def __init__(self, client: AppClient, measure: Measure | None = None,
                 measure_file: Callable[[str, dict[str, str]], Awaitable[Answer]] | None = None, *,
                 registry: LineRegistry | None = None, clock: Callable[[], float] = time.monotonic,
                 term: Term | None = None) -> None:
        self.client = client
        self.name, self.label = client.profile.name, client.profile.label
        self.registry = registry if registry is not None else LineRegistry.load(self.name)
        self._measure = measure or measure_head
        self._measure_file = measure_file or measure_file_frame
        self._clock = clock
        self.term = term if term is not None else Term()
        # (line, token) -> (address a parser gave, when): what was parsed serves the asks of the episode within the term.
        self._parsed: dict[tuple[str, str], tuple[AppStream, float]] = {}

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

    def _kept(self, line: str, item: str) -> AppStream | None:
        found = self._parsed.get((line, item))
        if found is None or self._clock() - found[1] >= self.term.seconds:
            return None
        return found[0]

    async def address(self, line: str, item: str) -> AppStream:
        """The stream of an episode: what the application gave for it within the term, else what it gives now."""
        kept = self._kept(line, item)
        if kept is not None:
            return kept
        got = await self.client.resolve(line, item)
        self._parsed[(line, item)] = (got, self._clock())
        return got

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, *, measure: bool = True) -> Answer:
        found = self.parts(ref.url)
        if found is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a link of the application"))
        line, item = found
        # An address kept from an earlier parse that is no longer served is parsed anew, once, within the same ask.
        for _ in range(2 if self._kept(line, item) is not None else 1):
            try:
                got = await self.address(line, item)
            except AppError as exc:
                return Answer(200, reading=Reading(Kind.NO_ANSWER if exc.transient else Kind.NO_FILE, 200,
                                                   message=exc.reason))
            answer = await self._served(http, got.address, got.headers, measure)
            if self.read(answer).kind is Kind.PLAYABLE:
                return answer
            # An address that gave no stream serves no later ask: the next one has the application parse the episode anew.
            self._parsed.pop((line, item), None)
        return answer

    async def _served(self, http: httpx.AsyncClient, address: str, headers: dict[str, str], measure: bool) -> Answer:
        """What the host serves; a transport failure on the way is an answer without a status (no answer), as the
        contract of a player has it, not an error out of the player."""
        try:
            return await self._stream_answer(http, address, headers, measure)
        except httpx.HTTPError as exc:
            return Answer(None, error=f"{type(exc).__name__}: {urlsplit(address).hostname}")

    async def _stream_answer(self, http: httpx.AsyncClient, address: str, headers: dict[str, str],
                             measure: bool) -> Answer:
        """What the host serves at the address of a stream: a file or a playlist, measured or only seen to be there."""
        served = await served_as(http, address, headers)
        if served.kind == "file":
            if not measure:
                return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, address),)))
            return await self._measure_file(address, headers)
        if served.kind == "playlist":
            return await measured(http, address, "", self._measure if measure else None, headers,
                                  fetched=served.answer if served.answer.status else None)
        return served.answer

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error)
        if answer.status == 404:
            return Reading(Kind.NOT_FOUND, answer.status, message="the host answered 404")
        if answer.status == 200:
            return Reading(Kind.NOT_PLAYER, 200, message=f"the host served no playlist: «{_excerpt(answer.text.encode())}»")
        return Reading(Kind.HTTP_ERROR, answer.status, message=f"the host answered {answer.status}")

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """The stream of the episode now — a playlist or a file, as its host serves it — with the headers of its line."""
        found = self.parts(url)
        verdict = await settle(_Located(self), PlayerRef(self.name, url), http, urldefrag(url).url, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None or found is None:
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=verdict.reading.message
                                           or verdict.reading.kind.value), verdict.exhausted)
        kept = self._kept(*found)
        headers = kept.headers if kept is not None else {}
        served = await served_as(http, picked[1], headers)
        if not served.kind:
            cause = self.read(served.answer)
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=cause.message or cause.kind.value),
                                    True)
        progressive = served.kind == "file"
        return {"stream_url": picked[1], "headers": headers, "progressive": progressive, "playlist": not progressive}


class LinePlayer:
    """A line as a player of the table, one for its code in every application (`line:<code>`, named by the code): its
    rows are asked of the application whose card the link of the row names."""

    series_scope = False

    def __init__(self, lines: "AppLines", code: str) -> None:
        self.lines, self.code = lines, code
        self.name, self.label = line_player_name(code), code
        self.stream_headers: dict[str, str] = {}

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        return None

    def video_of(self, url: str) -> str:
        return self.lines.video_of(url)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self.lines.fetch(ref, http, page_url)

    def read(self, answer: Answer) -> Reading:
        return self.lines.read(answer)


class AppLines:
    """The player of the lines of every application (`line`): a row of `line:<code>` is the row of the application its
    link names (`app://<application>/<card>#line=…&item=…`), asked of that application."""

    name = LINES
    label = "Линии приложений"
    series_scope = False
    lines = True
    stream_headers: dict[str, str] = {}

    def __init__(self, apps: "list[AppPlayer]") -> None:
        self.apps = {app.name: app for app in apps}

    def app_of(self, url: str) -> AppPlayer | None:
        split = urlsplit(url)
        return self.apps.get(split.hostname or "") if split.scheme == SCHEME else None

    def owns(self, url: str) -> bool:
        app = self.app_of(url)
        return app is not None and app.owns(url)

    def video_of(self, url: str) -> str:
        app = self.app_of(url)
        return app.video_of(url) if app is not None else ""

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        return None

    def line(self, code: str) -> LinePlayer:
        return LinePlayer(self, code)

    def codes(self) -> list[str]:
        """The codes of the lines some application admits, each once."""
        return sorted({code for app in self.apps.values() for code in app.registry.admitted})

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        app = self.app_of(ref.url)
        if app is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a link of an application"))
        return await app.fetch(ref, http, page_url)

    def read(self, answer: Answer) -> Reading:
        return next(iter(self.apps.values())).read(answer) if self.apps else Reading(Kind.NOT_PLAYER)

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        app = self.app_of(url)
        if app is None:
            raise StreamUnavailable(reason("player_failed", player=self.label, cause="not a link of an application"), False)
        return await app.stream(url, http, max_height, pacer)


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
