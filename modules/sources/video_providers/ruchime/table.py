"""The table of a release: an episode in a row, a player in a column, what the player gave in the cell.

A page of an episode is read once and a player is asked once on an episode: a cell that has its answer is not asked again,
a cell that stayed unsure to the end ("not obtained") is. The table knows the players only by the contract they share.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field, replace
from typing import Iterable

import httpx

from core.reasons import reason

from ...players.contract import Player, PlayerRef
from ...policy import Pacer, Verdict, settle
from ..players import PlayerProbe, probe_from

logger = logging.getLogger("sources.video.ruchime")


@dataclass
class Cell:
    """What one player gave on one episode. Not available and `transient` is "not obtained": the answer stayed unsure to the
    end, which is not the same as "the player has no such episode"."""
    available: bool
    resolution: int | None = None
    # How the candidate is named: the link of the player, or of the catalog entry.
    url: str = ""
    # The address the downloader is given, when the player knows a better one than its link.
    download: str | None = None
    reason: str = ""
    transient: bool = False

    @property
    def not_obtained(self) -> bool:
        return not self.available and self.transient


@dataclass
class Row:
    number: int
    page_url: str
    title: str
    publication_date: str
    refs: dict[str, PlayerRef] = field(default_factory=dict)
    cells: dict[str, Cell] = field(default_factory=dict)
    reason: str = ""
    page_failed: bool = False

    def best(self, off: frozenset[str] = frozenset()) -> int:
        """The highest resolution any enabled player gave on this episode."""
        return max((cell.resolution or 0 for name, cell in self.cells.items() if cell.available and name not in off), default=0)

    def unverified(self, off: frozenset[str] = frozenset()) -> bool:
        """Some enabled player stayed unsure to the end here."""
        return any(cell.not_obtained for name, cell in self.cells.items() if name not in off)

    @property
    def player_reasons(self) -> dict[str, str]:
        return {name: cell.reason for name, cell in self.cells.items() if not cell.available}

    def settled(self, player: str) -> bool:
        cell = self.cells.get(player)
        return cell is not None and not cell.not_obtained

    def candidates(self, off: frozenset[str] = frozenset()) -> list[tuple[int, str, str]]:
        """(resolution, player, name) of every player that gave a stream, except the disabled ones."""
        return [(cell.resolution or 0, name, cell.url) for name, cell in self.cells.items()
                if cell.available and name not in off]

    def downloads(self, off: frozenset[str] = frozenset()) -> dict[str, str]:
        return {name: cell.download for name, cell in self.cells.items()
                if cell.available and cell.download and name not in off}


def cell_of(probe: PlayerProbe) -> Cell:
    return Cell(probe.available, probe.resolution if probe.available else None, probe.ref.url if probe.available else "",
                probe.download, probe.reason, probe.transient)


def probe_of(adapter: Player, ref: PlayerRef, verdict: Verdict) -> PlayerProbe:
    probe = probe_from(verdict, ref, adapter.name if adapter.name != "vk" else "VK")
    address = getattr(adapter, "download_address", None)
    if probe.available and address is not None:
        probe = replace(probe, download=address(ref))
    return probe


async def probe(adapter: Player, ref: PlayerRef, http: httpx.AsyncClient, page_url: str, pacer: Pacer) -> PlayerProbe:
    """Ask a player once under the one policy: asking again and the pauses between asks are decided there."""
    return probe_of(adapter, ref, await settle(adapter, ref, http, page_url, pacer=pacer))


async def read_batch(adapter: Player, rows: list[Row], http: httpx.AsyncClient) -> dict:
    """What a player that can answer for many videos at once settles; the rest is asked one by one."""
    batch = getattr(adapter, "read_batch", None)
    if batch is None or len(rows) < 2:
        return {}
    try:
        settled = await batch([row.refs[adapter.name] for row in rows], http, rows[0].page_url)
    except httpx.HTTPError as exc:
        logger.warning("%s batch failed: %s", adapter.name, type(exc).__name__)
        return {}
    logger.info("%s batch: %d of %d players settled, %d asked one by one", adapter.name, len(settled), len(rows),
                len(rows) - len(settled))
    return settled


class ReleaseTable:
    """The rows of a release by episode number, filled one player at a time, and the time they stay good."""

    def __init__(self, slug: str, rows: Iterable[Row] = ()) -> None:
        self.slug = slug
        self.rows: dict[int, Row] = {row.number: row for row in rows}
        self.expires_at = 0.0

    def fresh(self, now: float | None = None) -> bool:
        return self.expires_at > (time.monotonic() if now is None else now)

    async def fill(self, adapter: Player, http: httpx.AsyncClient, pacer: Pacer, rows: Iterable[Row] | None = None) -> None:
        """Ask `adapter` on the given rows (all by default) that show it and have no answer from it yet."""
        targets = [row for row in (self.rows.values() if rows is None else rows)
                   if adapter.name in row.refs and not row.settled(adapter.name)]
        if not targets:
            return
        settled = await read_batch(adapter, targets, http)

        async def ask(row: Row) -> PlayerProbe:
            ref = row.refs[adapter.name]
            if ref in settled:
                return probe_of(adapter, ref, Verdict(settled[ref], 1, False))
            return await probe(adapter, ref, http, row.page_url, pacer)

        answers = await asyncio.gather(*(ask(row) for row in targets), return_exceptions=True)
        for row, answer in zip(targets, answers):
            if isinstance(answer, Exception):
                row.reason = reason("player_failed", player=adapter.name, cause=str(answer))
                row.cells[adapter.name] = Cell(False, reason=row.reason)
                logger.warning("%s probe failed for %s: %s", adapter.name, row.page_url, answer)
                continue
            row.cells[adapter.name] = cell_of(answer)
            if not answer.available:
                row.reason = answer.reason
