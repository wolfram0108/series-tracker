"""The tables of site pages: one table per page, one read per page at a time, a term of one hour.

A page is read by exactly one task. Every other request for it is answered "reading" and waits for the event of that
task (`ready` or `failed`); nothing waits on a clock. A table reaches consumers only when it is complete, and while a
read runs or has failed they keep getting the last complete table. The state "reading" is stored, so a stop in the
middle of a read makes the read begin again at the next start.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from core.contracts import ReleaseTable, TableFailed, TableGetReply, TableProgress, TableReady, TableState
from core.logging import get_logger
from core.reasons import describe, reason

from .repository import SourcesRepository
from .sites import ReadContext, Site, SiteError, SiteRegistry

PROGRESS_TOPIC = "sources.table.progress"
READY_TOPIC = "sources.table.ready"
FAILED_TOPIC = "sources.table.failed"

# The term of a table is a requirement (a page is not read again within the hour); it is the subject of the task.
TABLE_TERM = timedelta(hours=1)
# A safeguard, not a sync: a hung site must not hold its page for ever; its expiry is a failed read.
READ_SAFEGUARD_S = 1800.0

Publish = Callable[[str, dict], None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TableReader:
    """Answers for the table of a page, and reads the page when its table is missing or past its term."""

    def __init__(self, repo: SourcesRepository, sites: SiteRegistry, publish: Publish,
                 http: Callable[[], httpx.AsyncClient], *, clock: Callable[[], datetime] = utc_now,
                 term: timedelta = TABLE_TERM, safeguard_s: float = READ_SAFEGUARD_S) -> None:
        self._repo, self._sites, self._publish, self._http = repo, sites, publish, http
        self._clock, self._term, self._safeguard_s = clock, term, safeguard_s
        self._log = get_logger("sources")
        self._reading: dict[str, asyncio.Task] = {}
        # Makes "look at the stored table, then decide to read" one step with respect to the end of a read.
        self._gate = asyncio.Lock()

    # --- requests -----------------------------------------------------------------------

    async def get(self, url: str) -> TableGetReply:
        found = self._resolve(url)
        if found is None:
            return TableGetReply(state=TableState.FAILED, reason=reason("site_not_found"))
        site, key = found
        async with self._gate:
            table = await self._repo.last_table(key)
            if table is not None and self._fresh(table):
                return TableGetReply(state=TableState.READY, table=table)
            if key not in self._reading:
                self._launch(site, key)
            return TableGetReply(state=TableState.READING, table=table)

    async def read(self, url: str) -> None:
        """Read the page now, term or not, unless a read of it is already going."""
        found = self._resolve(url)
        if found is None:
            self._publish(FAILED_TOPIC, TableFailed(url=url, reason=reason("site_not_found")).model_dump(mode="json"))
            return
        site, key = found
        if key not in self._reading:
            self._launch(site, key)

    async def resume(self) -> None:
        """Begin again the reads that a stop interrupted; their consumers ask again by themselves when they start."""
        async with self._gate:
            for page in await self._repo.pages_being_read():
                found = self._resolve(page["url"])
                if found is None:
                    await self._conclude_unreadable(page["url"], page["site"])
                elif found[1] not in self._reading:
                    self._launch(*found)

    async def close(self) -> None:
        """Stop the reads in flight without a verdict: their state stays "reading" for the next start."""
        tasks = list(self._reading.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # --- the read -----------------------------------------------------------------------

    def _resolve(self, url: str) -> tuple[Site, str] | None:
        site = self._sites.find(url)
        return (site, site.canonical_url(url)) if site is not None else None

    def _fresh(self, table: ReleaseTable) -> bool:
        try:
            age = self._clock() - datetime.fromisoformat(table.read_at or "")
        except (TypeError, ValueError):
            return False
        # A table "from the future" means the clock moved back: it is not trusted.
        return timedelta(0) <= age < self._term

    def _stamp(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def _launch(self, site: Site, key: str) -> None:
        """Claim the page for one read; no await between the check of the caller and this, so the claim is exclusive."""
        task = asyncio.create_task(self._run(site, key), name=f"sources.table:{key}")
        self._reading[key] = task
        # A net for a task that ends without concluding (a stop): the page must not stay claimed.
        task.add_done_callback(lambda done: self._reading.pop(key, None) if self._reading.get(key) is done else None)

    async def _run(self, site: Site, key: str) -> None:
        table: ReleaseTable | None = None
        problem: str | None = None
        try:
            await self._repo.mark_reading(key, site.name, self._stamp())
            table = await self._read(site, key)
        except SiteError as exc:
            problem = exc.reason
        except Exception as exc:  # noqa: BLE001 — an unexpected failure is a failed read, not a silent task death
            self._log.exception("reading %s failed unexpectedly", key)
            problem = reason("read_failed", cause=f"{type(exc).__name__}: {exc}")
        async with self._gate:
            problem = await self._settle(site, key, table, problem)
            self._reading.pop(key, None)
        if problem is None and table is not None:
            self._publish(READY_TOPIC, TableReady(url=key, read_at=table.read_at or "").model_dump(mode="json"))
        else:
            self._log.warning("table of %s not read: %s", key, describe(problem))
            self._publish(FAILED_TOPIC, TableFailed(url=key, reason=problem or "").model_dump(mode="json"))

    async def _read(self, site: Site, key: str) -> ReleaseTable:
        context = ReadContext(http=self._http(), progress=lambda phase, current, total: self._publish(
            PROGRESS_TOPIC, TableProgress(url=key, phase=phase, current=current, total=total).model_dump(mode="json")))
        limit = asyncio.timeout(self._safeguard_s)
        try:
            async with limit:
                draft = await site.read(key, context)
        except TimeoutError:
            if limit.expired():
                raise SiteError(reason("read_timeout", seconds=int(self._safeguard_s))) from None
            raise
        seen: set[str] = set()
        for row in draft.rows:
            if row.key in seen:
                raise SiteError(reason("duplicate_key", key=row.key, player=row.player))
            seen.add(row.key)
        return ReleaseTable(url=key, site=site.name, names=draft.names, site_voice=draft.site_voice,
                            read_at=self._stamp(), complete=True, rows=draft.rows)

    async def _settle(self, site: Site, key: str, table: ReleaseTable | None, problem: str | None) -> str | None:
        """Write the outcome before any event: the stored table, or the failure. Returns the reason of a failure."""
        if problem is None and table is not None:
            try:
                await self._repo.save_table(table)
                return None
            except Exception as exc:  # noqa: BLE001 — a table that cannot be stored is a failed read
                self._log.exception("table of %s could not be stored", key)
                problem = reason("read_failed", cause=f"{type(exc).__name__}: {exc}")
        problem = problem or reason("read_failed")
        try:
            await self._repo.mark_failed(key, site.name, problem)
        except Exception:  # noqa: BLE001 — the failure is still announced; the state heals at the next request
            self._log.exception("failure of %s could not be recorded", key)
        return problem

    async def _conclude_unreadable(self, url: str, site: str) -> None:
        """A page recorded as being read whose site is no longer registered: it cannot be read, say so."""
        problem = reason("site_not_found")
        await self._repo.mark_failed(url, site, problem)
        self._publish(FAILED_TOPIC, TableFailed(url=url, reason=problem).model_dump(mode="json"))
