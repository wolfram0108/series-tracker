"""What every site shares: reading one of its pages, and turning the players its pages show into rows.

A site tells which players its pages show (`ref` by player name for each page); this module reads the catalog of each player
that lists a whole series, once however many pages show it, and turns the entries into rows asked of the player. A site that
lists the videos of a player one by one gives them as entries of its own (`Listed`), asked the same way. A catalog that
cannot be read is a table that cannot be complete: the site is refused with the reason of the player.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from core.contracts import TableRow
from core.reasons import reason

from ..players.asking import catalog_rows
from ..players.catalog import CatalogError, Entry, catalog_id, read_catalog
from ..players.contract import Answer, PlayerRef, Reading
from ..players.http import USER_AGENT, get_answer
from ..players.registry import PlayerRegistry
from ..policy import ask
from . import PHASE_CATALOG, PHASE_PLAYERS, ReadContext, SiteError
from .recent import last_of_each

PAGE_TIMEOUT_S = 20.0


async def read_page(url: str, ctx: ReadContext, site: str, *, headers: Mapping[str, str] | None = None,
                    params: Mapping[str, str] | None = None) -> Answer:
    """One page of a site (or an answer of its API) under the one policy; a page that did not come is a refused read."""
    answer = await ask(lambda: get_answer(ctx.http, url, "", headers={"User-Agent": USER_AGENT, **(headers or {})},
                                          params=dict(params) if params else None, timeout=PAGE_TIMEOUT_S))
    if answer.status is None:
        raise SiteError(reason("site_unreachable", site=site, error=answer.error))
    if answer.status != 200:
        raise SiteError(reason("site_status", site=site, status=answer.status, url=url))
    return answer


@dataclass(frozen=True)
class Catalog:
    """What one catalog player lists, and the page that shows it."""
    player: object
    page_url: str
    entries: list[Entry]


@dataclass(frozen=True)
class Listed:
    """The videos of a player that a site lists itself, one entry each, and the page that shows them. `known` is what an
    answer of the player already said of some of them (its own playlist with their files): they are not asked again."""
    player: object
    page_url: str
    entries: list[Entry]
    known: Mapping[PlayerRef, Reading] = field(default_factory=dict)


class AskCount:
    """How many asks of the players a read has done of how many it has to do."""

    def __init__(self, ctx: ReadContext, total: int = 0) -> None:
        self._ctx, self.total, self.done = ctx, total, 0

    def __call__(self) -> None:
        self.done += 1
        self._ctx.progress(PHASE_PLAYERS, self.done, self.total)


async def read_catalogs(players: PlayerRegistry, pages: Iterable[tuple[str, Mapping[str, PlayerRef]]],
                        ctx: ReadContext) -> list[Catalog]:
    """The catalog of each source the pages show; `pages` are (address, refs by player name). A player lists a series
    once: the first page that shows a catalog is the one it is read through. Two catalogs of one player may list one
    video (two lists of a voice-over that overlap): it is one row, of the catalog that listed it first."""
    chosen: list[tuple[object, PlayerRef, str]] = []
    seen: set[tuple[str, str]] = set()
    for page_url, refs in pages:
        for player in players:
            ref = refs.get(player.name)
            if player.series_scope and ref is not None and (player.name, catalog_id(player, ref)) not in seen:
                seen.add((player.name, catalog_id(player, ref)))
                chosen.append((player, ref, page_url))
    catalogs: list[Catalog] = []
    listed: set[tuple[str, str]] = set()
    ctx.progress(PHASE_CATALOG, 0, len(chosen))
    for player, ref, page_url in chosen:
        try:
            entries = await read_catalog(player, ref, ctx.http, page_url)
        except CatalogError as exc:
            raise SiteError(exc.reason) from exc
        fresh = [entry for entry in entries if (player.name, entry.key) not in listed]
        listed.update((player.name, entry.key) for entry in fresh)
        catalogs.append(Catalog(player, page_url,
                                last_of_each(fresh, lambda each: (each.voice.name, each.voice.kind, each.group), ctx.recent)))
        ctx.progress(PHASE_CATALOG, len(catalogs), len(chosen))
    return catalogs


async def rows_of_catalogs(catalogs: Sequence[Catalog | Listed], ctx: ReadContext, pacer, asked: AskCount) -> list[TableRow]:
    rows: list[TableRow] = []
    for catalog in catalogs:
        rows += await catalog_rows(catalog.player, catalog.entries, ctx.http, catalog.page_url, pacer, on_asked=asked,
                                   known=getattr(catalog, "known", None))
    return rows


async def table_rows(players: PlayerRegistry, pages: Iterable[tuple[str, Mapping[str, PlayerRef]]], ctx: ReadContext,
                     listed: Sequence[Listed] = ()) -> list[TableRow]:
    """The rows of a site: of every catalog its pages show, then of the videos it lists itself; each video asked of its
    player."""
    catalogs = await read_catalogs(players, pages, ctx)
    every: list[Catalog | Listed] = [*catalogs, *(each for each in listed if each.entries)]
    asked = AskCount(ctx, sum(len(each.entries) for each in every))
    ctx.progress(PHASE_PLAYERS, 0, asked.total)
    return await rows_of_catalogs(every, ctx, players.pacer, asked)
