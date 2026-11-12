"""Static DASH with complete, separately addressed MP4 tracks (CO4K / SegmentBase).

Read XML ourselves: the signed BaseURL query must survive XML entity decoding exactly once.
Segmented, encrypted and multi-period presentations need a different downloader. An application can serve a Bilibili
TV audio address its CDN refuses at every ask (CO4K, 2026-11-12): only that audio is taken anew from Bilibili's public
player API for the same CID and length (`AudioRenewal`); the video stays the application's.
"""
from dataclasses import dataclass, field
import asyncio
import re
import time
from urllib.parse import parse_qs, urljoin, urlsplit
from xml.etree import ElementTree as ET

import httpx

from .http import USER_AGENT


class UnsupportedDash(ValueError):
    pass


def is_dash(head: bytes) -> bool:
    return re.search(rb"<(?:[\w.-]+:)?MPD(?:\s|>)", head[:4096]) is not None


@dataclass(frozen=True)
class Tracks:
    videos: tuple[str, ...] = field(repr=False)
    audio: str = field(repr=False)
    duration: float | None = None


def _television(found: Tracks) -> bool:
    urls = [urlsplit(url) for url in (*found.videos, found.audio)]
    return all((url.hostname or "").endswith(".bilivideo.com")
               and parse_qs(url.query).get("platform", [""])[0] in {"android_tv_yst", "tv"} for url in urls)


def _cid(address: str) -> str | None:
    url = urlsplit(address)
    if url.scheme not in {"http", "https"} or not (url.hostname or "").endswith((".bilivideo.com", ".akamaized.net")):
        return None
    match = re.fullmatch(r"/upgcxcode/\d+/\d+/(\d+)/\1[^/]*", url.path)
    return match[1] if match else None


class AudioRenewal:
    """Refresh only a refused TV audio URL, using the public API for the same CID.

    No login, preview, DRM or quality downgrade of the video. One API request per second, a short
    cache, and a cooldown on transport/rate-limit refusals keep a broken line from flooding the API.
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.lock = asyncio.Lock()
        self.cached: dict[str, tuple[str, float]] = {}
        self.missed: dict[str, float] = {}
        self.next_at = self.blocked_until = 0.0

    def reject(self, found: Tracks) -> None:
        cid = _cid(found.audio)
        if cid:
            self.cached.pop(cid, None)
            self.missed[cid] = self.clock() + 60

    async def get(self, http: httpx.AsyncClient, found: Tracks) -> str | None:
        cid = _cid(found.audio)
        if not cid or not _television(found) or not found.duration or any(_cid(url) != cid for url in found.videos):
            return None
        async with self.lock:
            now = self.clock()
            cached = self.cached.get(cid)
            if cached and cached[1] > now:
                return cached[0]
            if max(self.blocked_until, self.missed.get(cid, 0.0)) > now:
                return None
            await asyncio.sleep(max(0.0, self.next_at - now))
            self.next_at = self.clock() + 1.0
            self.missed[cid] = self.clock() + 60
            try:
                response = await http.get("https://api.bilibili.com/pgc/player/web/playurl",
                    params={"cid": cid, "qn": 16, "fnval": 16},
                    headers={"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/"},
                    timeout=httpx.Timeout(12, connect=6))
                if response.status_code != 200:
                    self.blocked_until = self.clock() + 60
                    return None
                payload = response.json()
            except (httpx.HTTPError, ValueError):
                self.blocked_until = self.clock() + 60
                return None
            if not isinstance(payload, dict) or payload.get("code") != 0:
                return None
            result = payload.get("result")
            if not isinstance(result, dict) or result.get("is_preview") != 0 or result.get("is_drm") is not False:
                return None
            try:
                seconds = float(result.get("timelength") or 0) / 1000
                if seconds <= 0 or abs(seconds - found.duration) > 2:
                    return None
                audio = (result.get("dash") or {}).get("audio") or []
                choices = [(int(track.get("bandwidth") or 0), track.get("base_url") or track.get("baseUrl") or "")
                           for track in audio if isinstance(track, dict)]
                choices = [(bandwidth, url) for bandwidth, url in choices if _cid(url) == cid]
                if not choices:
                    return None
                address = max(choices)[1]
                deadline = float(parse_qs(urlsplit(address).query).get("deadline", [time.time() + 90])[0])
                ttl = min(300, deadline - time.time() - 30)
                if ttl <= 0:
                    return None
            except (AttributeError, TypeError, ValueError):
                return None
            self.cached[cid] = (address, self.clock() + ttl)
            self.missed.pop(cid, None)
            return address


def tracks(text: str, address: str) -> Tracks:
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise UnsupportedDash("DASH: XML declarations of entities are unsupported")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise UnsupportedDash("DASH: invalid or incomplete manifest") from exc

    def name(node):
        return node.tag.rsplit("}", 1)[-1]

    def children(node, tag):
        return [child for child in node if name(child) == tag]

    def base(node, parent):
        urls = children(node, "BaseURL")
        try:
            return urljoin(parent, (urls[0].text or "").strip()) if urls else parent
        except ValueError as exc:
            raise UnsupportedDash("DASH: invalid track address") from exc

    if name(root) != "MPD" or root.get("type", "static") != "static":
        raise UnsupportedDash("DASH: only static presentations are supported")
    if any(name(node) in {"ContentProtection", "SegmentTemplate", "SegmentList"} for node in root.iter()):
        raise UnsupportedDash("DASH: encrypted or segmented tracks are unsupported")
    periods = children(root, "Period")
    if len(periods) != 1:
        raise UnsupportedDash("DASH: expected one period")
    period = periods[0]
    parent = base(period, base(root, address))
    videos, audios = [], []
    for adaptation in children(period, "AdaptationSet"):
        for representation in children(adaptation, "Representation"):
            mime = representation.get("mimeType", adaptation.get("mimeType", ""))
            kind = representation.get("contentType", adaptation.get("contentType", mime.split("/")[0]))
            if kind not in {"video", "audio"}:
                continue
            chain = (root, period, adaptation, representation)
            if not any(children(node, "BaseURL") for node in chain):
                raise UnsupportedDash("DASH: track has no file address")
            for node in chain:
                for segment in children(node, "SegmentBase"):
                    if any(init.get("sourceURL") for init in children(segment, "Initialization")):
                        raise UnsupportedDash("DASH: separate initialization file is unsupported")
            url = base(representation, base(adaptation, parent))
            try:
                parsed = urlsplit(url)
            except ValueError as exc:
                raise UnsupportedDash("DASH: invalid track address") from exc
            if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                raise UnsupportedDash("DASH: track address must be HTTP(S)")
            target = videos if kind == "video" else audios
            if url not in target:
                target.append(url)
    if not videos or not audios:
        raise UnsupportedDash("DASH: both video and audio tracks are required")
    if len(videos) > 8:
        raise UnsupportedDash("DASH: too many video representations to measure")
    if len(audios) > 1:
        # Which of several audio tracks an episode has is a choice nobody made: refused with its reason, not guessed.
        raise UnsupportedDash("DASH: several audio tracks")
    duration = None
    stated = root.get("mediaPresentationDuration") or period.get("duration") or ""
    match = re.fullmatch(r"PT(?:(\d+(?:\.\d+)?)H)?(?:(\d+(?:\.\d+)?)M)?(?:(\d+(?:\.\d+)?)S)?", stated)
    if match:
        duration = sum(float(value or 0) * scale for value, scale in zip(match.groups(), (3600, 60, 1))) or None
    return Tracks(tuple(videos), audios[0], duration)
