"""VK video: find it on a release page, ask it once, read the answer, resolve a stream.

The direct call behind vk.com/video<oid>_<id> (al_video.php, act=show) is asked first: it answers the same whichever of
the two player pages VK serves for an embed. Only a video it refuses is looked at through the player page.
"""
from __future__ import annotations

import html
import logging
import re

import httpx

from .contract import Answer, Kind, PlayerRef, Reading
from .http import get_answer, post_answer, read_answer
from .vk_embed import read_embed
from .vk_show import ENDPOINT, best_stream, read_show

logger = logging.getLogger("sources.video.players")

_VIDEO = re.compile(r"video(-?\d+_\d+)|oid=(-?\d+)&(?:amp;)?id=(\d+)")
_SHOW_HEADERS = {"Referer": ENDPOINT, "X-Requested-With": "XMLHttpRequest"}
_STREAM_HEADERS = {"Referer": "https://vk.com/"}
_ENVELOPE = '{"payload"'


def video_of(url: str) -> str | None:
    """The `<oid>_<id>` of a direct link or of an embed address."""
    found = _VIDEO.search(url or "")
    if not found:
        return None
    return found.group(1) or f"{found.group(2)}_{found.group(3)}"


async def _show(http: httpx.AsyncClient, video: str) -> Answer:
    return await post_answer(http, ENDPOINT, {"act": "show", "video": video, "al": 1}, _SHOW_HEADERS)


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
        ids = re.search(r"[?&]oid=(-?\d+)&(?:amp;)?id=(\d+)", raw_url)
        canonical = (f"https://vk.com/video{ids.group(1)}_{ids.group(2)}"
                     if ids else raw_url)
        return PlayerRef(name=self.name, url=canonical, raw_url=raw_url)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        video = video_of(ref.url) or video_of(ref.raw_url)
        if video is None:
            return await get_answer(http, ref.raw_url, page_url)
        shown = await _show(http, video)
        if ref.raw_url and shown.status == 200 and read_show(200, shown.text).kind is Kind.RESTRICTED:
            # The player page carries the hash that opens a video the direct call refuses.
            return await get_answer(http, ref.raw_url, page_url)
        return shown

    def read(self, answer: Answer) -> Reading:
        reader = read_show if answer.text.lstrip().startswith(_ENVELOPE) else read_embed
        return read_answer(answer, reader)

    async def resolve(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Fresh direct stream for a download: from the direct call, else from the player page of an embed address."""
        video = video_of(url)
        if video is not None:
            shown = await _show(http, video)
            stream = best_stream(shown.text) if shown.status == 200 else None
            if stream:
                return {"stream_url": html.unescape(stream), "headers": dict(_STREAM_HEADERS)}
        if "video_ext.php" not in url:
            logger.warning("VK gave no stream for %s", url)
            return None
        return await self._resolve_page(url, http)

    async def _resolve_page(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """The embed page of the older kind lists signed mp4 addresses itself."""
        try:
            response = await http.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://vk.com/"},
                follow_redirects=True, timeout=15.0)
            if response.status_code != 200:
                logger.warning("VK embed HTTP %s for %s", response.status_code, url)
                return None
            streams = [
                (int(quality), html.unescape(raw.replace("\\/", "/")))
                for quality, raw in re.findall(r'"mp4_(\d+)":"([^"]+)"', response.text)
            ]
            if streams:
                _quality, stream_url = max(streams, key=lambda item: item[0])
                return {"stream_url": stream_url, "headers": dict(_STREAM_HEADERS)}
            hls = re.search(r'"hls":"([^"]+)"', response.text)
            if hls:
                return {"stream_url": html.unescape(hls.group(1).replace("\\/", "/")), "headers": dict(_STREAM_HEADERS)}
            logger.warning("VK embed has no playable stream: %s", url)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed resolving VK stream for %s: %s", url, exc)
            return None
