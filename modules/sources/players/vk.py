"""VK video embed: find it on a release page, ask it once, read the answer, resolve a stream."""
from __future__ import annotations

import html
import logging
import re

import httpx

from .contract import Answer, PlayerRef, Reading
from .http import get_answer, read_answer
from .vk_embed import read_embed

logger = logging.getLogger("sources.video.players")


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
        return await get_answer(http, ref.raw_url, page_url)

    def read(self, answer: Answer) -> Reading:
        return read_answer(answer, read_embed)

    async def resolve(self, url: str, http: httpx.AsyncClient) -> dict | None:
        """Fresh direct stream from the VK embed page.

        The canonical `vk.com/video...` page is rejected by yt-dlp as
        «Access restricted» for some videos, while the embed page with hash
        still exposes signed mp4 URLs. Resolve them at download time so the
        signature is fresh.
        """
        try:
            response = await http.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                         "Referer": "https://vk.com/"},
                follow_redirects=True, timeout=15.0)
            if response.status_code != 200:
                logger.warning("VK embed HTTP %s for %s",
                               response.status_code, url)
                return None
            streams = [
                (int(quality), html.unescape(raw.replace("\\/", "/")))
                for quality, raw in re.findall(r'"mp4_(\d+)":"([^"]+)"',
                                                response.text)
            ]
            if streams:
                _quality, stream_url = max(streams, key=lambda item: item[0])
                return {"stream_url": stream_url,
                        "headers": {"Referer": "https://vk.com/"}}
            hls = re.search(r'"hls":"([^"]+)"', response.text)
            if hls:
                stream_url = html.unescape(hls.group(1).replace("\\/", "/"))
                return {"stream_url": stream_url,
                        "headers": {"Referer": "https://vk.com/"}}
            logger.warning("VK embed has no playable stream: %s", url)
            return None
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Failed resolving VK stream for %s: %s", url, exc)
            return None
