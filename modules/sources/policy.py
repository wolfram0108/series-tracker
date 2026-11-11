"""The one place that decides whether to ask a player again: which answers are unsure, and the pace of the requests.

Players only fetch once and read the answer into a kind; nothing else in `modules/sources` repeats a request or pauses
between attempts on its own account. How many times and how long to wait is the budget of `core.retry`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from core.reasons import reason
from core.retry import Budget, retrying

from .players.contract import Answer, Kind, Player, PlayerRef, Reading

logger = logging.getLogger("sources")

# A refusal that can pass: the host is asked again, unlike a final "no".
TRANSIENT_STATUSES = frozenset({403, 408, 425, 429})
UNSURE_KINDS = frozenset({Kind.NOT_PLAYER, Kind.NO_FILE, Kind.NO_ANSWER})


def status_is_transient(status: int) -> bool:
    return status in TRANSIENT_STATUSES or status >= 500


def is_retryable(reading: Reading) -> bool:
    if reading.kind is Kind.HTTP_ERROR:
        return status_is_transient(reading.status)
    return reading.kind in UNSURE_KINDS


DEFAULT = Budget()


@dataclass(frozen=True)
class Verdict:
    """The last reading of a cell; `exhausted` means it was still unsure when attempts or time ran out."""
    reading: Reading
    attempts: int
    exhausted: bool


@dataclass(frozen=True)
class PlayerProbe:
    ref: PlayerRef
    available: bool
    resolution: int | None = None
    reason: str = ""
    transient: bool = False
    # The address the downloader is given for this player's video, when the player knows a better one than its link.
    download: str | None = None


def probe_from(verdict: Verdict, ref: PlayerRef, label: str) -> PlayerProbe:
    """What a settled ask means to a parser; `transient` is an answer that stayed unsure to the end."""
    reading = verdict.reading
    if reading.kind is Kind.PLAYABLE:
        return PlayerProbe(ref, True, max(reading.resolutions, default=None))
    if reading.kind is Kind.NOT_FOUND:
        return PlayerProbe(ref, False, reason=reason("player_video_missing", player=label))
    if reading.kind is Kind.RESTRICTED:
        return PlayerProbe(ref, False, reason=reason("video_private"))
    if reading.kind is Kind.HTTP_ERROR:
        return PlayerProbe(ref, False, reason=reason("player_status", player=label, status=reading.status),
                           transient=verdict.exhausted)
    if reading.kind is Kind.NO_ANSWER:
        return PlayerProbe(ref, False, reason=reason("player_no_answer", player=label), transient=True)
    return PlayerProbe(ref, False, reason=reason("player_unconfirmed", player=label), transient=True)


class Pacer:
    """Spreads the requests to one host so that a burst is not taken for abuse."""

    def __init__(self, interval_s: float | Callable[[], float], *, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self._interval = interval_s if callable(interval_s) else (lambda: interval_s)
        self._clock, self._sleep = clock, sleep
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._next_at - self._clock()
            if delay > 0:
                await self._sleep(delay)
            self._next_at = self._clock() + self._interval()


class AdaptivePacer(Pacer):
    """A pacer that goes slower when the host asks for it: every request after a refusal waits the gap longer, and
    everybody waits the pause the host named."""

    def __init__(self, interval_s: float, *, ceiling_s: float = 1.0, factor: float = 1.25,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        super().__init__(lambda: self.interval, clock=clock, sleep=sleep)
        self.interval, self._ceiling, self._factor = interval_s, max(ceiling_s, interval_s), factor

    def slow_down(self, hold_s: float = 0.0) -> None:
        self.interval = min(self.interval * self._factor, self._ceiling)
        if hold_s > 0:
            self._next_at = max(self._next_at, self._clock() + hold_s)


def busy(status: int | None) -> bool:
    """A host that is silent, rate-limiting or failing: the same request may succeed a moment later."""
    return status is None or status == 429 or status >= 500


async def ask(call: Callable[[], Awaitable[Answer]], budget: Budget | None = None, *,
              clock: Callable[[], float] = time.monotonic,
              sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Answer:
    """One request of a catalog or a page under the budget: asked again while the host is busy; the last answer
    comes back whatever it is, and the caller says what it means."""
    answer, _attempts, _exhausted = await retrying(
        call, final=lambda got: not busy(got.status), budget=budget or DEFAULT,
        retry_after=lambda got: got.retry_after, clock=clock, sleep=sleep)
    return answer


async def settle(player: Player, ref: PlayerRef, http: httpx.AsyncClient, page_url: str,
                 budget: Budget | None = None, *, pacer: Pacer | None = None,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> Verdict:
    """Ask a player until its answer is final or the budget is spent. The time of the budget is the time of the asking —
    the answers of the host and the pauses between them; the turn a video waits for at the pacer among the hundreds of
    a page is no part of it, or the videos asked late in a read would never be asked again."""
    tries = 0
    queued = 0.0  # the time spent waiting for the turn at the pacer

    async def ask() -> tuple[Answer, Reading]:
        nonlocal tries, queued
        if pacer is not None:
            before = clock()
            await pacer.wait()
            queued += clock() - before
        tries += 1
        began = clock()
        answer = await player.fetch(ref, http, page_url)
        reading = player.read(answer)
        # Every answer of a player, with what it said, is in the log: a verdict alone hides why it was unsure.
        logger.info("плеер %s, %s: попытка %d — %s, статус %s, %s (%.1f с)", player.name, ref.url, tries,
                    reading.kind.value, reading.status or answer.status, reading.message or answer.error or "—",
                    clock() - began)
        return answer, reading

    (_answer, reading), attempts, exhausted = await retrying(
        ask, final=lambda asked: not is_retryable(asked[1]), budget=budget or DEFAULT,
        retry_after=lambda asked: asked[0].retry_after, clock=lambda: clock() - queued, sleep=sleep)
    return Verdict(reading, attempts, exhausted)
