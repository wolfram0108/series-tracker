"""The site 1.ruchime.org: every entry of the list of episodes by every player its page shows, and the catalogs.

The table is the whole page: the rows of VK and AllVideo for each entry of the list, and a row for each entry of the
catalogs of the players that list a whole series (Vibix, Kodik), whatever the resolution already found and whatever the
source of a series switches off (the choices are applied to the table afterwards). A row carries the text of the site as it
is; no number of an episode or a season is made here. The page of an episode and a catalog are parts of the page: one that
cannot be read is named in the table, and the core keeps the rows it gave the read before.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse

import httpx

from core.contracts import RowVoice, TableRow, UnreadPart
from core.reasons import reason

from ...players.asking import ask_many, outcome_of
from ...players.contract import Player, PlayerRef
from ...players.registry import PlayerRegistry
from .. import PHASE_EPISODES, PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft
from ..catalogs import AskCount, read_catalogs, rows_of_catalogs
from ..recent import last_of_each
from .pages import (MAX_CONCURRENT_PAGES, EpisodePage, PageReader, episode_url, extract_slug, published_of,
                    release_episodes, release_names, release_voice)


class RuchimeSite:
    name = "ruchime"
    domains = ("ruchime.org",)
    source_type = "web_video"

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()
        self._pages = PageReader()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        """The players of the module, so that the one Vibix (its browser capture and keys) serves every site."""
        if players is not None:
            self.players = players

    def match_url(self, url: str) -> bool:
        return "/release/" in urlparse(url).path

    def canonical_url(self, url: str) -> str:
        """The page of the release alone: the choices of a source (the Vibix season, the players off) do not change it."""
        return f"https://1.ruchime.org/release/{extract_slug(url)}"

    async def read(self, url: str, ctx: ReadContext) -> TableDraft:
        slug = extract_slug(url)
        ctx.progress(PHASE_RELEASE, 0, 1)
        try:
            release = await self._pages.read_release(slug, ctx.http)
        except httpx.HTTPStatusError as exc:
            raise SiteError(reason("site_status", site="Ruchime", status=exc.response.status_code, url=url)) from exc
        except httpx.HTTPError as exc:
            raise SiteError(reason("site_unreachable", site="Ruchime", error=type(exc).__name__)) from exc
        listed = last_of_each(release_episodes(release.text), lambda entry: None, ctx.recent)
        if not listed:
            raise SiteError(reason("title_no_videos", player="Ruchime"))
        ctx.progress(PHASE_RELEASE, 1, 1)

        site_voice = release_voice(release.text)
        pages = await self._read_pages(slug, listed, ctx)
        unread = [UnreadPart(part=page.url, names=[str(entry.get("title") or entry.get("number") or "")],
                             reason=page.failure) for entry, page in zip(listed, pages) if page is not None and page.failure]
        read = [page if page is not None and not page.failure else None for page in pages]
        rows, catalogs = await self._rows(slug, listed, read, site_voice, ctx)
        return TableDraft(names=release_names(release.text, slug), site_voice=site_voice, rows=rows,
                          unread=[*unread, *catalogs])

    async def _read_pages(self, slug: str, listed: list[dict], ctx: ReadContext) -> list[EpisodePage | None]:
        """The page of every entry that has one; a page that could not be read says why (`failure`)."""
        sem = asyncio.Semaphore(MAX_CONCURRENT_PAGES)
        total, done = len(listed), 0
        every = list(self.players)

        async def read_one(entry: dict) -> EpisodePage | None:
            nonlocal done
            address = episode_url(slug, entry)
            page = await self._pages.read_episode(address, ctx.http, sem, every) if address else None
            done += 1
            ctx.progress(PHASE_EPISODES, done, total)
            return page

        ctx.progress(PHASE_EPISODES, 0, total)
        return list(await asyncio.gather(*(read_one(entry) for entry in listed)))

    async def _rows(self, slug: str, listed: list[dict], pages: list[EpisodePage | None], site_voice: str,
                    ctx: ReadContext) -> tuple[list[TableRow], list[UnreadPart]]:
        """The rows of the players of the pages (one ref per entry, the page its part), then the rows of the catalogs the
        pages show; and the catalogs that could not be read."""
        pacer = self.players.pacer
        page_players = [player for player in self.players if not player.series_scope and player.name not in ctx.off]
        sources: dict[str, list[tuple[int, dict, EpisodePage]]] = {player.name: [] for player in page_players}
        for index, (entry, page) in enumerate(zip(listed, pages)):
            for player in page_players:
                if page is not None and player.name in page.refs:
                    sources[player.name].append((index, entry, page))

        catalogs = await read_catalogs(
            self.players, [(page.url, page.refs) for page in pages if page is not None], ctx)
        # A video that two entries share (a compilation) is asked once.
        asked = AskCount(ctx, sum(len({page.refs[name] for _index, _entry, page in found}) for name, found in sources.items())
                         + sum(len(catalog.entries) for catalog in catalogs.read))
        ctx.progress(PHASE_PLAYERS, 0, asked.total)
        rows: list[TableRow] = []
        for player in page_players:
            found = sources[player.name]
            refs = [page.refs[player.name] for _index, _entry, page in found]
            pages_of = {page.refs[player.name]: page.url for _index, _entry, page in found}
            verdicts = await ask_many(player, refs, ctx.http, lambda ref: pages_of[ref], pacer, on_asked=asked)
            rows += [self._row(player, index, entry, page.refs[player.name], verdicts[page.refs[player.name]], site_voice
                               ).model_copy(update={"part": page.url}) for index, entry, page in found]
        return rows + await rows_of_catalogs(catalogs.read, ctx, pacer, asked), catalogs.unread

    def _row(self, player: Player, index: int, entry: dict, ref: PlayerRef, verdict, site_voice: str) -> TableRow:
        outcome = outcome_of(verdict, ref, getattr(player, "label", player.name))
        link = getattr(player, "download_address", lambda given: None)(ref) or ref.url
        # The id of the entry is the stable part of the key; the place in the list stands in only for an entry without one.
        ident = entry.get("id") if entry.get("id") is not None else f"#{index}"
        return TableRow(
            key=f"{player.name}:{ident}", player=player.name, player_label=getattr(player, "label", ""),
            text=str(entry.get("title") or ""), voice=RowVoice(name=site_voice), heights=outcome.heights,
            state=outcome.state, reason=outcome.reason, published=published_of(entry.get("date")), link=link,
            site_fields={name: entry.get(name) for name in
                         ("id", "number", "compilation_from", "compilation_to", "is_trailer", "is_vip")})
