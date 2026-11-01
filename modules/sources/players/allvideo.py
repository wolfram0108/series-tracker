"""AllVideo embed (Playerjs): find it on a release page, ask it once, read the answer, resolve a stream.

A resolution is read only from the quality tags of the player's own file list: the title of the page names the
episode, not the stream, and was seen to say 4K over a list that stops at 1080p.
"""
from __future__ import annotations

import html
import logging
import re
from urllib.parse import urlparse

import httpx

from .contract import Answer, Kind, PlayerRef, Reading
from .http import get_answer, read_answer

logger = logging.getLogger("sources")

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


def read_playerjs(status: int, text: str) -> Reading:
    if status == 404:
        return Reading(Kind.NOT_FOUND, status)
    if status != 200:
        return Reading(Kind.HTTP_ERROR, status)
    values = _FILE.findall(text)
    if any(ERROR_FILE in value for value in values):
        return Reading(Kind.NOT_FOUND, status, message=ERROR_FILE)
    heights = set().union(*(_heights(value) for value in values)) if values else set()
    if heights:
        return Reading(Kind.PLAYABLE, status, tuple(sorted(heights)))
    found = _TITLE.search(text)
    title = " ".join(found.group(1).split()) if found else ""
    if _PLAYERJS in text.lower():
        return Reading(Kind.NO_FILE, status, title=title)
    return Reading(Kind.NOT_PLAYER, status, title=title)


def best_stream_url(file_val: str) -> str | None:
    """Best stream URL from the comma-separated Playerjs quality list."""
    streams: list[tuple[int, str]] = []
    for chunk in file_val.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = re.search(r"\[(?:(\d+)p?|(4k))\]", chunk, re.IGNORECASE)
        if match:
            quality = 2160 if match.group(2) else int(match.group(1))
            stream_url = re.sub(r"\[[^\]]+\]", "", chunk).strip()
            if stream_url:
                streams.append((quality, stream_url))
        else:
            cleaned = re.sub(r"\[[^\]]+\]", "", chunk).strip()
            if cleaned:
                streams.append((0, cleaned))
    if streams:
        streams.sort(key=lambda item: item[0], reverse=True)
        return streams[0][1]
    cleaned = re.sub(r"\[[^\]]+\]", "", file_val).strip()
    return cleaned if cleaned else None


class AllVideoPlayer:
    name = "allvideo"
    label = "AllVideo"
    series_scope = False

    HOSTS = ("fsst.online", "incvideo1.online")
    _SOURCE = re.compile(
        r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online)/"
        r"embed/\d+/?)")

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

    async def resolve(self, url: str, http: httpx.AsyncClient) -> dict | None:
        try:
            response = await http.get(url, headers={"Referer": "https://1.ruchime.org/"},
                                      follow_redirects=True)
            if response.status_code != 200:
                return None
            match = _FILE.search(response.text)
            if not match:
                return None
            stream_url = best_stream_url(match.group(1))
            if not stream_url:
                return None
            final_url = str(getattr(response, "url", "") or url)
            parsed = urlparse(final_url)
            if not parsed.scheme or not parsed.netloc:
                parsed = urlparse(url)
            origin = (f"{parsed.scheme}://{parsed.netloc}/"
                      if parsed.scheme and parsed.netloc else "")
            return {"stream_url": stream_url,
                    "headers": {"Referer": origin} if origin else {}}
        except Exception as exc:  # noqa: BLE001 - resolution is retried by download
            logger.warning("Failed resolving AllVideo stream for %s: %s", url, exc)
            return None
