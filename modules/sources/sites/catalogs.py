"""The catalogs of the players a site's pages show, read into rows: the part of a site that every site shares.

A site tells which players its pages show (`ref` by player name for each page); this module reads the catalog of each player
that lists a whole series, once however many pages show it, and turns the entries into rows asked of the player. A catalog
that cannot be read is a table that cannot be complete: the site is refused with the reason of the player.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from core.contracts import TableRow

from ..players.asking import catalog_rows
from ..players.catalog import CatalogError, Entry, catalog_id, read_catalog
from ..players.contract import PlayerRef
from ..players.registry import PlayerRegistry
from . import PHASE_CATALOG, PHASE_PLAYERS, ReadContext, SiteError


@dataclass(frozen=True)
class Catalog:
    """What one catalog player lists, and the page that shows it."""
    player: object
    page_url: str
    entries: list[Entry]


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
    once: the first page that shows a catalog is the one it is read through."""
    chosen: list[tuple[object, PlayerRef, str]] = []
    seen: set[tuple[str, str]] = set()
    for page_url, refs in pages:
        for player in players:
            ref = refs.get(player.name)
            if player.series_scope and ref is not None and (player.name, catalog_id(player, ref)) not in seen:
                seen.add((player.name, catalog_id(player, ref)))
                chosen.append((player, ref, page_url))
    catalogs: list[Catalog] = []
    ctx.progress(PHASE_CATALOG, 0, len(chosen))
    for player, ref, page_url in chosen:
        try:
            catalogs.append(Catalog(player, page_url, await read_catalog(player, ref, ctx.http, page_url)))
        except CatalogError as exc:
            raise SiteError(exc.reason) from exc
        ctx.progress(PHASE_CATALOG, len(catalogs), len(chosen))
    return catalogs


async def rows_of_catalogs(catalogs: list[Catalog], ctx: ReadContext, pacer, asked: AskCount) -> list[TableRow]:
    rows: list[TableRow] = []
    for catalog in catalogs:
        rows += await catalog_rows(catalog.player, catalog.entries, ctx.http, catalog.page_url, pacer, on_asked=asked)
    return rows
