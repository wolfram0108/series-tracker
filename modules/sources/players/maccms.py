"""The HLS of an episode at a MacCMS collection site (Jisu, Xinlang, Lzi, Subo): one rendition, its frame measured.

A site gives one rendition of an episode (a media playlist, or a master playlist of one variant) and says nothing true of
its frame: Lzi labels nearly every title 1080x608 (its frames were 1280x720, 1080x606 and 1920x1080 within one title), the
others say nothing at all. So the frame is read from the picture: the head of the first segment, decrypted when the
playlist names an AES-128 key, is given to ffprobe. The address of a playlist names its video for good, so the site hands
over what the last table said of it and a frame is measured once.

The link of a row is the page of the title with the playlist in its fragment; the stream is the playlist itself, which
yt-dlp downloads in parallel segments (no headers: the hosts ask for none).
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import quote, unquote, urljoin, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.ffprobe import film_stream, streams_of

from .contract import Answer, Kind, PlayerRef, Reading
from .http import USER_AGENT, get_answer
from .streaming import listed_stream

REQUEST_TIMEOUT_S = 20.0
# Enough of a segment to hold its first key frame with the parameters of the picture; the rest of it is not fetched.
HEAD_BYTES = 256 * 1024
# The asks in flight to one host of the files: above it the hosts slow down (40 episodes of Lzi: 7.4 s at 8, 12.1 s at 16).
HOST_GATE = 8
MEASURE_TIMEOUT_S = 30.0

_LINK = re.compile(r"(?:^|&)maccms=([^&]+)")
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass(frozen=True)
class Head:
    """What the picture of a playlist is read from: its first segment, how it is encrypted and its initialisation."""
    segment: str
    sequence: int
    key: str = ""
    iv: bytes | None = None
    init: str = ""
    method: str = "NONE"


class MeasureError(RuntimeError):
    """A request of the measure got no picture: the answer it got, for the policy to judge."""

    def __init__(self, answer: Answer) -> None:
        super().__init__(answer.error or str(answer.status))
        self.answer = answer


Measure = Callable[[httpx.AsyncClient, Head, str], Awaitable[int | None]]


def attributes(line: str) -> dict[str, str]:
    return {name: value.strip('"') for name, value in _ATTRIBUTE.findall(line.split(":", 1)[1] if ":" in line else "")}


def is_playlist(text: str) -> bool:
    return text.lstrip("﻿").lstrip().startswith("#EXTM3U")


def best_variant(text: str, base: str) -> str | None:
    """The variant of a master playlist with the highest bandwidth; None for a media playlist."""
    lines = text.splitlines()
    variants = [(int(attributes(line).get("BANDWIDTH") or 0), urljoin(base, uri.strip()))
                for line, uri in zip(lines, lines[1:]) if line.startswith("#EXT-X-STREAM-INF") and uri.strip()
                and not uri.startswith("#")]
    return max(variants)[1] if variants else None


def head_of(text: str, base: str) -> Head | None:
    """The first segment of a media playlist with the key and the initialisation that apply to it; None without one."""
    sequence, key, iv, init, method = 0, "", None, "", "NONE"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1] or 0)
        elif line.startswith("#EXT-X-KEY:"):
            found = attributes(line)
            method = found.get("METHOD", "NONE")
            key = urljoin(base, found["URI"]) if found.get("URI") else ""
            iv = bytes.fromhex(found["IV"][2:].zfill(32)) if found.get("IV", "").lower().startswith("0x") else None
        elif line.startswith("#EXT-X-MAP:"):
            init = urljoin(base, attributes(line).get("URI", ""))
        elif line and not line.startswith("#"):
            return Head(urljoin(base, line), sequence, key, iv, init, method)
    return None


async def _bytes(http: httpx.AsyncClient, url: str, referer: str, limit: int | None = None) -> bytes:
    headers = {"User-Agent": USER_AGENT, "Referer": referer, **({"Range": f"bytes=0-{limit - 1}"} if limit else {})}
    try:
        response = await http.get(url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise MeasureError(Answer(None, error=type(exc).__name__)) from exc
    if response.status_code not in (200, 206):
        raise MeasureError(Answer(response.status_code, "", final_url=str(response.url)))
    return response.content[:limit] if limit else response.content


def decrypted(data: bytes, key: bytes, iv: bytes) -> bytes:
    """The head of an AES-128 segment: CBC decrypts any whole blocks from the start without the rest of the segment."""
    whole = data[: len(data) // 16 * 16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(whole) + decryptor.finalize()


async def measure_head(http: httpx.AsyncClient, head: Head, referer: str) -> int | None:
    """The frame height of the film in the head of the first segment; None when the picture cannot be read from it."""
    data = await _bytes(http, head.segment, referer, HEAD_BYTES)
    if head.method == "AES-128":
        key = await _bytes(http, head.key, referer)
        data = decrypted(data, key, head.iv if head.iv is not None else head.sequence.to_bytes(16, "big"))
    if head.init:
        data = await _bytes(http, head.init, referer) + data
    streams = await streams_of("pipe:0", input_args=[] if head.init else ["-f", "mpegts"], data=data,
                               timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    try:
        return int(film["height"]) if film is not None else None
    except (KeyError, TypeError, ValueError):
        return None


class MaccmsPlayer:
    name = "maccms"
    label = "MacCMS"
    # The site of the title lists the episodes; no page shows this player as a catalog.
    series_scope = False
    stream_headers: dict[str, str] = {}

    def __init__(self, measure: Measure | None = None) -> None:
        self._measure = measure or measure_head
        self._gates: dict[str, asyncio.Semaphore] = {}

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        """No page embeds this player: the site of the title makes its links from the episodes the API lists."""
        return None

    @staticmethod
    def link_of(page_url: str, playlist: str) -> str:
        return f"{page_url}#maccms={quote(playlist, safe='')}"

    @staticmethod
    def playlist_of(url: str) -> str | None:
        found = _LINK.search(urlsplit(url).fragment)
        return unquote(found.group(1)) if found else None

    @classmethod
    def video_of(cls, url: str) -> str:
        """The playlist: its address is the host's own name of the video, whichever title page lists it."""
        return cls.playlist_of(url) or ""

    @classmethod
    def owns(cls, url: str) -> bool:
        return cls.playlist_of(url) is not None

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        return await listed_stream(self, url, http, max_height, pacer)

    def capped(self, max_height: int | None) -> "_Located":
        """An episode has one rendition, its frame measured on the table: there is nothing to choose under a cap, and the
        stream is its playlist, asked for only to see that it is there."""
        return _Located(self)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, *, measure: bool = True) -> Answer:
        playlist = self.playlist_of(ref.url)
        if playlist is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a maccms link"))
        host = urlsplit(playlist).hostname or ""
        async with self._gates.setdefault(host, asyncio.Semaphore(HOST_GATE)):
            return await self._fetch(playlist, http, page_url, measure)

    async def _fetch(self, playlist: str, http: httpx.AsyncClient, page_url: str, measure: bool) -> Answer:
        answer = await get_answer(http, playlist, page_url, timeout=REQUEST_TIMEOUT_S)
        if answer.status != 200 or not is_playlist(answer.text):
            return answer
        media, base = answer.text, answer.final_url or playlist
        variant = best_variant(media, base)
        if variant is not None:
            answer = await get_answer(http, variant, page_url, timeout=REQUEST_TIMEOUT_S)
            if answer.status != 200 or not is_playlist(answer.text):
                return answer
            media, base = answer.text, answer.final_url or variant
        head = head_of(media, base)
        if head is None:
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="the playlist lists no segment"))
        if head.method not in ("NONE", "AES-128"):
            # A segment whose samples are encrypted (SAMPLE-AES) shows no picture from its head: no file to take.
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=f"encryption {head.method}"))
        if not measure:
            return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, playlist),)))
        try:
            height = await self._measure(http, head, page_url)
        except MeasureError as exc:
            return exc.answer
        if not height:
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no picture in the head of the first segment"))
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (height,), files=((height, playlist),)))

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


class _Located:
    """The player asked for the stream of an episode: the playlist is read, its picture is not measured again."""

    series_scope = False

    def __init__(self, player: MaccmsPlayer) -> None:
        self.player = player
        self.name, self.label, self.stream_headers = player.name, player.label, player.stream_headers

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self.player.fetch(ref, http, page_url, measure=False)

    def read(self, answer: Answer) -> Reading:
        return self.player.read(answer)
