"""Asking a player on many videos, and what an answer means to a row of the table.

Which answers are final, how often to ask again and how fast are decided in `policy`; this module only gathers the asks of
one player and turns each verdict into the state of a row: playable (with its steps), absent, or not obtained.

An answer that stayed unsure while a whole page was being asked (nothing came, a host too busy, a status that passes) is
not yet the answer about the video: a host asked for hundreds of videos at once refuses some of them. When the rest is
done such videos are looked at again (`look_again`), cause by cause, and the read shows it as a phase of its own.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

import httpx

from core.contracts import RowState, TableRow
from core.reasons import reason

from ..policy import Pacer, Verdict, probe_from, settle
from .catalog import Entry
from .contract import Player, PlayerRef, Reading

logger = logging.getLogger("sources")

# The videos asked again at one time once the rest of a page is done: a host too busy for a hundred asks answers a few.
CALM_IN_FLIGHT = 8


def unsure(said: "Verdict | Exception") -> bool:
    """An answer that is not the last word about the video: the ask failed, or the policy of the players left it unsure
    when its attempts ran out."""
    return isinstance(said, Exception) or said.exhausted


def cause_of(said: "Verdict | Exception") -> str:
    """What was said of a video that was not confirmed, in words that tell one cause from another."""
    if isinstance(said, Exception):
        return f"{type(said).__name__}: {said}"[:200]
    reading = said.reading
    return reading.message or f"{reading.kind.value} {reading.status or ''}".strip()


async def look_again(said: dict, ask: Callable, kind_of: Callable, *, told: Callable[[int, int], None] | None = None,
                     what: str = "") -> None:
    """The videos of `said` (key -> verdict or exception) left unsure, looked at again when the rest is done; `said` is
    updated. The videos of one kind (`kind_of(key)`: a player, a line) left with one cause are one question — was it the
    load of the read, or is the way to them down — answered by asking one of them again (`ask(key)`): the same answer
    says the way is down, and the others keep it unasked (a host that takes no connection is not asked for each of its
    hundred videos again); another answer says it was the load, and every one of them is asked again, a few at a time.
    `told(current, total)` is told how far it is, by videos."""
    groups: dict[tuple, list] = {}
    for key, verdict in said.items():
        if unsure(verdict):
            groups.setdefault((kind_of(key), cause_of(verdict)), []).append(key)
    if not groups:
        return
    total, checked = sum(len(keys) for keys in groups.values()), 0
    calm = asyncio.Semaphore(CALM_IN_FLIGHT)
    logger.info("%s: %d без окончательного ответа, причин %d — каждая причина проверяется повторным запросом одного",
                what, total, len(groups))
    if told is not None:
        told(0, total)

    async def one(key) -> None:
        nonlocal checked
        async with calm:
            said[key] = await ask(key)
        checked += 1
        if told is not None:
            told(checked, total)

    async def group(kind, cause: str, keys: list) -> None:
        nonlocal checked
        first, rest = keys[0], keys[1:]
        async with calm:
            said[first] = await ask(first)
        down = unsure(said[first]) and cause_of(said[first]) == cause
        checked += len(keys) if down or not rest else 1
        if told is not None:
            told(checked, total)
        logger.info("%s, %s: «%s» — %s", what, kind, cause[:160],
                    f"подтвердилось на повторном запросе, остальные {len(rest)} не спрашиваются" if down
                    else f"не подтвердилось, спрашиваются остальные {len(rest)}")
        if not down:
            await asyncio.gather(*(one(key) for key in rest))

    await asyncio.gather(*(group(kind, cause, keys) for (kind, cause), keys in groups.items()))


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
                   on_asked: Callable[[], None] | None = None,
                   known: Mapping[PlayerRef, Reading] | None = None,
                   on_recheck: Callable[[int, int], None] | None = None) -> dict[PlayerRef, Verdict | Exception]:
    """The verdict of the player on each video; an ask that raised comes back as its exception, so that one video cannot
    take the others with it. `page_url` is the page that shows the videos, or a function from a video to its page.
    `gate` bounds the asks in flight and `on_asked` is told as each one ends. `known` is what an answer of the player
    already said of some videos (a playlist of its own that lists their files): those are not asked again. The videos
    left unsure are looked at again when the rest is done (`look_again`); `on_recheck(current, total)` — or the
    `recheck` of the counter given as `on_asked` — is told how far that is."""
    page_of = page_url if callable(page_url) else (lambda ref: page_url)
    unique = list(dict.fromkeys(refs))
    known = known or {}
    unknown = [ref for ref in unique if ref not in known]
    settled = {**known, **(await read_batch(player, unknown, http, page_of(unknown[0])) if unknown else {})}

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
    said = dict(zip(unique, answers))

    async def again(ref: PlayerRef) -> Verdict | Exception:
        try:
            return await settle(player, ref, http, page_of(ref), pacer=pacer)
        except Exception as exc:  # noqa: BLE001 - one video cannot take the others with it
            return exc

    await look_again(said, again, lambda _ref: getattr(player, "label", player.name),
                     told=on_recheck or getattr(on_asked, "recheck", None), what=f"плеер {player.name}")
    for ref, answer in said.items():
        if isinstance(answer, Exception):
            logger.warning("%s ask failed for %s: %s", player.name, ref.url, answer)
    return said


async def catalog_rows(player, entries: Sequence[Entry], http: httpx.AsyncClient, page_url: str, pacer: Pacer, *,
                       on_asked: Callable[[], None] | None = None,
                       known: Mapping[PlayerRef, Reading] | None = None) -> list[TableRow]:
    """A row for each entry of a catalog: the entry as the player words it, with what the player says of its video."""
    verdicts = await ask_many(player, [entry.ref for entry in entries], http, page_url, pacer, on_asked=on_asked,
                              known=known)
    rows = []
    for entry in entries:
        outcome = outcome_of(verdicts[entry.ref], entry.ref, player.label)
        rows.append(TableRow(
            key=f"{player.name}:{entry.key}", player=player.name, player_label=player.label, group=entry.group,
            text=entry.text, voice=entry.voice, heights=outcome.heights, state=outcome.state, reason=outcome.reason,
            link=entry.ref.url, site_fields=entry.site_fields))
    return rows
