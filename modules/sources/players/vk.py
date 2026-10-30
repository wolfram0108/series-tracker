"""VK video: find it on a release page, ask it once, read the answer.

Only the direct link of a video goes anywhere from here: the call behind vk.com/video<oid>_<id> (al_video.php, act=show)
tells whether the video opens, and the same link is what the central downloader is given, so that the downloader's own
reading of it (yt-dlp: the formats, the cap of the resolution, the parallel fragments) decides what is fetched.
"""
from __future__ import annotations

import html
import re

import httpx

from .contract import Answer, PlayerRef, Reading
from .http import post_answer, read_answer
from .vk_show import ENDPOINT, read_show

_VIDEO = re.compile(r"video(-?\d+_\d+)|oid=(-?\d+)&(?:amp;)?id=(\d+)")
_SHOW_HEADERS = {"Referer": ENDPOINT, "X-Requested-With": "XMLHttpRequest"}
DIRECT_LINK = "https://vkvideo.ru/video{video}"


def video_of(url: str) -> str | None:
    """The `<oid>_<id>` of a direct link or of an embed address."""
    found = _VIDEO.search(url or "")
    if not found:
        return None
    return found.group(1) or f"{found.group(2)}_{found.group(3)}"


class VkPlayer:
    name = "vk"
    series_scope = False

    _SOURCE = re.compile(
        r"switchSource\(['\"](https?://(?:vk\.com|vkvideo\.ru)/"
        r"video_ext\.php\?[^'\"]+)")

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        match = self._SOURCE.search(text)
        if not match:
            return None
        raw_url = html.unescape(match.group(1))
        video = video_of(raw_url)
        canonical = f"https://vk.com/video{video}" if video else raw_url
        return PlayerRef(name=self.name, url=canonical, raw_url=raw_url)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        video = video_of(ref.url) or video_of(ref.raw_url)
        if video is None:
            return Answer(None, error="no video in the address")
        return await post_answer(http, ENDPOINT, {"act": "show", "video": video, "al": 1}, _SHOW_HEADERS)

    def read(self, answer: Answer) -> Reading:
        return read_answer(answer, read_show)

    def download_address(self, ref: PlayerRef) -> str | None:
        """What the central downloader is given."""
        video = video_of(ref.url) or video_of(ref.raw_url)
        return DIRECT_LINK.format(video=video) if video else None
