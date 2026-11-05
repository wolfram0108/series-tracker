"""AllVideo embed (Playerjs): find it on a release page, ask it once, read the answer, resolve a stream.

A resolution is read only from the quality tags of the player's own file list: the title of the page names the
episode, not the stream, and was seen to say 4K over a list that stops at 1080p.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urlparse

import httpx

from core.reasons import reason

from ..policy import probe_from, settle
from .contract import EMBED_PAGE, Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .http import get_answer, read_answer
from .vk_files import pick_file

# AllVideo answers a missing embed with status 404 and a Playerjs that plays this placeholder file.
ERROR_FILE = "video_error"
_FILE = re.compile(r'file:\s*["\']([^"\']+)["\']')
_TAG = re.compile(r"\[(\d+)p?\]", re.IGNORECASE)
_TAG_4K = re.compile(r"\[4k\]", re.IGNORECASE)
_NAME = re.compile(r"[-_](\d{3,4})p?\.(?:mp4|m3u8)", re.IGNORECASE)
_TITLE = re.compile(r"<title>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_PLAYERJS = "new playerjs("


def _heights(value: str) -> set[int]:
    found = {int(height) for height in _TAG.findall(value)} | {int(height) for height in _NAME.findall(value)}
    if _TAG_4K.search(value):
        found.add(2160)
    return found


def reading_of_files(values: list[str], status: int = 200) -> Reading:
    """What the file lists of the player say of a video, wherever they are given (its embed, a playlist of many)."""
    if any(ERROR_FILE in value for value in values):
        return Reading(Kind.NOT_FOUND, status, message=ERROR_FILE)
    heights = set().union(*(_heights(value) for value in values)) if values else set()
    if heights:
        return Reading(Kind.PLAYABLE, status, tuple(sorted(heights)), files=_files(values))
    return Reading(Kind.NO_FILE, status)


def embed_url(host: str, video_id: str) -> str:
    return f"https://{host}/embed/{video_id}/"


def read_playerjs(status: int, text: str) -> Reading:
    if status == 404:
        return Reading(Kind.NOT_FOUND, status)
    if status != 200:
        return Reading(Kind.HTTP_ERROR, status)
    reading = reading_of_files(_FILE.findall(text), status)
    if reading.kind is not Kind.NO_FILE:
        return reading
    found = _TITLE.search(text)
    title = " ".join(found.group(1).split()) if found else ""
    if _PLAYERJS in text.lower():
        return Reading(Kind.NO_FILE, status, title=title)
    return Reading(Kind.NOT_PLAYER, status, title=title)


def _files(values: list[str]) -> tuple[tuple[int, str], ...]:
    """The streams of the file lists by height: `[720p]https://host/a.mp4/` is the stream of 720p, a bare address with a
    height in its name is the stream of that height, and one with neither is left out (nothing says what it is)."""
    found: dict[int, str] = {}
    for value in values:
        for chunk in value.split(","):
            heights = _heights(chunk)
            stream = re.sub(r"\[[^\]]+\]", "", chunk).strip()
            if heights and stream:
                found[max(heights)] = stream
    return tuple(sorted(found.items()))


class AllVideoPlayer:
    name = "allvideo"
    label = "AllVideo"
    series_scope = False

    # The domains of the one player: the others send their embeds to incvideo1.online.
    HOSTS = ("fsst.online", "incvideo1.online", "csst.online")
    _SOURCE = re.compile(
        r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online|csst\.online)/"
        r"embed/\d+/?)")

    @staticmethod
    def video_of(url: str) -> str:
        found = re.search(r"/embed/(\d+)", urlparse(url).path)
        return found.group(1) if found else ""

    @classmethod
    def owns(cls, url: str) -> bool:
        host = (urlparse(url).hostname or "").lower()
        return any(host == known or host.endswith("." + known) for known in cls.HOSTS)

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        match = self._SOURCE.search(text)
        if not match:
            return None
        return PlayerRef(name=self.name, url=html.unescape(match.group(1)))

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await get_answer(http, ref.url, page_url)

    def read(self, answer: Answer) -> Reading:
        return read_answer(answer, read_playerjs)

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """An embed -> its best stream within the cap now, asked with the origin the embed answered from."""
        asked, ref = self.resolving(), PlayerRef(self.name, url)
        verdict = await settle(asked, ref, http, EMBED_PAGE, pacer=pacer)
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None:
            probe = probe_from(verdict, ref, self.label)
            raise StreamUnavailable(reason("player_failed", player=self.label, cause=probe.reason), probe.transient)
        return {"stream_url": picked[1], "headers": {"Referer": asked.origin} if asked.origin else {}}

    def resolving(self) -> "_Resolving":
        """The same player asked for the files of an embed, which says where the embed finally answered from."""
        return _Resolving(self)


class _Resolving:
    """A player asked under the one policy, remembering the origin the embed answered from: the stream is asked with it."""

    series_scope = False

    def __init__(self, player: AllVideoPlayer) -> None:
        self._player, self.name, self.origin = player, player.name, ""

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        answer = await self._player.fetch(ref, http, page_url)
        parts = urlparse(answer.final_url or ref.url)
        self.origin = f"{parts.scheme}://{parts.netloc}/" if parts.scheme and parts.netloc else ""
        return answer

    def read(self, answer: Answer) -> Reading:
        return self._player.read(answer)
