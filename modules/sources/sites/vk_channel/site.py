"""The site "channel of VK": the videos of a channel that a query finds, one row each, with the steps of their files.

How the videos are listed (found on the snapshots of the API in `raw/run1`, `raw/run2_vk`):
- `video.search` with `owner_id` answers `count: 0` since 2026-10-22, so it is not used;
- `video.search` without `owner_id` searches all of VK and lists the videos with `files` (the steps `mp4_*`); the videos of
  other channels that share the words are left out by `owner_id` of the channel, which `utils.resolveScreenName` gives.
  This is the mode `search`: one search for each term of the query, the pages read to the end of the count VK reports;
- `video.get` of the channel lists every video of it (12 thousand on a big channel) but gives no `files`. This is the mode
  `get_all`: the videos whose title has a term of the query are kept, and the steps of each are asked of the VK player by the
  key in the `player` link of the video, as the player of a page does (a batch call with the token of the player page).
A video the listing gives without files is asked of the player in either mode. The text of a row is the title of the video
as it is; the voice-over of a row is empty, the constructor names it. Nothing here numbers an episode.
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
from .api import (PAGE_SIZE, SEARCH, dedupe, heights_of, is_external, keep_by_terms, key_of, link_of,
                  owner_id_from_resolve, parse_address, published_of)

logger = logging.getLogger("sources")

# The gap between the pages of the API is a limit of the host (rate limit), not a way to wait for it.
VK_PAGE_INTERVAL_S = 1.0


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
        if address.mode == SEARCH:
            videos: list[dict] = []
            for term in address.terms:
                found = await self._pages("video.search", {"q": term, "fields": "files"}, ctx)
                videos += [video for video in found if video.get("owner_id") == owner]
        else:
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

    async def _pages(self, method: str, params: dict, ctx: ReadContext) -> list[dict]:
        """Every page of a listing: the count VK reports ends it, a page that comes back short does not."""
        items: list[dict] = []
        offset, total = 0, None
        while total is None or offset < total:
            await self._pacer.wait()
            response = (await self._ask(method, {**params, "offset": offset, "count": PAGE_SIZE})).get("response") or {}
            total = int(response.get("count") or 0) if total is None else total
            items += response.get("items") or []
            offset += PAGE_SIZE
            ctx.progress(PHASE_VIDEOS, min(offset, total), total)
        return items

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
