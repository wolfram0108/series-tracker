"""CDN VideoHub aggregator: VK-hosted voice-overs of a title (used by dunhuatv.ru).

The playlist lists every voice-over of a title; each item is a VK video whose signed
mp4 links are bound to the User-Agent of the request that issued them. An item has the numbers the player gives
(`season`, `episode`) and no text: the text of its row is made of those numbers, and nothing is made up for what the player
does not give. A player asks once and reads the answer; asking again is decided by `policy`.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from core.contracts import RowVoice
from core.proc import run_capture
from core.reasons import reason

from ..policy import ask
from .catalog import CatalogError, Entry
from .contract import Answer, Kind, PlayerRef, Reading
from .http import get_answer

API = "https://plapi.cdnvideohub.com/api/v1"
ORIGIN = "https://player.cdnvideohub.com"
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")
# The CDN rejects (HTTP 400) a stream request whose User-Agent differs from the API request.
STREAM_HEADERS = {"Origin": ORIGIN, "Referer": ORIGIN + "/", "User-Agent": USER_AGENT}
REQUEST_HEADERS = {**STREAM_HEADERS, "Accept": "application/json"}
REQUEST_TIMEOUT_S = 20.0
MEASURE_TIMEOUT = 40.0
DEFAULT_PUBLISHER = 1110

# Ascending quality; a None height means the rung label is not a resolution (measure the stream).
LADDER: tuple[tuple[str, int | None], ...] = (
    ("mpegTinyUrl", 144), ("mpegLowestUrl", 240), ("mpegLowUrl", 360),
    ("mpegMediumUrl", 480), ("mpegHighUrl", 720), ("mpegFullHdUrl", 1080),
    ("mpegQhdUrl", None), ("mpeg2kUrl", None), ("mpeg4kUrl", None),
)
LADDER_ORDER = {key: index for index, (key, _height) in enumerate(LADDER)}

_WIDGET = re.compile(r"<video-player\b([^>]*)>", re.IGNORECASE)
_ATTR = re.compile(r"""([\w-]+)\s*=\s*["']([^"']*)["']""")
_LINK = re.compile(r"^cvh=(.+)$")


class CdnVideoHubError(RuntimeError):
    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class VoiceVideo:
    """One item of the playlist: the VK video behind it, its voice-over, and the numbers the player gives it (empty or
    None where it gives none)."""

    cvh_id: str
    vk_id: str
    studio: str
    voice_type: str
    season: int | None
    episode: str


@dataclass(frozen=True)
class Rung:
    key: str
    height: int
    url: str


Measure = Callable[[Rung], Awaitable[int | None]]


def widget_of(text: str) -> tuple[int | None, int]:
    """The title and the publisher the page player is embedded with; no title when the page has no player."""
    found = _WIDGET.search(text)
    widget = dict(_ATTR.findall(found.group(1))) if found else {}
    title, publisher = widget.get("data-title-id", ""), widget.get("data-publisher-id", "")
    return (int(title) if title.isdigit() else None), (int(publisher) if publisher.isdigit() else DEFAULT_PUBLISHER)


def link_of(page_url: str, vk_id: str) -> str:
    return f"{page_url}#cvh={vk_id}"


def vk_id_of(url: str) -> str | None:
    """The video behind a candidate link."""
    found = _LINK.match(urlsplit(url).fragment)
    return found.group(1) if found else None


def _whole(value: object) -> int | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def _number_text(value: object) -> str:
    """A number of the player as it reads: 5 for 5.0, 5.5 for 5.5, nothing for what is not a number."""
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ""
    return str(int(number)) if number.is_integer() else str(number)


async def playlist_request(http: httpx.AsyncClient, title_id: int, publisher_id: int) -> Answer:
    """One request for the playlist of a title."""
    return await get_answer(http, f"{API}/player/sv/playlist", "", headers=REQUEST_HEADERS,
                            params={"pub": publisher_id, "aggr": "mali", "id": title_id}, timeout=REQUEST_TIMEOUT_S)


def read_playlist(answer: Answer) -> list[VoiceVideo]:
    """Every item of the title that has a video, in the order the aggregator lists them; none is dropped for a number it
    lacks or for being a second upload of the same episode."""
    if answer.status is None:
        raise CdnVideoHubError(reason("player_unreachable", player="CVH", error=answer.error), transient=True)
    if answer.status >= 500 or answer.status == 429:
        raise CdnVideoHubError(reason("player_status", player="CVH", status=answer.status), transient=True)
    if answer.status != 200 and answer.status != 204:
        raise CdnVideoHubError(reason("player_status", player="CVH", status=answer.status))
    if answer.status == 204 or not answer.text.strip():
        return []
    try:
        data = json.loads(answer.text)
    except ValueError as exc:
        raise CdnVideoHubError(reason("player_not_json", player="CVH")) from exc
    items: list[VoiceVideo] = []
    for raw in (data or {}).get("items") or []:
        vk_id = raw.get("vkId")
        if not vk_id:
            continue
        items.append(VoiceVideo(
            cvh_id=str(raw.get("cvhId") or "").strip(), vk_id=str(vk_id), studio=str(raw.get("voiceStudio") or "").strip(),
            voice_type=str(raw.get("voiceType") or "").strip(), season=_whole(raw.get("season")),
            episode=_number_text(raw.get("episode"))))
    return items


def read_rungs(text: str) -> list[Rung]:
    """Direct mp4 links of a VK video; rungs above 1080p carry height 0 until measured."""
    sources = (json.loads(text) or {}).get("sources") or {}
    return [Rung(key, height or 0, sources[key]) for key, height in LADDER if sources.get(key)]


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
        _, out, _ = await run_capture(cmd, timeout=MEASURE_TIMEOUT)
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
    raise CdnVideoHubError(reason("vk_no_known_resolution"))


async def rung_within(rungs: list[Rung], cap: int, measure: Measure = measure_rung) -> Rung:
    """Best rung not above `cap` frame height; the lowest one above it when none fits.

    Rungs of unknown height (above 1080p) are measured only when nothing else is left."""
    known = [rung for rung in rungs if rung.height]
    fitting = [rung for rung in known if rung.height <= cap]
    if fitting:
        return max(fitting, key=lambda rung: rung.height)
    if known:
        return min(known, key=lambda rung: rung.height)
    return await best_rung(rungs, measure)


class CdnVideoHubPlayer:
    name = "cvh"
    label = "CVH"
    series_scope = True
    stream_headers = STREAM_HEADERS

    def __init__(self, measure: Measure | None = None) -> None:
        self._measure = measure or measure_rung
        # A stream does not change its height, so a rung is measured once.
        self._heights: dict[tuple[str, str], int | None] = {}

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        title, publisher = widget_of(text)
        return PlayerRef(self.name, page_url, f"{title}/{publisher}") if title is not None else None

    @staticmethod
    def ref_of(page_url: str, vk_id: str) -> PlayerRef:
        return PlayerRef("cvh", link_of(page_url, vk_id))

    @staticmethod
    def owns(url: str) -> bool:
        return vk_id_of(url) is not None

    async def entries(self, source: PlayerRef, http: httpx.AsyncClient, page_url: str) -> list[Entry]:
        """The playlist of the title, one entry for each item: the voice-over as the player names it, the season as
        a "season N" group when the player gives one (and nothing when it gives none), the text made of the episode number."""
        title, _, publisher = source.raw_url.partition("/")
        answer = await ask(lambda: playlist_request(http, int(title), int(publisher or DEFAULT_PUBLISHER)))
        try:
            items = read_playlist(answer)
        except CdnVideoHubError as exc:
            raise CatalogError(exc.reason, exc.transient) from exc
        return [Entry(
            key=item.cvh_id or item.vk_id, ref=self.ref_of(page_url, item.vk_id),
            text=f"{item.episode} серия" if item.episode else "",
            group=f"{item.season} сезон" if item.season is not None else "",
            voice=RowVoice(name=item.studio, kind=item.voice_type),
            site_fields={"season": item.season, "episode": item.episode, "vk_id": item.vk_id}) for item in items]

    def capped(self, max_height: int | None) -> "_Capped":
        """The same player asked for the one file a download takes under a height limit."""
        return _Capped(self, max_height)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self._look(ref, http, None)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error)
        if answer.status in (204, 404) or (answer.status == 200 and not answer.text.strip()):
            return Reading(Kind.NOT_FOUND, answer.status)
        return Reading(Kind.HTTP_ERROR, answer.status)

    async def _look(self, ref: PlayerRef, http: httpx.AsyncClient, cap: int | None) -> Answer:
        vk_id = vk_id_of(ref.url)
        if vk_id is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not a CVH link"))
        answer = await get_answer(http, f"{API}/player/sv/video/{vk_id}", "", headers=REQUEST_HEADERS,
                                  timeout=REQUEST_TIMEOUT_S)
        if answer.status != 200 or not answer.text.strip():
            return answer
        try:
            rungs = read_rungs(answer.text)
        except (ValueError, AttributeError):
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not JSON"))
        if not rungs:
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no direct links"))
        files = await self._files(rungs, vk_id, cap)
        if not files:
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no known resolution"))
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, tuple(sorted({height for height, _ in files})), files=files))

    async def _files(self, rungs: list[Rung], vk_id: str, cap: int | None) -> tuple[tuple[int, str], ...]:
        measure = self._cached_measure(vk_id)
        try:
            if cap:
                rung = await rung_within(rungs, cap, measure)
                return ((rung.height, rung.url),)
            best = await best_rung(rungs, measure)
        except CdnVideoHubError:
            return ()
        known = {(rung.height, rung.url) for rung in rungs if rung.height}
        return tuple(sorted(known | {(best.height, best.url)}))

    def _cached_measure(self, vk_id: str) -> Measure:
        async def measure(rung: Rung) -> int | None:
            key = (vk_id, rung.key)
            if key not in self._heights:
                self._heights[key] = await self._measure(rung)
            return self._heights[key]
        return measure


class _Capped:
    """A player asked for one file under a height limit, so that `settle` can ask it under the one policy."""

    series_scope = False

    def __init__(self, player: CdnVideoHubPlayer, max_height: int | None) -> None:
        self._player, self._cap = player, max_height
        self.name, self.label = player.name, player.label

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self._player._look(ref, http, self._cap)

    def read(self, answer: Answer) -> Reading:
        return self._player.read(answer)
