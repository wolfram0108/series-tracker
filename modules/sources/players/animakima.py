"""The own player of animakima.ru: the HLS files of an episode on the CDN of the site (linglongstrim.ru), outside VK.

The site gives what a player shows through its API (`get_player_view`): for this player, the files of one episode as a list
of `[<label>]<address>`; the addresses are signed for some hours, so they are asked right before a download and never kept.
A label is not a frame height (the files labelled 360p, 720p and 1080p of one episode were all measured at 1280x720): every
file is measured, and of the files of one frame the one the site lists later is taken (it lists them in rising bitrate).
For an episode it has no file of, the site answers with the address of its intro and a message: that is a missing file.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urlsplit

import httpx

from core.reasons import reason

from .contract import Answer, Kind, PlayerRef, Reading
from .frames import frame_height
from .http import USER_AGENT, post_answer
from .streaming import listed_stream

SITE = "https://animakima.ru"
API = f"{SITE}/api/"
REQUEST_TIMEOUT_S = 20.0
STREAM_HEADERS = {"Referer": SITE + "/", "User-Agent": USER_AGENT}
# The kind of view the site names this player by.
VIEW = "main"

_FILE = re.compile(r"\[([^\]]*)\]\s*(\S+?)\s*(?:,|$)")
_LINK = re.compile(r"^animakima=(\d+)/(\d+)$")
# What names a file whatever its signature: the part of the address after the signed prefix of an edge node.
_STABLE = re.compile(r"/videos/.+$")

Measure = Callable[[str], Awaitable[int | None]]


class AnimakimaError(RuntimeError):
    """Why the API gave no view, as a coded reason; `transient` marks causes that may pass."""

    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class View:
    """What the API answered for one player of a title: `data` is what the page would put in place of the player."""
    data: str
    message: str = ""


async def view_request(http: httpx.AsyncClient, aid: str, kind: str, sid: str = "", referer: str = SITE + "/") -> Answer:
    """One request for the view of a player of a title (of one episode, for a player that shows one at a time)."""
    return await post_answer(http, API, {"action": "get_player_view", "aid": aid, "type": kind, "sid": sid},
                             {"Referer": referer, "Origin": SITE}, timeout=REQUEST_TIMEOUT_S)


def read_view(answer: Answer, site: str) -> View:
    """The view an answer carries; the error says why there is none, and whether asking again may help."""
    if answer.status is None:
        raise AnimakimaError(reason("site_unreachable", site=site, error=answer.error), transient=True)
    if answer.status >= 500 or answer.status == 429:
        raise AnimakimaError(reason("site_status", site=site, status=answer.status, url=API), transient=True)
    if answer.status != 200:
        raise AnimakimaError(reason("site_status", site=site, status=answer.status, url=API))
    try:
        data = json.loads(answer.text)
    except ValueError as exc:
        raise AnimakimaError(reason("player_not_json", player=site)) from exc
    if not isinstance(data, dict) or data.get("success") is not True:
        message = str(data.get("message") or "") if isinstance(data, dict) else ""
        raise AnimakimaError(reason("site_refused", site=site, message=message))
    return View(str(data.get("data") or ""), str(data.get("message") or ""))


def files_of(data: str) -> list[tuple[str, str]]:
    """(label, address) of each file of the list, in the order of the list."""
    return [(label.strip(), address) for label, address in _FILE.findall(data) if address.startswith("http")]


def link_of(page_url: str, aid: str, sid: str) -> str:
    return f"{page_url}#animakima={aid}/{sid}"


def parse_link(url: str) -> tuple[str, str] | None:
    found = _LINK.match(urlsplit(url).fragment)
    return (found.group(1), found.group(2)) if found else None


def stable_name(url: str) -> str:
    found = _STABLE.search(urlsplit(url).path)
    return found.group(0) if found else urlsplit(url).path


async def measure_file(url: str) -> int | None:
    return await frame_height(url, STREAM_HEADERS)


class AnimakimaPlayer:
    name = "animakima"
    label = "Animakima"
    # Its list is the list of episodes of the page, which the site of animakima reads: no page shows it as a catalog.
    series_scope = False
    stream_headers = STREAM_HEADERS

    def __init__(self, measure: Measure | None = None) -> None:
        self._measure = measure or measure_file
        # A file does not change its frame, so each is measured once; a failed measure is tried again next time.
        self._heights: dict[str, int] = {}

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        """No page embeds this player: the site of animakima makes its links from the episodes of the page."""
        return None

    @staticmethod
    def ref_of(page_url: str, aid: str, sid: str) -> PlayerRef:
        return PlayerRef("animakima", link_of(page_url, aid, sid))

    @staticmethod
    def owns(url: str) -> bool:
        return parse_link(url) is not None

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        return await listed_stream(self, url, http, max_height, pacer)

    def capped(self, max_height: int | None) -> "AnimakimaPlayer":
        """Every file of an episode is listed, so the cap is applied when one is picked, not when it is asked for."""
        return self

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        target = parse_link(ref.url)
        if target is None:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message="not an animakima link"))
        aid, sid = target
        answer = await view_request(http, aid, VIEW, sid, referer=page_url)
        if answer.status != 200:
            return answer
        try:
            view = read_view(answer, "animakima.ru")
        except AnimakimaError as exc:
            return Answer(200, reading=Reading(Kind.NOT_PLAYER, 200, message=exc.reason))
        listed = files_of(view.data)
        if not listed:
            # The site words a link it has no file for ("link invalid") and gives its intro instead.
            kind = Kind.NOT_FOUND if view.message else Kind.NOT_PLAYER
            return Answer(200, reading=Reading(kind, 200, message=view.message or "no files"))
        files = await self._files(listed)
        if not files:
            return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no known resolution"))
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, tuple(sorted(files)), files=tuple(sorted(files.items()))))

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error)
        if answer.status == 404:
            return Reading(Kind.NOT_FOUND, answer.status)
        return Reading(Kind.HTTP_ERROR, answer.status)

    async def _files(self, listed: list[tuple[str, str]]) -> dict[int, str]:
        """The measured frame of each file; of the files of one frame, the one listed later (the higher bitrate)."""
        files: dict[int, str] = {}
        for _label, url in listed:
            key = stable_name(url)
            height = self._heights.get(key) or await self._measure(url)
            if height:
                self._heights[key] = height
                files[height] = url
        return files
