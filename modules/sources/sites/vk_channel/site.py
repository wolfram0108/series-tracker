"""The site "channel of VK": the videos of a channel whose title has a term of the query, one row each, with the steps of
their files.

The channel is read whole and the query is looked for in the titles of the listing. The search of VK is a ranking, not a
list: it loses videos even when its cursor reaches the end (measured 2026-11-05), so it is not used. A channel with no
query is not read.

The listing is `video.get` of the channel by the positions of the API, a page of `PAGE_SIZE` at a time; VK leaves out the
videos that are not available, even a whole page. It is the whole channel when every page was read and the count VK
states stayed the same from the first page to the last: an upload or a removal during the read shifts the positions, so a
count that moved reads the listing again. A malformed or repeated answer, more videos than the count, or a listing past
the limit of pages end the read with a reason: a part is never given as the whole.

One listing of a channel serves the tables of every series that looks for something in it within the term of the module
(term.py):
a channel holds thousands of videos and several series share one. Reads of one channel at the same time share one
listing, and each of them is told its progress. A read the user asked for (`ReadContext.fresh`) lists the channel anew.

A video without files is asked of the VK player by the key in the `player` link of the video, as the player of a page does.
The head of the table names the channel (`channel`), so that the line of the constructor tells apart two channels of VK in
one series. The text of a row is the title of the video as it is; the voice-over of a row is empty, the constructor names it.
Nothing here numbers an episode.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.asking import ask_many, outcome_of
from ...players.contract import PlayerRef
from ...players.registry import PlayerRegistry
from ...players.vk_files import embed_parts
from ...policy import Pacer
from ...term import Term
from .. import PHASE_PLAYERS, PHASE_VIDEOS, Progress, ReadContext, SiteError, TableDraft
from .api import (PAGE_SIZE, dedupe, error_code_of, heights_of, is_external, keep_by_terms, key_of, link_of,
                  owner_id_from_resolve, parse_address, published_of)

logger = logging.getLogger("sources")

# The gap between the pages of the API is a limit of the host (rate limit), not a way to wait for it. Two requests a second
# drew no refusal of VK over 700 requests (2026-11-05).
VK_PAGE_INTERVAL_S = 0.5
# The listing of a channel of 100 thousand videos fits; a listing that does not is an error, not a part.
LISTING_PAGES = 1000
# A channel whose count moves during each of this many listings is not given as a whole.
LISTING_READS = 3
# VK answers codes that a repeat of the same page mends: no code, a flood (6) and a failure of its own (10).
RETRY_CODES = (None, 6, 10)


@dataclass(frozen=True)
class Listing:
    """Every video of a channel the API gives, and when (by the monotonic clock) the listing was finished."""
    owner: int
    videos: list[dict]
    at: float


@dataclass
class _Flight:
    """One listing of a channel in progress and the reads that wait for it, each told the progress."""
    task: asyncio.Task
    watchers: list[Progress] = field(default_factory=list)


class VkChannelSite:
    name = "vk_channel"
    domains = ("vk.com", "vkvideo.ru", "vk.ru")
    source_type = "vk_video"

    def __init__(self, players: PlayerRegistry | None = None, vk_call=None,
                 clock: Callable[[], float] = time.monotonic, term: Term | None = None) -> None:
        self.players = players or PlayerRegistry.standard()
        self.term = term if term is not None else Term()
        self._call = vk_call
        self._pacer = Pacer(lambda: VK_PAGE_INTERVAL_S)
        self._clock = clock
        self._listings: dict[str, Listing] = {}
        self._flights: dict[str, _Flight] = {}

    def configure(self, *, players: PlayerRegistry | None = None, vk_call=None, term: Term | None = None,
                  **_wiring) -> None:
        """The players of the module, the way to the VK API (the token lives with the module that owns the sessions) and
        the term of what the sources answered."""
        if players is not None:
            self.players = players
        if term is not None:
            self.term = term
        if vk_call is not None:
            self._call = vk_call

    def match_url(self, url: str) -> bool:
        return parse_address(url) is not None

    def canonical_url(self, url: str) -> str:
        address = parse_address(url)
        if address is None:
            raise ValueError(f"not a channel address: {url!r}")
        return address.canonical

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        address = parse_address(url)
        if address is None:
            raise SiteError(reason("vk_bad_address"))
        if not address.terms:
            raise SiteError(reason("vk_query_missing"))
        ctx.progress(PHASE_VIDEOS, 0, 0)
        listing = await self._channel(address.name, ctx)
        videos = [video for video in keep_by_terms(listing.videos, address.terms) if not is_external(video)]
        if ctx.recent is not None:  # the newest videos of the channel, in the order of their dates
            videos = sorted(videos, key=lambda video: video.get("date") or 0)
            videos = videos[max(0, len(videos) - ctx.recent):]
        return TableDraft(channel=address.name, rows=await self._rows(videos, address.channel_url, ctx))

    async def _ask(self, method: str, params: dict) -> dict:
        if self._call is None:
            raise RuntimeError("the VK API is not wired to the channel site")
        return await self._call(method, params)

    # --- the listing of a channel ---------------------------------------------------------------------------------------

    async def _channel(self, name: str, ctx: ReadContext) -> Listing:
        """The listing of the channel: the one kept, while it is in its term and the user did not ask for a new one, else
        the one in progress, else a new one. A read that stops waiting does not stop the listing the others wait for."""
        kept = self._listings.get(name)
        if kept is not None and not ctx.fresh and self._clock() - kept.at < self.term.seconds:
            return kept
        flight = self._flights.get(name)
        if flight is None:
            flight = _Flight(asyncio.create_task(self._list(name), name=f"sources.vk_channel:{name}"))
            self._flights[name] = flight
            flight.task.add_done_callback(lambda done: self._land(name, done))
        flight.watchers.append(ctx.progress)
        try:
            return await asyncio.shield(flight.task)
        finally:
            if ctx.progress in flight.watchers:
                flight.watchers.remove(ctx.progress)

    def _land(self, name: str, done: asyncio.Task) -> None:
        if self._flights.get(name) is not None and self._flights[name].task is done:
            del self._flights[name]
        if not done.cancelled() and done.exception() is None:
            self._listings[name] = done.result()

    def _told(self, name: str) -> Progress:
        def progress(phase: str, current: int, total: int) -> None:
            flight = self._flights.get(name)
            for watcher in list(flight.watchers if flight else ()):
                watcher(phase, current, total)
        return progress

    async def _list(self, name: str) -> Listing:
        owner = owner_id_from_resolve(await self._ask("utils.resolveScreenName", {"screen_name": name}), name)
        progress = self._told(name)
        for _ in range(LISTING_READS):
            videos, counts = await self._pages(owner, progress)
            if len(set(counts)) > 1:
                logger.warning("VK %s: число роликов менялось за чтение (%d → %d) — читаю список снова", name, counts[0],
                               counts[-1])
                continue
            if len(videos) > counts[0]:
                raise SiteError(reason("vk_reply_unexpected", method="video.get"))
            logger.info("VK %s: список канала — %d роликов из %d (скрыто VK: %d)", name, len(videos), counts[0],
                        counts[0] - len(videos))
            return Listing(owner, videos, self._clock())
        raise SiteError(reason("vk_listing_moving", reads=LISTING_READS))

    async def _pages(self, owner: int, progress: Progress) -> tuple[list[dict], list[int]]:
        """Every video of the listing by the positions the API names, and the count VK stated on each page."""
        items: list[dict] = []
        counts: list[int] = []
        seen: set[tuple[str, ...]] = set()
        offset = 0
        for _ in range(LISTING_PAGES):
            response = await self._page("video.get", {"owner_id": owner, "offset": offset, "count": PAGE_SIZE})
            if (not isinstance(response, dict) or not isinstance(response.get("items"), list)
                    or type(response.get("count")) is not int or response["count"] < 0
                    or len(response["items"]) > PAGE_SIZE):
                raise SiteError(reason("vk_reply_unexpected", method="video.get"))
            counts.append(response["count"])
            if response["items"]:
                signature = tuple(key_of(video) for video in response["items"])
                if signature in seen:
                    raise SiteError(reason("vk_reply_unexpected", method="video.get"))
                seen.add(signature)
            items += response["items"]
            offset += PAGE_SIZE
            total = max(counts)
            progress(PHASE_VIDEOS, min(offset, total), total)
            if offset >= total:
                return dedupe(items), counts
        raise SiteError(reason("vk_listing_limit", pages=LISTING_PAGES))

    async def _page(self, method: str, params: dict) -> dict | None:
        """One page, asked again, up to three asks, when VK answers a code that a repeat mends."""
        for attempt in range(3):
            await self._pacer.wait()
            try:
                return (await self._ask(method, params)).get("response")
            except SiteError as exc:
                if attempt == 2 or error_code_of(exc) not in RETRY_CODES:
                    raise
                logger.warning("VK %s: ошибка страницы offset=%s, повтор %d/2: %s", method, params.get("offset"), attempt + 1, exc.reason)
        raise AssertionError("unreachable")

    # --- the rows -------------------------------------------------------------------------------------------------------

    async def _rows(self, videos: list[dict], channel_url: str, ctx: ReadContext) -> list[TableRow]:
        """A row per video. The steps come from the files the listing gave; what it gave none for is asked of the VK player."""
        refs = {key_of(video): self._ref(video) for video in videos if heights_of(video) is None}
        asked = {}
        sought = [ref for ref in refs.values() if ref is not None]
        if sought:
            done = 0

            def counted() -> None:
                nonlocal done
                done += 1
                ctx.progress(PHASE_PLAYERS, done, len(set(sought)))

            ctx.progress(PHASE_PLAYERS, 0, len(set(sought)))
            asked = await ask_many(self.players.get("vk"), sought, ctx.http, channel_url, self.players.pacer,
                                   on_asked=counted)
        rows = []
        for video in videos:
            heights = heights_of(video)
            if heights is not None:
                state, reason_text = RowState.PLAYABLE, None
            elif refs[key_of(video)] is None:
                heights, state, reason_text = [], RowState.NOT_OBTAINED, reason("no_data")
            else:
                outcome = outcome_of(asked[refs[key_of(video)]], refs[key_of(video)], "VK")
                heights, state, reason_text = outcome.heights, outcome.state, outcome.reason
            rows.append(TableRow(
                key=key_of(video), player="vk", player_label="VK", text=str(video.get("title") or ""),
                heights=heights, state=state, reason=reason_text, duration_s=video.get("duration"),
                published=published_of(video), link=link_of(video),
                site_fields={"owner_id": video.get("owner_id"), "id": video.get("id")}))
        return rows

    @staticmethod
    def _ref(video: dict) -> PlayerRef | None:
        """The player ref of a video by the key (`hash`) in its `player` link; None when the link has none."""
        parts = embed_parts(str(video.get("player") or ""))
        if parts is None or not parts.hash:
            return None
        raw = f"https://vkvideo.ru/video_ext.php?oid={parts.oid}&id={parts.id}&hash={parts.hash}"
        return PlayerRef("vk", f"https://vk.com/video{parts.oid}_{parts.id}", raw)
