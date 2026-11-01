"""Asking a player on many videos, and what an answer means to a row of the table.

Which answers are final, how often to ask again and how fast are decided in `policy`; this module only gathers the asks of
one player and turns each verdict into the state of a row: playable (with its steps), absent, or not obtained.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Sequence

import httpx

from core.contracts import RowState
from core.reasons import reason

from ..policy import Pacer, Verdict, probe_from, settle
from .contract import Player, PlayerRef, Reading

logger = logging.getLogger("sources")


@dataclass(frozen=True)
class Outcome:
    """What one ask came to, in the terms of a table row. `not_obtained` is not `absent`: the answer stayed unsure."""
    state: RowState
    heights: list[int] = field(default_factory=list)
    reason: str | None = None


def outcome_of(answer: Verdict | Exception, ref: PlayerRef, label: str) -> Outcome:
    """An ask that raised is `not_obtained` with the cause, never `absent`."""
    if isinstance(answer, Exception):
        return Outcome(RowState.NOT_OBTAINED, reason=reason("player_failed", player=label, cause=str(answer)))
    probe = probe_from(answer, ref, label)
    if probe.available:
        return Outcome(RowState.PLAYABLE, sorted(answer.reading.resolutions))
    return Outcome(RowState.NOT_OBTAINED if probe.transient else RowState.ABSENT, reason=probe.reason)


async def read_batch(player: Player, refs: Sequence[PlayerRef], http: httpx.AsyncClient,
                     page_url: str) -> dict[PlayerRef, Reading]:
    """What a player that can answer for many videos at once settles; the rest is asked one by one."""
    batch = getattr(player, "read_batch", None)
    if batch is None or len(refs) < 2:
        return {}
    try:
        settled = await batch(list(refs), http, page_url)
    except httpx.HTTPError as exc:
        logger.warning("%s batch failed: %s", player.name, type(exc).__name__)
        return {}
    logger.info("%s batch: %d of %d players settled, %d asked one by one", player.name, len(settled), len(refs),
                len(refs) - len(settled))
    return settled


async def ask_many(player: Player, refs: Sequence[PlayerRef], http: httpx.AsyncClient,
                   page_url: "str | Callable[[PlayerRef], str]", pacer: Pacer, *,
                   gate: asyncio.Semaphore | None = None,
                   on_asked: Callable[[], None] | None = None) -> dict[PlayerRef, Verdict | Exception]:
    """The verdict of the player on each video; an ask that raised comes back as its exception, so that one video cannot
    take the others with it. `page_url` is the page that shows the videos, or a function from a video to its page.
    `gate` bounds the asks in flight and `on_asked` is told as each one ends."""
    page_of = page_url if callable(page_url) else (lambda ref: page_url)
    unique = list(dict.fromkeys(refs))
    settled = await read_batch(player, unique, http, page_of(unique[0])) if unique else {}

    async def ask(ref: PlayerRef) -> Verdict:
        try:
            if ref in settled:
                return Verdict(settled[ref], 1, False)
            if gate is None:
                return await settle(player, ref, http, page_of(ref), pacer=pacer)
            async with gate:
                return await settle(player, ref, http, page_of(ref), pacer=pacer)
        finally:
            if on_asked is not None:
                on_asked()

    answers = await asyncio.gather(*(ask(ref) for ref in unique), return_exceptions=True)
    for ref, answer in zip(unique, answers):
        if isinstance(answer, Exception):
            logger.warning("%s ask failed for %s: %s", player.name, ref.url, answer)
    return dict(zip(unique, answers))
