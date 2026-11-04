"""The site "channel of VK": the videos of a channel that a query finds, one row each, with the steps of their files.

How the videos are found:
- the mode `search` is the channel search of VK Video itself: `catalog.getOwnerVideosSearch` with `owner_id` and the term, then
  `catalog.getBlockItems` with the opaque cursor of the block, one search for each term (`video.search` with `owner_id`
  answers `count: 0` since 2026-10-18, without it the search runs over all of VK and finds a part of the channel, so
  neither is used). The key of this API is a short-lived web token of the player page, which the module of the sessions
  keeps inside; it also keeps the interval between the requests. A search that is empty, partial or refused is not
  trusted: the channel is listed whole instead;
- `video.get` of the channel lists every video of it but gives no `files`. This is the mode `get_all`: the videos whose title
  has a term of the query are kept. A listing is read by the positions the API names (VK leaves out the videos that are not
  available, even a whole page) and ends in an error, never in a part, when the answer is malformed, repeats or does not fit
  the limit of pages.
A video without files is asked of the VK player by the key in the `player` link of the video, as the player of a page does.
The text of a row is the title of the video as it is; the voice-over of a row is empty, the constructor names it. Nothing here
numbers an episode.
"""
from __future__ import annotations

import logging
from typing import Mapping

from core.contracts import RowState, TableRow
from core.reasons import reason

from ...players.asking import ask_many, outcome_of
from ...players.contract import PlayerRef
from ...players.registry import PlayerRegistry
from ...players.vk_files import embed_parts
from ...policy import Pacer
from .. import PHASE_PLAYERS, PHASE_VIDEOS, ReadContext, SiteError, TableDraft
from .api import (PAGE_SIZE, SEARCH, dedupe, error_code_of, heights_of, is_external, keep_by_terms, key_of, link_of,
                  owner_id_from_resolve, parse_address, published_of, search_block, search_page)

logger = logging.getLogger("sources")

# The gap between the pages of the API is a limit of the host (rate limit), not a way to wait for it.
VK_PAGE_INTERVAL_S = 1.0
# The listing of a channel of 200 thousand videos fits; a listing that does not is an error, not a part.
LISTING_PAGES = 1000
# The pages of one search; a search that goes on is not trusted and the channel is listed.
SEARCH_PAGES = 100
# VK answers codes that a repeat of the same page mends: no code, a flood (6) and a failure of its own (10).
RETRY_CODES = (None, 6, 10)
NOT_FOUND = 104


class VkChannelSite:
    name = "vk_channel"
    domains = ("vk.com", "vkvideo.ru", "vk.ru")
    source_type = "vk_video"

    def __init__(self, players: PlayerRegistry | None = None, vk_call=None) -> None:
        self.players = players or PlayerRegistry.standard()
        self._call = vk_call
        self._pacer = Pacer(lambda: VK_PAGE_INTERVAL_S)

    def configure(self, *, players: PlayerRegistry | None = None, vk_call=None, **_wiring) -> None:
        """The players of the module and the way to the VK API (the token lives with the module that owns the sessions)."""
        if players is not None:
            self.players = players
        if vk_call is not None:
            self._call = vk_call

    def match_url(self, url: str) -> bool:
        return parse_address(url, {}) is not None

    def canonical_url(self, url: str, options: Mapping[str, str] = {}) -> str:
        address = parse_address(url, options)
        if address is None:
            raise ValueError(f"not a channel address: {url!r}")
        return address.canonical

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        address = parse_address(url, {})
        if address is None:
            raise SiteError(reason("vk_bad_address"))
        ctx.progress(PHASE_VIDEOS, 0, 0)
        owner = owner_id_from_resolve(await self._ask("utils.resolveScreenName", {"screen_name": address.name}),
                                      address.name)
        videos = await self._searched(owner, address.terms, ctx) if address.mode == SEARCH else None
        if videos is None:
            videos = keep_by_terms(await self._pages("video.get", {"owner_id": owner}, ctx), address.terms)
        videos = [video for video in dedupe(videos) if not is_external(video)]
        if ctx.recent is not None:  # the newest videos of the channel, in the order of their dates
            videos = sorted(videos, key=lambda video: video.get("date") or 0)
            videos = videos[max(0, len(videos) - ctx.recent):]
        return TableDraft(rows=await self._rows(videos, address.channel_url, ctx))

    async def _ask(self, method: str, params: dict) -> dict:
        if self._call is None:
            raise RuntimeError("the VK API is not wired to the channel site")
        return await self._call(method, params)

    async def _searched(self, owner: int, terms: tuple[str, ...], ctx: ReadContext) -> list[dict] | None:
        """The videos the quick search found, or None when it cannot be trusted: it found nothing, a part, or was refused.
        An empty answer is not believed because the search of VK has left out whole channels before."""
        found: list[dict] = []
        try:
            for term in terms:
                part, whole = await self._search(owner, term, ctx)
                if not whole:
                    logger.warning("VK %s: быстрый поиск %r неполон — читаю список канала", owner, term)
                    return None
                found += part
        except SiteError as exc:
            logger.warning("VK %s: быстрый поиск не ответил (%s) — читаю список канала", owner, exc.reason)
            return None
        return dedupe(found) or None

    async def _search(self, owner: int, term: str, ctx: ReadContext) -> tuple[list[dict], bool]:
        """One term through the catalog search: its videos, and whether the cursors led to the end of them."""
        response = (await self._ask("catalog.getOwnerVideosSearch", {"owner_id": owner, "q": term})).get("response") or {}
        block = search_block(response, owner)
        if block is None:
            return [], True
        items: list[dict] = []
        cursors: set[tuple[str, str]] = set()
        for page in range(SEARCH_PAGES):
            items += search_page(response, block)
            ctx.progress(PHASE_VIDEOS, len(items), len(items))
            cursor = block.get("next_from")
            if not cursor:
                return dedupe(items), True
            if not block.get("id") or (block["id"], cursor) in cursors:
                raise SiteError(reason("vk_reply_unexpected", method="catalog.getBlockItems"))
            cursors.add((block["id"], cursor))
            if page + 1 == SEARCH_PAGES:
                break
            # The sessions module paces the requests of this API: a pause here would count the time of the request twice.
            params = {"block_id": block["id"], "start_from": cursor}
            try:
                data = await self._ask("catalog.getBlockItems", params)
            except SiteError as exc:
                if error_code_of(exc) != NOT_FOUND:
                    raise
                # "Not found" is also what VK says of a continuation it has advertised and no longer has: ask once more,
                # then keep what was found and say it is a part.
                await self._pacer.wait()
                try:
                    data = await self._ask("catalog.getBlockItems", params)
                except SiteError as again:
                    if error_code_of(again) != NOT_FOUND or not items:
                        raise
                    return dedupe(items), False
            response = data.get("response") or {}
            block = response.get("block")
            if not isinstance(block, dict) or block.get("data_type") != "videos":
                raise SiteError(reason("vk_reply_unexpected", method="catalog.getBlockItems"))
        raise SiteError(reason("vk_listing_limit", pages=SEARCH_PAGES))

    async def _pages(self, method: str, params: dict, ctx: ReadContext) -> list[dict]:
        """Every video of a listing, by the positions the API names. A page that cannot be had, a malformed or repeated
        answer and a listing past the limit of pages end the read with a reason: a part is never given as the whole."""
        items: list[dict] = []
        offset, total, seen = 0, 0, set()
        for _ in range(LISTING_PAGES):
            response = await self._page(method, {**params, "offset": offset, "count": PAGE_SIZE})
            if (not isinstance(response, dict) or not isinstance(response.get("items"), list)
                    or type(response.get("count")) is not int or response["count"] < 0):
                raise SiteError(reason("vk_reply_unexpected", method=method))
            total = max(total, response["count"])
            if response["items"]:
                signature = tuple(key_of(video) for video in response["items"])
                if signature in seen:
                    raise SiteError(reason("vk_reply_unexpected", method=method))
                seen.add(signature)
            items += response["items"]
            offset += PAGE_SIZE
            ctx.progress(PHASE_VIDEOS, min(offset, total), total)
            if offset >= total:
                return dedupe(items)
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
