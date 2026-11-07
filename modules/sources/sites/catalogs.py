"""What every site shares: reading one of its pages, and turning the players its pages show into rows.

A site tells which players its pages show (`ref` by player name for each page); this module reads the catalog of each player
that lists a whole series, once however many pages show it, and turns the entries into rows asked of the player. A site that
lists the videos of a player one by one gives them as entries of its own (`Listed`), asked the same way. A catalog is a part
of the page: one that cannot be read is named in the table (`UnreadPart`, with the reason of the player and the names the
site gives its content) and the rest of the page is read; the core keeps the rows the catalog gave the read before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from core.contracts import TableRow, UnreadPart
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
    """What one catalog player lists, the page that shows it, and the part of the page it is (`catalog_part`)."""
    player: object
    page_url: str
    entries: list[Entry]
    part: str = ""


@dataclass(frozen=True)
class Catalogs:
    """The catalogs a read got, and the ones it could not."""
    read: list[Catalog]
    unread: list[UnreadPart]


@dataclass(frozen=True)
class Body:
    """The rows of a page, and the parts of it the read could not get."""
    rows: list[TableRow]
    unread: list[UnreadPart]


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


def catalog_part(player, ref: PlayerRef) -> str:
    """The part of a page a catalog is: the player and its list, the same whichever page shows it."""
    return f"{player.name}:{catalog_id(player, ref)}"


async def read_catalogs(players: PlayerRegistry, pages: Iterable[tuple[str, Mapping[str, PlayerRef]]],
                        ctx: ReadContext, names: Mapping[PlayerRef, Sequence[str]] | None = None) -> Catalogs:
    """The catalog of each source the pages show; `pages` are (address, refs by player name), `names` what the site calls
    the content of a ref (its voice-overs), for a catalog that cannot be read. A player lists a series once: the first
    page that shows a catalog is the one it is read through. Two catalogs of one player may list one video (two lists of a
    voice-over that overlap): it is one row, of the catalog that listed it first."""
    chosen: dict[str, tuple[object, PlayerRef, str]] = {}
    named: dict[str, list[str]] = {}
    for page_url, refs in pages:
        for player in players:
            ref = refs.get(player.name)
            if not player.series_scope or ref is None:
                continue
            part = catalog_part(player, ref)
            chosen.setdefault(part, (player, ref, page_url))
            named.setdefault(part, []).extend((names or {}).get(ref, ()))
    catalogs: list[Catalog] = []
    unread: list[UnreadPart] = []
    listed: set[tuple[str, str]] = set()
    ctx.progress(PHASE_CATALOG, 0, len(chosen))
    for done, (part, (player, ref, page_url)) in enumerate(chosen.items(), start=1):
        try:
            entries = await read_catalog(player, ref, ctx.http, page_url)
        except CatalogError as exc:
            unread.append(UnreadPart(part=part, player_label=getattr(player, "label", player.name),
                                     names=list(dict.fromkeys(named[part])), reason=exc.reason))
            ctx.progress(PHASE_CATALOG, done, len(chosen))
            continue
        fresh = [entry for entry in entries if (player.name, entry.key) not in listed]
        listed.update((player.name, entry.key) for entry in fresh)
        catalogs.append(Catalog(player, page_url,
                                last_of_each(fresh, lambda each: (each.voice.name, each.voice.kind, each.group), ctx.recent),
                                part))
        ctx.progress(PHASE_CATALOG, done, len(chosen))
    return Catalogs(catalogs, unread)


async def rows_of_catalogs(catalogs: Sequence[Catalog | Listed], ctx: ReadContext, pacer, asked: AskCount) -> list[TableRow]:
    rows: list[TableRow] = []
    for catalog in catalogs:
        found = await catalog_rows(catalog.player, catalog.entries, ctx.http, catalog.page_url, pacer, on_asked=asked,
                                   known=getattr(catalog, "known", None))
        part = getattr(catalog, "part", "")
        rows += [row.model_copy(update={"part": part}) for row in found] if part else found
    return rows


async def table_rows(players: PlayerRegistry, pages: Iterable[tuple[str, Mapping[str, PlayerRef]]], ctx: ReadContext,
                     listed: Sequence[Listed] = (), names: Mapping[PlayerRef, Sequence[str]] | None = None) -> Body:
    """The rows of a site: of every catalog its pages show, then of the videos it lists itself; each video asked of its
    player. The catalogs that cannot be read are the unread parts of the page."""
    catalogs = await read_catalogs(players, pages, ctx, names)
    every: list[Catalog | Listed] = [*catalogs.read, *(each for each in listed if each.entries)]
    asked = AskCount(ctx, sum(len(each.entries) for each in every))
    ctx.progress(PHASE_PLAYERS, 0, asked.total)
    return Body(await rows_of_catalogs(every, ctx, players.pacer, asked), catalogs.unread)
