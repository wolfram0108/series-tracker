"""The site 1.ruchime.org: every entry of the list of episodes by every player its page shows, and the Vibix catalog.

The table is the whole page: the rows of VK and AllVideo for each entry of the list, and a row for each entry of the
Vibix catalog, whatever the resolution already found and whatever the source of a series switches off (the choices are
applied to the table afterwards). A row carries the text of the site as it is; no number of an episode or a season is made
here. Kodik, which the page also shows, is read together with dunhuatv, where its code is shared.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Mapping
from urllib.parse import urlparse

import httpx

from core.contracts import RowState, RowVoice, TableRow
from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from ...players.asking import ask_many, outcome_of
from ...players.contract import Player, PlayerRef
from ...players.registry import PlayerRegistry
from ...players.vibix import VibixPlayer, link_of
from ...players.vibix_protocol import Episode, VibixProtocolError, VibixUnavailable
from .. import (PHASE_CATALOG, PHASE_EPISODES, PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError, TableDraft,
                on_domain)
from .pages import (MAX_CONCURRENT_PAGES, EpisodePage, PageReader, episode_url, extract_slug, published_of,
                    release_episodes, release_names, release_voice)

logger = logging.getLogger("sources")

# One more capture of the catalog after a failed one: the capture is a whole browser session and the table is the work of
# minutes, so a passing failure is not allowed to throw it away.
CATALOG_BUDGET = Budget(attempts=2, pauses=(3.0,), limit_s=UNTIMED)


class RuchimeSite:
    name = "ruchime"
    domains = ("ruchime.org",)

    def __init__(self, players: PlayerRegistry | None = None) -> None:
        self.players = players or PlayerRegistry.standard()
        self._pages = PageReader()

    def configure(self, *, players: PlayerRegistry | None = None, **_wiring) -> None:
        """The players of the module, so that the one Vibix (its browser capture and keys) serves every site."""
        if players is not None:
            self.players = players

    def match_url(self, url: str) -> bool:
        return "/release/" in urlparse(url).path

    def canonical_url(self, url: str, options: Mapping[str, str] = {}) -> str:
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
        listed = release_episodes(release.text)
        if not listed:
            raise SiteError(reason("title_no_videos", player="Ruchime"))
        ctx.progress(PHASE_RELEASE, 1, 1)

        site_voice = release_voice(release.text)
        pages = await self._read_pages(slug, listed, ctx)
        rows = await self._rows(slug, listed, pages, site_voice, ctx)
        return TableDraft(names=release_names(release.text, slug), site_voice=site_voice, rows=rows)

    async def _read_pages(self, slug: str, listed: list[dict], ctx: ReadContext) -> list[EpisodePage | None]:
        """The page of every entry that has one; a table with an unread page is not the whole page, so it is refused."""
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
        pages = list(await asyncio.gather(*(read_one(entry) for entry in listed)))
        failed = [page for page in pages if page is not None and page.failure]
        if failed:
            raise SiteError(reason("pages_unread", site="Ruchime", count=len(failed), total=len(pages)))
        return pages

    async def _rows(self, slug: str, listed: list[dict], pages: list[EpisodePage | None], site_voice: str,
                    ctx: ReadContext) -> list[TableRow]:
        """The rows of the players of the pages (one ref per entry), then the rows of the catalog of Vibix."""
        every = list(self.players)
        pacer = self.players.pacer
        page_players = [player for player in every if not player.series_scope]
        sources: dict[str, list[tuple[int, dict, EpisodePage]]] = {player.name: [] for player in page_players}
        for index, (entry, page) in enumerate(zip(listed, pages)):
            for player in page_players:
                if page is not None and player.name in page.refs:
                    sources[player.name].append((index, entry, page))

        catalog = await self._catalog(slug, pages, ctx)
        # A video that two entries share (a compilation) is asked once.
        total = (sum(len({page.refs[name] for _index, _entry, page in found}) for name, found in sources.items())
                 + (len(catalog[1]) if catalog else 0))
        done = 0

        def asked() -> None:
            nonlocal done
            done += 1
            ctx.progress(PHASE_PLAYERS, done, total)

        ctx.progress(PHASE_PLAYERS, 0, total)
        rows: list[TableRow] = []
        for player in page_players:
            found = sources[player.name]
            refs = [page.refs[player.name] for _index, _entry, page in found]
            pages_of = {page.refs[player.name]: page.url for _index, _entry, page in found}
            verdicts = await ask_many(player, refs, ctx.http, lambda ref: pages_of[ref], pacer, on_asked=asked)
            rows += [self._row(player, index, entry, page.refs[player.name], verdicts[page.refs[player.name]], site_voice)
                     for index, entry, page in found]
        if catalog:
            rows += await self._catalog_rows(catalog, ctx, pacer, asked)
        return rows

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

    async def _catalog(self, slug: str, pages: list[EpisodePage | None],
                       ctx: ReadContext) -> tuple[str, list[Episode]] | None:
        """The catalog of Vibix through the first page that shows its player: (that page, its entries); None when no page
        shows it. A catalog that cannot be captured is a table that cannot be complete."""
        if "vibix" not in self.players:
            return None
        vibix: VibixPlayer = self.players.get("vibix")
        shown = next((page for page in pages if page is not None and "vibix" in page.refs), None)
        if shown is None:
            return None
        ctx.progress(PHASE_CATALOG, 0, 1)

        async def capture():
            try:
                return await vibix.playlist(shown.url)
            except (VibixUnavailable, VibixProtocolError) as exc:
                return exc

        outcome, _attempts, _exhausted = await retrying(
            capture, final=lambda got: not isinstance(got, Exception), budget=CATALOG_BUDGET)
        if isinstance(outcome, Exception):
            logger.warning("Vibix catalog unavailable for %s: %s", shown.url, outcome)
            raise SiteError(reason("player_failed", player="Vibix", cause=str(outcome)))
        ctx.progress(PHASE_CATALOG, 1, 1)
        return shown.url, outcome[0]

    async def _catalog_rows(self, catalog: tuple[str, list[Episode]], ctx: ReadContext, pacer, asked) -> list[TableRow]:
        page_url, entries = catalog
        vibix = self.players.get("vibix")
        refs = {(entry.season_index, entry.position): PlayerRef("vibix", link_of(page_url, entry.season_index, entry.position))
                for entry in entries}
        verdicts = await ask_many(vibix, list(refs.values()), ctx.http, page_url, pacer, on_asked=asked)
        rows = []
        for entry in entries:
            ref = refs[(entry.season_index, entry.position)]
            outcome = outcome_of(verdicts[ref], ref, vibix.label)
            rows.append(TableRow(
                key=f"vibix:{entry.season_index}/{entry.position}", player=vibix.name, player_label=vibix.label,
                group=entry.season, text=entry.title, voice=RowVoice(name=entry.voice), heights=outcome.heights,
                state=outcome.state, reason=outcome.reason, link=ref.url,
                site_fields={"season_index": entry.season_index, "position": entry.position}))
        return rows
