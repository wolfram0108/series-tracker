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
from core import notices
from core.http_limits import AnswerTooLarge
from core.reasons import reason

from ..apps import SCHEME
from ..apps import AppClient, AppError, AppStream
from ..apps.lines import LineRegistry
from ..policy import settle
from ..term import Term
from .contract import Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .dash import AudioRenewal, Tracks, UnsupportedDash, is_dash, tracks
from .hls import (MEASURE_TIMEOUT_S, Measure, container_of, file_head_size, is_playlist, measure_head, measured,
                  probe_args, waiting)
from .http import USER_AGENT, failure_words, retry_after
from .vk_files import pick_file

logger = logging.getLogger("sources")

# Enough of the start of an answer to tell a playlist from a file by its signature.
PEEK_BYTES = 4096
# The range asked of the address of a stream: a playlist of thousands of segments fits whole, a file is not taken.
PLAYLIST_BYTES = 8 * 1024 * 1024
# A resolver's daily allowance belongs to its line, not each of its thousands of episodes. Probe for recovery later.
QUOTA_RECHECK_S = 60.0
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
    head: bytes = b""


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
                    playlist = is_playlist(head[:PEEK_BYTES].decode("utf-8", "replace")) or is_dash(head)
                    # Of a file, as much of its head as tells a notice of the host from an episode (core/notices.py).
                    if not playlist and len(head) >= file_head_size(head, max(PEEK_BYTES, notices.head_bytes())):
                        break
                if len(head) > PLAYLIST_BYTES:
                    return Served("", Answer(200, reading=Reading(Kind.NO_FILE, 200,
                                         message="stream manifest exceeds the size limit")))
            # A range the host kept to cuts a playlist longer than it: the whole of it is then asked by the measure.
            cut = response.status_code == 206 and len(head) >= PLAYLIST_BYTES
    except AnswerTooLarge as exc:
        return Served("", Answer(None, error=f"{type(exc).__name__}: the host ignores the range of a head"))
    except httpx.HTTPError as exc:
        return Served("", Answer(None, error=failure_words(exc, address)))
    if is_dash(head):
        if cut:
            return Served("", Answer(200, reading=Reading(Kind.NO_FILE, 200, message="DASH manifest is incomplete")))
        return Served("dash", Answer(200, head.decode("utf-8-sig", "replace"), final_url=final))
    if is_playlist(head[:PEEK_BYTES].decode("utf-8", "replace")):
        return Served("playlist", Answer(None) if cut else Answer(200, head.decode("utf-8", "replace"), final_url=final))
    if container_of(head) is not None:
        notice = notices.notice_of(head)
        if notice is not None:
            # A clip the host serves instead of the episode: it has a frame and a length, and is no episode.
            return Served("", Answer(200, reading=Reading(Kind.NOT_FOUND, 200, message=(
                f"the host {urlsplit(address).hostname} served its notice «{notice.id}» instead of the episode: "
                f"{notice.words}"))))
        return Served("file", head=head)
    return Served("", Answer(200, reading=Reading(Kind.NO_FILE, 200, message=(
        f"the host {urlsplit(address).hostname} served no stream: {kind or 'no type'}, «{_excerpt(head)}»"))))


async def measure_file_frame(address: str, headers: dict[str, str], *, head: bytes = b"") -> Answer:
    """The frame of a file at its address, as ffprobe reads it with the headers of its line; what ffprobe said when it
    could not read it."""
    if head:
        # Fast-start MP4s carry their track parameters in the bytes already fetched to recognise the container.
        # Reuse those bytes before asking ffprobe to open another network connection to the same file.
        streams, _said = await probe("pipe:0", data=head, input_args=["-probesize", str(len(head))],
                                     timeout=MEASURE_TIMEOUT_S)
        film = film_stream(streams or [])
        if film is not None and film.get("height"):
            height = int(film["height"])
            return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (height,), files=((height, address),)))
    streams, said = await probe(address, input_args=probe_args(headers), timeout=MEASURE_TIMEOUT_S)
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
                 measure_file: Callable[..., Awaitable[Answer]] | None = None, *,
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
        self._quotas: dict[str, tuple[AppError, float]] = {}
        # The lines another application parses when this one's own parser says no: line -> (its client, its line); and
        # until when the asks of such a line go straight to it, its own parser having said no a moment ago.
        self.through: dict[str, tuple[AppClient, str]] = {}
        self._detours: dict[str, float] = {}
        # The agent the application's own player plays its streams with (`player_agent` of the profile): some CDNs of
        # the applications refuse any other. A header the application gave with the stream stays as it is.
        self.agent = str(client.profile.settings.get("player_agent") or "")
        self._dash_renewal = AudioRenewal(clock)

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

    def playback(self, headers: dict[str, str]) -> dict[str, str]:
        """The headers every request of a stream of this application carries, as its own player sends them."""
        if not self.agent or any(name.lower() == "user-agent" for name in headers):
            return headers
        return {**headers, "User-Agent": self.agent}

    def refusal_for(self, link: str) -> Reading | None:
        """A recently confirmed daily quota; a still usable parsed address needs no resolver allowance."""
        found = self.parts(link)
        if found is None or self._kept(*found) is not None:
            return None
        held = self._quotas.get(found[0])
        if held is not None and self._clock() < held[1]:
            return Reading(Kind.RESTRICTED, 200, message=held[0].reason)
        return None

    async def address(self, line: str, item: str, card: str = "") -> AppStream:
        """The stream of an episode: what the application gave for it within the term, else what it gives now."""
        kept = self._kept(line, item)
        if kept is not None:
            return kept
        got = await self._parsed_now(line, item, card)
        self._parsed[(line, item)] = (got, self._clock())
        return got

    async def _parsed_now(self, line: str, item: str, card: str) -> AppStream:
        route = self.through.get(line)
        if route is not None and self._clock() < self._detours.get(line, 0.0):
            return await self._detour(route, line, item)
        held = self._quotas.get(line)
        if held is not None and self._clock() < held[1]:
            raise held[0]
        self._quotas.pop(line, None)
        try:
            return await self.client.resolve(line, item, card)
        except AppError as exc:
            if route is not None and not exc.transient:
                # The own parser said no: the line goes to the other application's parser for the term, and the own one
                # is asked again after it (one ask of the refusing parser a minute, not one for each episode).
                logger.info("приложение %s, линия %s: собственный разбор отказал (%s), разбирает %s", self.name, line,
                            exc.reason[:160], route[0].profile.name)
                self._detours[line] = self._clock() + QUOTA_RECHECK_S
                return await self._detour(route, line, item)
            if exc.daily_quota:
                self._quotas[line] = (exc, self._clock() + QUOTA_RECHECK_S)
            raise

    async def _detour(self, route: tuple[AppClient, str], line: str, item: str) -> AppStream:
        client, code = route
        return await client.resolve(code, item, "")

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, *, measure: bool = True) -> Answer:
        found = self.parts(ref.url)
        if found is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a link of the application"))
        line, item = found
        # An address kept from an earlier parse that is no longer served is parsed anew, once, within the same ask; so is
        # the address of a line another application parses, when the host served no stream at it (a notice in place of the
        # episode is an answer of success to the application's own parser).
        routed = line in self.through
        for _ in range(2 if routed or self._kept(line, item) is not None else 1):
            detoured = routed and self._clock() < self._detours.get(line, 0.0)
            try:
                got = await self.address(line, item, urlsplit(ref.url).path.strip("/"))
            except AppError as exc:
                kind = (Kind.RESTRICTED if exc.daily_quota else Kind.HTTP_ERROR if exc.status is not None
                        else Kind.NO_ANSWER if exc.transient else Kind.NO_FILE)
                return Answer(exc.status or 200, retry_after=exc.retry_after,
                              reading=Reading(kind, exc.status or 200, message=exc.reason))
            answer = await self._served(http, got.address, self.playback(got.headers), measure)
            if self.read(answer).kind is Kind.PLAYABLE:
                return answer
            # An address that gave no stream serves no later ask: the next one has the application parse the episode anew.
            self._parsed.pop((line, item), None)
            if routed and not detoured and self.read(answer).kind is not Kind.NO_ANSWER:
                logger.info("приложение %s, линия %s: адрес собственного разбора не дал потока (%s), разбирает %s", self.name,
                            line, self.read(answer).kind.value, self.through[line][0].profile.name)
                self._detours[line] = self._clock() + QUOTA_RECHECK_S
        return answer

    async def _served(self, http: httpx.AsyncClient, address: str, headers: dict[str, str], measure: bool) -> Answer:
        """What the host serves; a transport failure on the way is an answer without a status (no answer), as the
        contract of a player has it, not an error out of the player."""
        try:
            return await self._stream_answer(http, address, headers, measure)
        except httpx.HTTPError as exc:
            return Answer(None, error=failure_words(exc, address))

    async def _stream_answer(self, http: httpx.AsyncClient, address: str, headers: dict[str, str],
                             measure: bool) -> Answer:
        """What the host serves at the address of a stream: a file or a playlist, measured or only seen to be there."""
        served = await served_as(http, address, headers)
        if served.kind == "dash":
            try:
                found = tracks(served.answer.text, served.answer.final_url or address)
            except UnsupportedDash as exc:
                return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=str(exc)))
            if not measure:
                return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, address),)))
            measured_tracks = await self._dash_files(http, found.videos, headers)
            if isinstance(measured_tracks, Answer):
                return measured_tracks
            audio = await self._dash_audio_ready(http, found, headers)
            if isinstance(audio, Answer):
                return audio
            heights = tuple(sorted({height for height, _ in measured_tracks}))
            return Answer(200, reading=Reading(Kind.PLAYABLE, 200, heights,
                                               files=tuple((height, address) for height in heights)))
        if served.kind == "file":
            if not measure:
                return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, address),)))
            return await self._measure_file(address, headers, head=served.head)
        if served.kind == "playlist":
            return await measured(http, address, "", self._measure if measure else None, headers,
                                  fetched=served.answer if served.answer.status else None)
        return served.answer

    async def _dash_files(self, http: httpx.AsyncClient, videos: tuple[str, ...], headers: dict[str, str]
                          ) -> tuple[tuple[int, str], ...] | Answer:
        files = []
        for address in videos:
            served = await served_as(http, address, headers)
            if served.kind != "file":
                return served.answer if not served.kind else Answer(200, reading=Reading(
                    Kind.NO_FILE, 200, message="DASH: video track is not a complete media file"))
            answer = await self._measure_file(address, headers, head=served.head)
            if self.read(answer).kind is not Kind.PLAYABLE:
                return answer
            files.extend(self.read(answer).files)
        return tuple(files)

    async def _dash_audio_ready(self, http: httpx.AsyncClient, found: Tracks, headers: dict[str, str]) -> str | Answer:
        """The audio of the episode: the application's, else, when the CDN refused it, Bilibili's for the same CID."""
        failure = await self._dash_audio(http, found.audio, headers)
        if failure is None:
            return found.audio
        if failure.status == 403:
            renewed = await self._dash_renewal.get(http, found)
            if renewed:
                if await self._dash_audio(http, renewed, headers) is None:
                    return renewed
                self._dash_renewal.reject(found)
        return failure

    async def _dash_audio(self, http: httpx.AsyncClient, address: str, headers: dict[str, str]) -> Answer | None:
        """A working picture alone is no playable DASH episode: its audio has a signature of its own."""
        served = await served_as(http, address, headers)
        if served.kind != "file":
            reading = self.read(served.answer) if not served.kind else Reading(Kind.NO_FILE, 200)
            return Answer(served.answer.status, retry_after=served.answer.retry_after, reading=Reading(
                reading.kind, reading.status, message="DASH audio: " + (reading.message or "no media file")))
        streams, _ = await probe("pipe:0", data=served.head, input_args=["-probesize", str(len(served.head))],
                                 timeout=MEASURE_TIMEOUT_S)
        if not any(stream.get("codec_type") == "audio" for stream in streams or []):
            streams, _ = await probe(address, input_args=probe_args(headers), timeout=MEASURE_TIMEOUT_S)
        if any(stream.get("codec_type") == "audio" for stream in streams or []):
            return None
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="DASH audio: no readable audio track"))

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
        headers = self.playback(kept.headers if kept is not None else {})
        served = await served_as(http, picked[1], headers)
        if served.kind == "dash":
            try:
                found_tracks = tracks(served.answer.text, served.answer.final_url or picked[1])
            except UnsupportedDash as exc:
                raise StreamUnavailable(reason("player_failed", player=self.label, cause=str(exc))) from exc
            files = await self._dash_files(http, found_tracks.videos, headers)
            if isinstance(files, Answer):
                cause = self.read(files)
            elif (video := pick_file(files, max_height)) is None:
                cause = Reading(Kind.NO_FILE, message="DASH: no measured video track")
            elif isinstance(audio := await self._dash_audio_ready(http, found_tracks, headers), Answer):
                cause = self.read(audio)
            else:
                return {"stream_url": video[1], "audio_url": audio, "headers": headers,
                        "progressive": False, "playlist": False}
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=cause.message or cause.kind.value),
                                    cause.kind in {Kind.NO_ANSWER, Kind.HTTP_ERROR})
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
