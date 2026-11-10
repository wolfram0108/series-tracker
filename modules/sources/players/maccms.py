"""The HLS of an episode at a MacCMS collection site (Jisu, Xinlang, Lzi, Subo): one rendition, its frame measured.

A site gives one rendition of an episode (a media playlist, or a master playlist of one variant) and says nothing true of
its frame: Lzi labels nearly every title 1080x608 (its frames were 1280x720, 1080x606 and 1920x1080 within one title), the
others say nothing at all. So the frame is read from the picture: the head of the first segment, decrypted when the
playlist names an AES-128 key, is given to ffprobe. The address of a playlist names its video for good, so the site hands
over what the last table said of it and a frame is measured once.

The link of a row is the page of the title with the playlist in its fragment; the stream is the playlist itself, downloaded
by its segments (modules/downloads/hls_native.py; no headers: the hosts ask for none).
"""
from __future__ import annotations

import asyncio
import re
from urllib.parse import quote, unquote, urlsplit

import httpx

from .contract import Answer, Kind, PlayerRef, Reading
from .hls import Head, Measure, head_of, measure_head, measured  # noqa: F401 - Head and head_of are the player's words too
from .streaming import listed_stream

# The asks in flight to one host of the files: above it the hosts slow down (40 episodes of Lzi: 7.4 s at 8, 12.1 s at 16).
HOST_GATE = 8

_LINK = re.compile(r"(?:^|&)maccms=([^&]+)")


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
        # The stream is an HLS playlist: it is downloaded by its segments (modules/downloads/hls_native.py).
        return {**(await listed_stream(self, url, http, max_height, pacer)), "playlist": True}

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
        return await measured(http, playlist, page_url, self._measure if measure else None)

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
