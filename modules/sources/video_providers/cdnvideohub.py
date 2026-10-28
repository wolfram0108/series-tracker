"""CDN VideoHub aggregator: VK-hosted voice-overs of a title (used by dunhuatv.ru).

The playlist lists every voice-over of a title; each item is a VK video whose signed
mp4 links are bound to the User-Agent of the request that issued them.
"""
from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from .retry import send_with_retry

API = "https://plapi.cdnvideohub.com/api/v1"
ORIGIN = "https://player.cdnvideohub.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
# The CDN rejects (HTTP 400) a stream request whose User-Agent differs from the API request.
STREAM_HEADERS = {"Origin": ORIGIN, "Referer": ORIGIN + "/", "User-Agent": USER_AGENT}
REQUEST_HEADERS = {**STREAM_HEADERS, "Accept": "application/json"}
REQUEST_TIMEOUT = 20.0
MEASURE_TIMEOUT = 40.0

# Ascending quality; a None height means the rung label is not a resolution (measure the stream).
LADDER: tuple[tuple[str, int | None], ...] = (
    ("mpegTinyUrl", 144), ("mpegLowestUrl", 240), ("mpegLowUrl", 360),
    ("mpegMediumUrl", 480), ("mpegHighUrl", 720), ("mpegFullHdUrl", 1080),
    ("mpegQhdUrl", None), ("mpeg2kUrl", None), ("mpeg4kUrl", None),
)


class CdnVideoHubError(RuntimeError):
    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class VoiceVideo:
    """One voice-over of one episode: the VK video behind it."""

    vk_id: str
    studio: str
    voice_type: str
    season: int
    episode: int


@dataclass(frozen=True)
class Rung:
    key: str
    height: int
    url: str


Measure = Callable[[Rung], Awaitable[int | None]]


def _whole(value: object) -> int | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


async def _get_json(http: httpx.AsyncClient, url: str, params: dict | None = None) -> dict | None:
    try:
        response = await send_with_retry(lambda: http.get(
            url, params=params, headers=REQUEST_HEADERS, timeout=REQUEST_TIMEOUT,
            follow_redirects=True))
    except httpx.HTTPError as exc:
        raise CdnVideoHubError(f"CDN VideoHub недоступен: {exc}", transient=True) from exc
    if response.status_code >= 500 or response.status_code == 429:
        raise CdnVideoHubError(f"CDN VideoHub ответил {response.status_code}", transient=True)
    if response.status_code == 204:
        return None
    if response.status_code != 200:
        raise CdnVideoHubError(f"CDN VideoHub ответил {response.status_code}")
    if not response.text.strip():
        return None
    try:
        return response.json()
    except ValueError as exc:
        raise CdnVideoHubError("CDN VideoHub вернул не JSON") from exc


async def fetch_playlist(http: httpx.AsyncClient, title_id: int,
                         publisher_id: int) -> list[VoiceVideo]:
    """Every playable voice-over item of the title, in the order the aggregator lists them."""
    data = await _get_json(http, f"{API}/player/sv/playlist",
                           {"pub": publisher_id, "aggr": "mali", "id": title_id})
    items: dict[tuple[str, str, int, int], VoiceVideo] = {}
    for raw in (data or {}).get("items") or []:
        vk_id, episode = raw.get("vkId"), _whole(raw.get("episode"))
        if not vk_id or episode is None:
            continue
        item = VoiceVideo(
            vk_id=str(vk_id), studio=str(raw.get("voiceStudio") or "").strip(),
            voice_type=str(raw.get("voiceType") or "").strip(),
            season=_whole(raw.get("season")) or 1, episode=episode)
        # Several records for one studio and episode: the last one is the newer upload.
        items[(item.studio, item.voice_type, item.season, item.episode)] = item
    return list(items.values())


async def fetch_rungs(http: httpx.AsyncClient, vk_id: str) -> list[Rung]:
    """Direct mp4 links of a VK video; rungs above 1080p carry height 0 until measured."""
    data = await _get_json(http, f"{API}/player/sv/video/{vk_id}")
    if not data:
        raise CdnVideoHubError("VK-видео недоступно")
    sources = data.get("sources") or {}
    rungs = [Rung(key, height or 0, sources[key]) for key, height in LADDER
             if sources.get(key)]
    if not rungs:
        raise CdnVideoHubError("у VK-видео нет прямых ссылок")
    return rungs


async def measure_height(url: str) -> int | None:
    """Real frame height of a stream via ffprobe; None when it cannot be read."""
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    blob = "".join(f"{key}: {value}\r\n" for key, value in STREAM_HEADERS.items()
                   if key != "User-Agent")
    cmd = [executable, "-v", "error", "-user_agent", USER_AGENT, "-headers", blob,
           "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(proc.communicate(), MEASURE_TIMEOUT)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        return int(out.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None


async def measure_rung(rung: Rung) -> int | None:
    return await measure_height(rung.url)


async def best_rung(rungs: list[Rung], measure: Measure = measure_rung) -> Rung:
    """Highest rung whose real height is known; an unmeasurable top rung yields to the next one."""
    for rung in sorted(rungs, key=lambda r: LADDER_ORDER[r.key], reverse=True):
        if rung.height:
            return rung
        height = await measure(rung)
        if height:
            return Rung(rung.key, height, rung.url)
    raise CdnVideoHubError("у VK-видео нет ссылки с известным разрешением")


LADDER_ORDER = {key: index for index, (key, _height) in enumerate(LADDER)}
