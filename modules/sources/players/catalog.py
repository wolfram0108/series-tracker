"""A player that lists a whole series at once: one thing found on a page, many entries in it.

Vibix (the catalog of seasons), Kodik (the voice-overs of an iframe, each with its seasons and episodes) and CDN VideoHub
(the playlist of a title) are read the same way by every site that embeds them: the player turns what the page shows into
`Entry` objects, the site turns each into a row of its table. An entry has no number of its own; the text is the text of the
player, and which entry is which is told by its key.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from core.contracts import RowVoice
from core.retry import UNTIMED, Budget, retrying

from .contract import PlayerRef

logger = logging.getLogger("sources")

# One more read of a catalog that is a whole browser session (Vibix) after a failed one: a read of a table is the work of
# minutes, so a passing failure is not allowed to throw it away. Players that read by plain requests repeat those under the
# policy and do not ask again.
CATALOG_BUDGET = Budget(attempts=2, pauses=(3.0,), limit_s=UNTIMED)


class CatalogError(RuntimeError):
    """Why a catalog could not be read, as a coded reason; `transient` marks causes that may pass."""

    def __init__(self, reason: str, transient: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.transient = transient


@dataclass(frozen=True)
class Entry:
    """One element of the list of a catalog. `key` is unique among the entries of the player's catalog, `ref` is the video
    (its address is the candidate link and is the same at every read), the rest is as the player words it."""
    key: str
    ref: PlayerRef
    text: str = ""
    group: str = ""
    voice: RowVoice = field(default_factory=RowVoice)
    site_fields: dict[str, Any] = field(default_factory=dict)


class CatalogPlayer(Protocol):
    name: str
    label: str
    series_scope: bool

    def find(self, text: str, page_url: str) -> PlayerRef | None: ...

    async def entries(self, source: PlayerRef, http: httpx.AsyncClient, page_url: str) -> list[Entry]:
        """The whole list the thing found on the page (`source`) gives; `page_url` is the page that shows it."""
        ...


def catalog_id(player, source: PlayerRef) -> str:
    """What tells two sources of one player apart: the pages of a series each show the same catalog, so a player may name
    its catalog by something other than the address of the page."""
    named = getattr(player, "catalog_id", None)
    return named(source) if named is not None else source.url


async def read_catalog(player: CatalogPlayer, source: PlayerRef, http: httpx.AsyncClient, page_url: str) -> list[Entry]:
    """The entries of a catalog; raises `CatalogError`. A player that `asks_catalog_again` is asked once more."""

    async def read() -> list[Entry] | CatalogError:
        try:
            return await player.entries(source, http, page_url)
        except CatalogError as exc:
            return exc

    if not getattr(player, "asks_catalog_again", False):
        found = await read()
    else:
        found, _attempts, _exhausted = await retrying(read, final=lambda got: not isinstance(got, CatalogError),
                                                      budget=CATALOG_BUDGET)
    if isinstance(found, CatalogError):
        logger.warning("%s catalog of %s unavailable: %s", player.name, page_url, found)
        raise found
    return found
