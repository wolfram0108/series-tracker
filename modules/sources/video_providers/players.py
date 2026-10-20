"""Player adapters for Ruchime: find a player on a page, probe, resolve.

Priority is VK, then AllVideo, then Vibix. An adapter reports availability, so a
player button without a playable stream never becomes a media candidate.
"""
from __future__ import annotations

import asyncio
import html
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _retry_after(response: "httpx.Response | None") -> float | None:
    if response is None:
        return None
    try:
        value = response.headers.get("Retry-After")
        return float(value) if value else None
    except (AttributeError, TypeError, ValueError):
        return None


async def _get_with_retry(http: "httpx.AsyncClient", url: str, headers: dict,
                          attempts: int = 3) -> "httpx.Response | None":
    """Probe a player host with backoff: rate limits must not look like absence."""
    response: "httpx.Response | None" = None
    for attempt in range(attempts):
        try:
            response = await http.get(url, headers=headers, follow_redirects=True,
                                      timeout=8.0)
        except Exception:  # noqa: BLE001 - retried below
            response = None
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
        if attempt < attempts - 1:
            await asyncio.sleep(_retry_after(response) or 5.0 * (attempt + 1))
    return response


@dataclass(frozen=True)
class PlayerRef:
    name: str
    url: str
    raw_url: str = ""


@dataclass(frozen=True)
class PlayerProbe:
    ref: PlayerRef
    available: bool
    resolution: int | None = None
    reason: str = ""
    transient: bool = False


class PlayerAdapter:
    name = ""
    series_scope = False

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        raise NotImplementedError

    async def probe(self, ref: PlayerRef, http: httpx.AsyncClient,
                    page_url: str) -> PlayerProbe:
        raise NotImplementedError


class VkPlayer(PlayerAdapter):
    name = "vk"

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

    async def probe(self, ref: PlayerRef, http: httpx.AsyncClient,
                    page_url: str) -> PlayerProbe:
        response = await _get_with_retry(http, ref.raw_url,
                                         {"Referer": page_url, "User-Agent": _UA})
        if response is None:
            return PlayerProbe(ref, False, reason="VK не ответил", transient=True)
        if response.status_code in (403, 408, 425, 429) or response.status_code >= 500:
            return PlayerProbe(ref, False,
                               reason=f"VK ответил {response.status_code}",
                               transient=True)
        if response.status_code == 404:
            return PlayerProbe(ref, False, reason="VK: видео не найдено")
        if response.status_code != 200:
            return PlayerProbe(ref, False,
                               reason=f"VK ответил {response.status_code}",
                               transient=True)
        resolutions = [int(q) for q in re.findall(r"mp4_(\d+)", response.text)]
        if resolutions:
            return PlayerProbe(ref, True, max(resolutions))
        lowered = response.text.lower()
        if "access denied" in lowered or "private" in lowered:
            return PlayerProbe(ref, False, reason="видео закрыто настройками приватности")
        # A page without markers and without an explicit refusal is inconclusive.
        return PlayerProbe(ref, False, reason="VK не подтвердил источник", transient=True)


class AllVideoPlayer(PlayerAdapter):
    name = "allvideo"

    _SOURCE = re.compile(
        r"switchSource\(['\"](https?://(?:fsst\.online|incvideo1\.online)/"
        r"embed/\d+/?)")

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        match = self._SOURCE.search(text)
        if not match:
            return None
        return PlayerRef(name=self.name, url=html.unescape(match.group(1)))

    async def probe(self, ref: PlayerRef, http: httpx.AsyncClient,
                    page_url: str) -> PlayerProbe:
        response = await _get_with_retry(http, ref.url,
                                         {"Referer": page_url, "User-Agent": _UA})
        if response is None:
            return PlayerProbe(ref, False, reason="AllVideo не ответил", transient=True)
        if response.status_code in (403, 408, 425, 429) or response.status_code >= 500:
            return PlayerProbe(ref, False,
                               reason=f"AllVideo ответил {response.status_code}",
                               transient=True)
        if response.status_code != 200:
            return PlayerProbe(ref, False,
                               reason=f"AllVideo ответил {response.status_code}")
        resolution = self.extract_resolution(response.text)
        if resolution is None:
            return PlayerProbe(ref, False, reason="AllVideo не отдал качеств")
        return PlayerProbe(ref, True, resolution)

    @staticmethod
    def extract_resolution(html_text: str) -> int | None:
        """Highest resolution from AllVideo/Playerjs configuration or title."""
        resolutions: list[int] = []
        match = re.search(r'file:\s*["\']([^"\']+)["\']', html_text)
        if match:
            file_val = match.group(1)
            for quality in re.findall(r"\[(\d+)p?\]", file_val, re.IGNORECASE):
                resolutions.append(int(quality))
            if re.search(r"\[4k\]", file_val, re.IGNORECASE):
                resolutions.append(2160)
            for quality in re.findall(r"[-_](\d{3,4})p?\.(?:mp4|m3u8)",
                                      file_val, re.IGNORECASE):
                resolutions.append(int(quality))

        if not resolutions:
            title = re.search(r"<title>(.*?)</title>", html_text, re.IGNORECASE)
            title_text = title.group(1) if title else ""
            if re.search(r"\b(?:4k|2160p?)\b", title_text, re.IGNORECASE) \
                    or "4к" in title_text.lower():
                resolutions.append(2160)
            for quality in re.findall(r"\b(1080|720|480|360)p?\b", title_text,
                                      re.IGNORECASE):
                resolutions.append(int(quality))

        return max(resolutions) if resolutions else None

    @staticmethod
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


class VibixPlayer(PlayerAdapter):
    name = "vibix"
    series_scope = True

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        if "vibix-embed" not in text:
            return None
        return PlayerRef(name=self.name, url=page_url)


PLAYER_ORDER: tuple[PlayerAdapter, ...] = (VkPlayer(), AllVideoPlayer(), VibixPlayer())
