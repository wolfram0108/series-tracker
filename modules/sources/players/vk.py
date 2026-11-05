"""VK video: find it on a release page, ask it, read the answer, say which files it has.

The player of the site asks VK with the key of its own link, so this player asks the same way: the batch call
`video.get` with a web token that the player page issues, then the files the page lists itself, and the page call of a
video only as one more source of files. A refusal of the page call proves nothing, since it refuses videos that open
by their key alone; only an explicit refusal about the video, from the API or printed by the player page, closes it.
"""
from __future__ import annotations

import html
import logging
import re
import time
from dataclasses import dataclass
from typing import Sequence
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

import httpx

from core.reasons import reason

from ..policy import probe_from, settle
from .contract import EMBED_PAGE, Answer, Kind, PlayerRef, Reading, StreamUnavailable
from .http import USER_AGENT, get_answer, post_answer
from .vk_files import (ApiContext, EmbedParts, api_context, call_form, chunks, embed_parts, pick_file, read_call, read_item,
                       read_page, read_single)
from .vk_show import ENDPOINT, read_show

logger = logging.getLogger("sources")

NEUTRAL_AGENT = "Mozilla/5.0"
DESKTOP_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
# VK picks the player page by the browser it sees, so a token is looked for under both agents.
AGENTS = (NEUTRAL_AGENT, DESKTOP_AGENT)
# Players of different episodes the token is looked for in: first, middle, last.
TOKEN_SEEDS = 3
# A token is let go this long before the end of its life, and lives this long when the page names no end.
TOKEN_MARGIN_S = 60.0
TOKEN_LIFETIME_S = 300.0
_SHOW_HEADERS = {"Referer": ENDPOINT, "X-Requested-With": "XMLHttpRequest"}


@dataclass(frozen=True)
class _Session:
    context: ApiContext
    agent: str
    valid_until: float


def _sure(reading: Reading, want_files: bool) -> bool:
    """A reading nothing more is asked for: a refusal about the video, or files (with addresses when they are wanted)."""
    if reading.kind in (Kind.RESTRICTED, Kind.NOT_FOUND):
        return True
    return reading.kind is Kind.PLAYABLE and (bool(reading.files) or not want_files)

# The address of a video on VK: `vk.com/video<oid>_<id>`.
_VIDEO_PATH = re.compile(r"/video(-?\d+)_(\d+)")


class VkPlayer:
    name = "vk"
    label = "VK"
    series_scope = False

    _SOURCE = re.compile(
        r"switchSource\(['\"](https?://(?:vk\.com|vkvideo\.ru)/"
        r"video_ext\.php\?[^'\"]+)")

    def __init__(self) -> None:
        # A token belongs to the session it was issued in, which is the client that asked for the page.
        self._sessions: "WeakKeyDictionary[httpx.AsyncClient, _Session]" = WeakKeyDictionary()

    def find(self, text: str, page_url: str) -> PlayerRef | None:
        match = self._SOURCE.search(text)
        if not match:
            return None
        return self.ref_of(html.unescape(match.group(1)))

    @staticmethod
    def ref_of(url: str) -> PlayerRef:
        parts = embed_parts(url)
        canonical = f"https://vk.com/video{parts.oid}_{parts.id}" if parts else url
        return PlayerRef(name="vk", url=canonical, raw_url=url)

    @staticmethod
    def video_of(url: str) -> str:
        """`oid_id`: of a player link (`video_ext.php`) or of the address of the video (`vk.com/video<oid>_<id>`)."""
        parts = embed_parts(url)
        if parts is not None:
            return f"{parts.oid}_{parts.id}"
        found = _VIDEO_PATH.search(urlsplit(url).path)
        return f"{found.group(1)}_{found.group(2)}" if found else ""

    @staticmethod
    def owns(url: str) -> bool:
        """A player link with a key: the address the site's own player gets."""
        parts = embed_parts(url)
        return parts is not None and parts.hash is not None

    def download_address(self, ref: PlayerRef) -> str | None:
        """What the downloader is given: the player link with its key, asked again for fresh files when the download starts."""
        return ref.raw_url if self.owns(ref.raw_url) else None

    async def stream(self, url: str, http: httpx.AsyncClient, max_height: int | None, pacer) -> dict:
        """A player link with its key -> the address of one file now."""
        ref = self.ref_of(url)
        verdict = await settle(self.with_files(), ref, http, EMBED_PAGE, pacer=pacer)
        probe = probe_from(verdict, ref, "VK")
        picked = pick_file(verdict.reading.files, max_height) if verdict.reading.kind is Kind.PLAYABLE else None
        if picked is None:
            raise StreamUnavailable(probe.reason or reason("player_unconfirmed", player="VK"),
                                    transient=probe.transient or probe.available)
        return {"stream_url": picked[1], "headers": {}, "progressive": True}

    def with_files(self) -> "_WithFiles":
        """The same player asked for the addresses of the files too, which a download needs and a probe does not."""
        return _WithFiles(self)

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self._answer(ref, http, page_url, want_files=False)

    def read(self, answer: Answer) -> Reading:
        if answer.reading is not None:
            return answer.reading
        return Reading(Kind.NO_ANSWER, message=answer.error)

    async def read_batch(self, refs: Sequence[PlayerRef], http: httpx.AsyncClient, page_url: str) -> dict[PlayerRef, Reading]:
        """What the batch call settles for these players; a video it omits or leaves unsure is left to `fetch`."""
        wanted = [(ref, parts) for ref in refs if (parts := embed_parts(ref.raw_url)) is not None and parts.hash]
        if len(wanted) < 2:
            return {}
        session = self._session(http) or await self._bootstrap(http, [ref for ref, _ in wanted], page_url)
        if session is None:
            logger.info("VK: the player pages issued no token, so %d players are asked one by one", len(wanted))
        refreshed, settled = False, {}
        for part in chunks(wanted):
            if session is None:
                break
            called, rejected = await self._call(http, session, [parts for _, parts in part], page_url, part[0][0].raw_url)
            if rejected and not refreshed:
                refreshed = True
                self._sessions.pop(http, None)
                session = await self._bootstrap(http, [ref for ref, _ in wanted], page_url)
                if session is None:
                    break
                called, rejected = await self._call(http, session, [parts for _, parts in part], page_url, part[0][0].raw_url)
            if called is None or called.error is not None:
                continue
            for ref, parts in part:
                item = called.items.get(parts.video)
                if item is not None and (reading := read_item(item)).kind in (Kind.PLAYABLE, Kind.RESTRICTED):
                    settled[ref] = reading
        return settled

    async def _answer(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, want_files: bool) -> Answer:
        reading, retry_after = await self._look(ref, http, page_url, want_files)
        return Answer(reading.status or 200, retry_after=retry_after, reading=reading)

    def _session(self, http: httpx.AsyncClient) -> _Session | None:
        session = self._sessions.get(http)
        if session is not None and session.valid_until <= time.time():
            self._sessions.pop(http, None)
            return None
        return session

    def _remember(self, http: httpx.AsyncClient, context: ApiContext, agent: str) -> _Session:
        lifetime = context.expires_at - time.time() - TOKEN_MARGIN_S if context.expires_at else TOKEN_LIFETIME_S
        session = _Session(context, agent, time.time() + max(lifetime, 0.0))
        self._sessions[http] = session
        return session

    async def _bootstrap(self, http: httpx.AsyncClient, refs: Sequence[PlayerRef], page_url: str) -> _Session | None:
        """The token the player page issues itself; the older page kind has none, and then there is nothing to ask the API with."""
        seeds = list(dict.fromkeys([refs[0], refs[len(refs) // 2], refs[-1]]))[:TOKEN_SEEDS]
        for seed in seeds:
            for agent in AGENTS:
                answer = await get_answer(http, seed.raw_url, page_url, agent)
                context = api_context(answer.text) if answer.status == 200 else None
                if context is not None:
                    return self._remember(http, context, agent)
        return None

    async def _call(self, http: httpx.AsyncClient, session: _Session, parts: Sequence[EmbedParts], page_url: str,
                    seed_url: str):
        """One `video.get`; (its answer or None, whether the token was refused)."""
        origin = f"https://{urlsplit(seed_url).netloc}"
        answer = await post_answer(http, f"https://{session.context.domain}/method/video.get",
                                   call_form(session.context, [one.key for one in parts], urlsplit(page_url).hostname or ""),
                                   {"Origin": origin, "Referer": seed_url, "User-Agent": session.agent})
        called = read_call(answer.text) if answer.status == 200 else None
        return called, bool(called and called.token_rejected)

    async def _single(self, http: httpx.AsyncClient, session: _Session, ref: PlayerRef, parts: EmbedParts,
                      page_url: str) -> tuple[Reading, bool]:
        answer = await post_answer(http, f"https://{session.context.domain}/method/video.get",
                                   call_form(session.context, [parts.key], urlsplit(page_url).hostname or ""),
                                   {"Origin": f"https://{urlsplit(ref.raw_url).netloc}", "Referer": ref.raw_url,
                                    "User-Agent": session.agent})
        if answer.status is None:
            return Reading(Kind.NO_ANSWER, message=answer.error), False
        if answer.status != 200:
            return Reading(Kind.HTTP_ERROR, answer.status), False
        called = read_call(answer.text)
        return read_single(called, parts.video), bool(called and called.token_rejected)

    async def _look(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str,
                    want_files: bool) -> tuple[Reading, float | None]:
        """One video through its entries, in order: the API with a token, the player page, the page call of the video."""
        parts = embed_parts(ref.raw_url) or embed_parts(ref.url)
        if parts is None:
            return Reading(Kind.NO_ANSWER, message="no video in the address"), None
        unsure, retry_after = Reading(Kind.NOT_PLAYER, message="no entry answered"), None
        session = self._session(http)
        if session is not None:
            reading, rejected = await self._single(http, session, ref, parts, page_url)
            if rejected:
                self._sessions.pop(http, None)
            if _sure(reading, want_files):
                return reading, None
            unsure = reading
        page = await get_answer(http, ref.raw_url, page_url)
        retry_after = page.retry_after
        if page.status is None:
            unsure = Reading(Kind.NO_ANSWER, message=page.error)
        else:
            reading = read_page(page.status, page.text, parts.video)
            if _sure(reading, want_files):
                return reading, None
            unsure = reading
            context = api_context(page.text) if page.status == 200 else None
            if context is not None:
                fresh = self._remember(http, context, USER_AGENT)
                reading, rejected = await self._single(http, fresh, ref, parts, page_url)
                if rejected:
                    self._sessions.pop(http, None)
                if _sure(reading, want_files):
                    return reading, None
                if reading.kind is Kind.HTTP_ERROR:
                    unsure = reading
        shown = await post_answer(http, ENDPOINT, {"act": "show", "video": f"{parts.oid}_{parts.id}", "al": 1}, _SHOW_HEADERS)
        if shown.status == 200:
            reading = read_show(200, shown.text)
            if reading.kind is Kind.PLAYABLE and (reading.files or not want_files):
                return reading, None
        return unsure, retry_after


class _WithFiles:
    """A player asked for the files, so that `settle` can ask it under the one policy."""

    series_scope = False

    def __init__(self, player: VkPlayer) -> None:
        self._player, self.name = player, player.name

    async def fetch(self, ref: PlayerRef, http: httpx.AsyncClient, page_url: str) -> Answer:
        return await self._player._answer(ref, http, page_url, want_files=True)

    def read(self, answer: Answer) -> Reading:
        return self._player.read(answer)
