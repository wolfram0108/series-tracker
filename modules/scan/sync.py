"""The first step of a synchronization: every source of the series has its table, or a reason why it has not.

A table is read by `sources`; this step asks for it and then waits for the verdict of that module (`ready` or
`failed`), never for a clock. The ceiling of the wait is a safeguard: when it is reached the source counts as failed
(`table_wait_timeout`), it is not taken for ready.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from core import BusRequestError
from core.deadlines import TABLE_WAIT_S
from core.contracts import SeriesReadProgress, SeriesSource, TableGetReply, TableGetRequest, TableProgress, TableState
from core.logging import get_logger
from core.reasons import reason

log = get_logger("scan")


Ask = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reason: str | None = None


class TableSignals:
    """The verdicts of `sources` about pages, as they come on the bus, for whoever waits for one.

    A verdict is kept with its number, so that one that came between sending a request and getting its answer is
    not lost: the waiter takes the number before it asks (`mark`) and accepts any verdict numbered after it.

    While a scan waits for a page, the progress of its read is told as the progress of that series and source
    (`announce`, the event `scan.series.progress`): the scan is the one that knows which series waits for which page.
    The end of the wait, whatever its outcome, is told too (`active` false)."""

    def __init__(self, announce: Callable[[SeriesReadProgress], None] | None = None) -> None:
        self._count = 0
        self._last: dict[str, tuple[int, Verdict]] = {}
        self._waiting: dict[str, list[asyncio.Future]] = {}
        self._announce = announce
        self._readers: dict[str, dict[tuple[int, int], SeriesReadProgress]] = {}

    def watch(self, url: str, series_id: int, source_id: int) -> None:
        self._readers.setdefault(url, {})[(series_id, source_id)] = SeriesReadProgress(
            series_id=series_id, source_id=source_id, url=url)

    def unwatch(self, url: str, series_id: int, source_id: int) -> None:
        readers = self._readers.get(url, {})
        last = readers.pop((series_id, source_id), None)
        if not readers:
            self._readers.pop(url, None)
        if last is not None and self._announce is not None:
            self._announce(last.model_copy(update={"active": False}))

    def progress(self, packet: TableProgress) -> None:
        readers = self._readers.get(packet.url, {})
        for key, last in readers.items():
            readers[key] = last.model_copy(update={"phase": packet.phase, "current": packet.current,
                                                   "total": packet.total, "rate": packet.rate})
            if self._announce is not None:
                self._announce(readers[key])

    def mark(self) -> int:
        return self._count

    def ready(self, url: str) -> None:
        self._record(url, Verdict(True))

    def failed(self, url: str, why: str | None) -> None:
        self._record(url, Verdict(False, why))

    def _record(self, url: str, verdict: Verdict) -> None:
        self._count += 1
        self._last[url] = (self._count, verdict)
        for future in self._waiting.pop(url, []):
            if not future.done():
                future.set_result(verdict)

    async def verdict(self, url: str, since: int, safeguard_s: float) -> Verdict | None:
        """The first verdict about the page numbered after `since`; None when the safeguard ran out first."""
        found = self._last.get(url)
        if found is not None and found[0] > since:
            return found[1]
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._waiting.setdefault(url, []).append(future)
        try:
            return await asyncio.wait_for(future, safeguard_s)
        except asyncio.TimeoutError:
            return None
        finally:
            waiting = self._waiting.get(url)
            if waiting is not None and future in waiting:
                waiting.remove(future)


@dataclass(frozen=True)
class SourceSync:
    """How a source stands after the step: `ready` (a table read now or within its term), `stale` (the read failed,
    the last complete table stays in force) or `skipped` (the read failed and there is no table at all)."""

    source_id: int
    url: str
    state: str
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "url": self.url, "state": self.state, "reason": self.reason}


async def sync_tables(sources: Sequence[SeriesSource], ask: Ask, signals: TableSignals,
                      safeguard_s: float = TABLE_WAIT_S, *, inventory: bool = False) -> list[SourceSync]:
    """Every source at once; the answer is in the order of `sources`. A failure of one source never stops the others.
    `inventory` asks the sources of a VK channel for the whole channel (a scan of the schedule)."""
    return list(await asyncio.gather(*(_sync_source(source, ask, signals, safeguard_s, inventory) for source in sources)))


async def _sync_source(source: SeriesSource, ask: Ask, signals: TableSignals, safeguard_s: float,
                       inventory: bool = False) -> SourceSync:
    since = signals.mark()
    request = TableGetRequest(url=source.url, options=source.read_options(inventory=inventory), refresh=True)
    try:
        reply = TableGetReply.model_validate(
            await ask("sources.table.get", request.model_dump(mode="json")))
    except (BusRequestError, asyncio.TimeoutError) as exc:
        log.error("источник %d: запрос таблицы %s не выполнен: %s", source.id, source.url, exc)
        return SourceSync(source.id, source.url, "skipped", reason("scan_step_failed", step="sources.table.get"))
    if reply.state == TableState.READY:
        return SourceSync(source.id, reply.url, "ready")
    failure = reply.reason if reply.state == TableState.FAILED else None
    if reply.state == TableState.READING:
        signals.watch(reply.url, source.series_id, source.id)
        try:
            verdict = await signals.verdict(reply.url, since, safeguard_s)
        finally:
            signals.unwatch(reply.url, source.series_id, source.id)
        if verdict is None:
            failure = reason("table_wait_timeout", url=reply.url)
        elif verdict.ok:
            return SourceSync(source.id, reply.url, "ready")
        else:
            failure = verdict.reason or reason("scan_step_failed", step="sources.table.get")
    elif failure is None:
        failure = reason("scan_step_failed", step="sources.table.get")
    held = reply.table is not None and reply.table.complete
    return SourceSync(source.id, reply.url, "stale" if held else "skipped", failure)
