"""The first step of a synchronization: every source of the series has its table, or a reason why it has not.

A table is read by `sources`; this step asks for it and then waits for the verdict of that module (`ready` or `failed`), never for a
clock. The ceiling of the wait is a safeguard: when it is reached the source counts as failed (`table_wait_timeout`), it is not taken for
ready. What the answer and the verdict come to is counted in calc/scan/sync.py.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Sequence

from calc.scan.sync import Verdict, read, settled
from core.contracts import SeriesSource, SourceSync, TableRefresh
from core.logging import get_logger
from core.module import BusRequestError
from core.reasons import reason
from core.topics import SOURCES_TABLE_REFRESH, TABLE_WAIT_S

log = get_logger("scan")


Ask = Callable[..., Awaitable[Any]]


class TableSignals:
    """The verdicts of `sources` about pages, as they come on the bus, for whoever waits for one.

    A verdict is kept with its number, so that one that came between sending a request and getting its answer is
    not lost: the waiter takes the number before it asks (`mark`) and accepts any verdict numbered after it.

    How far a read is goes to the series that own the page by the layout (`composition.series.reading`), whoever asked
    for the read: it is not the business of a scan."""

    def __init__(self) -> None:
        self._count = 0
        self._last: dict[str, tuple[int, Verdict]] = {}
        self._waiting: dict[str, list[asyncio.Future]] = {}

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


async def sync_tables(sources: Sequence[SeriesSource], ask: Ask, signals: TableSignals,
                      safeguard_s: float = TABLE_WAIT_S) -> list[SourceSync]:
    """Every source at once; the answer is in the order of `sources`. A failure of one source never stops the others. `ask` is the
    reply of a topic as the model of its record (`BaseModule.reply_of`)."""
    return list(await asyncio.gather(*(_sync_source(source, ask, signals, safeguard_s) for source in sources)))


async def _sync_source(source: SeriesSource, ask: Ask, signals: TableSignals, safeguard_s: float) -> SourceSync:
    since = signals.mark()
    request = TableRefresh(url=source.url, rows=source.rows_per_line, by_sync=True)
    step = SOURCES_TABLE_REFRESH.name
    try:
        reply = await ask(SOURCES_TABLE_REFRESH, request.model_dump(mode="json"))
    except (BusRequestError, asyncio.TimeoutError) as exc:
        log.error("источник %d: запрос таблицы %s не выполнен: %s", source.id, source.url, exc)
        return SourceSync(source_id=source.id, url=source.url, state="skipped", reason=reason("scan_step_failed", step=step))
    found = settled(source, reply, step)
    if found is not None:
        return found
    return read(source, reply, await signals.verdict(reply.url, since, safeguard_s), step)
