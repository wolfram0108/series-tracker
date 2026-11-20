"""The tables of site pages: one table per page, one read per page at a time, within the term of the module (term.py).

A page is read by exactly one task. Every other request for it is answered "reading" and waits for the event of that
task (`ready` or `failed`); nothing waits on a clock. A table reaches consumers only when it is complete, and while a
read runs or has failed they keep getting the last complete table. The state "reading" is stored, so a stop in the
middle of a read makes the read begin again at the next start.

A read that could not get some parts of a page (a catalog of a player, the page of an episode) is still complete: the
parts are named in the table (`unread`), and the rows the last read gave of every part not read this time are kept, so a
part that did not answer is not taken for one that is gone. A read that got no part at all is a failed read, and so is one
with parts unread over a table stored before its rows named their parts.
"""
from __future__ import annotations

import asyncio
import time
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

from core.contracts import (ReleaseTable, SiteResolveReply, TableFailed, TableGetReply, TableGetRequest, TableProgress,
                            TableReady, TableRow, TableState, UnreadPart)
from core.contracts.release_table import TABLE_TRACE_TOPIC, site_of
from core.contracts.torrent import order_name
from core.deadlines import TABLE_READ_S
from core.logging import get_logger
from core.reasons import EXTERNAL, describe, parse, reason
from core.row_limit import limit_of

from .players.registry import PlayerRegistry
from .repository import SourcesRepository, Stored
from .sites import ReadContext, Site, SiteError, SiteRegistry
from .term import Term

PROGRESS_TOPIC = "sources.table.progress"
READY_TOPIC = "sources.table.ready"
FRESH_TOPIC = "sources.table.fresh"
FAILED_TOPIC = "sources.table.failed"
# What a read does, told as it happens for its dump (modules/runlog): `begin` when a page is taken for a read, `end`
# with what the read came to — how long it took and what every player of the page gave.
TRACE_TOPIC = TABLE_TRACE_TOPIC


def _cause(exc: Exception) -> str:
    """The cause of a failed read: a coded reason of ours travels as it is; any other error says its type and words (an
    error with no words, as a TimeoutError, says its type alone)."""
    text = str(exc)
    found = parse(text)
    if found is None:
        return type(exc).__name__
    return text if found["code"] != EXTERNAL else f"{type(exc).__name__}: {text}"


def read_summary(table: ReleaseTable) -> dict:
    """What a read gave, player by player: its rows by state, the frames of the rows that play, and every cause a row
    was left without a video with, counted. The debugging of a source begins here, not at the rows one by one."""
    players: dict[str, dict] = {}
    causes: dict[tuple[str, str, str], int] = {}
    for row in table.rows:
        held = players.setdefault(row.player, {"player": row.player, "label": row.player_label or row.player, "rows": 0,
                                               "states": {}, "heights": {}})
        held["rows"] += 1
        held["states"][row.state.value] = held["states"].get(row.state.value, 0) + 1
        if row.height:
            held["heights"][str(row.height)] = held["heights"].get(str(row.height), 0) + 1
        if row.state.value != "playable":
            key = (row.player, row.state.value, row.reason or "")
            causes[key] = causes.get(key, 0) + 1
    return {"rows": len(table.rows), "players": list(players.values()),
            "causes": [{"player": player, "state": state, "reason": why, "rows": count}
                       for (player, state, why), count in sorted(causes.items(), key=lambda item: -item[1])],
            "unread": [part.model_dump(mode="json") for part in table.unread]}

Publish = Callable[[str, dict], None]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Pace:
    """The speed of one read, phase by phase: steps per second since the phase began. The time is the subject here
    (how fast a page is read is what the user is shown), not a way to wait for anything."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._phase: str | None = None
        self._began = 0.0

    def rate(self, phase: str, current: int) -> float | None:
        now = self._clock()
        if phase != self._phase:
            self._phase, self._began = phase, now
        elapsed = now - self._began
        return round(current / elapsed, 2) if current > 0 and elapsed > 0 else None


@asynccontextmanager
async def _nothing():
    yield


def _merged(old: list[TableRow], new: list[TableRow], off: frozenset[str], limited_read: bool) -> list[TableRow]:
    """The rows of a read that did not ask everything: the earlier rows in their places, each replaced by its new row when
    this read took it, and kept as it was when it did not (a player switched off, a line read in part); the new rows that
    were not there come after them in the order the site listed them. The row of a player asked and no longer listed is
    gone, as in any read."""
    taken = {row.key: row for row in new}
    merged = []
    for row in old:
        if row.key in taken:
            merged.append(taken.pop(row.key))
        elif limited_read or order_name(row.player) in off:
            merged.append(row)
    return merged + [row for row in new if row.key in taken]


class TableReader:
    """Answers for the table of a page, and reads the page when its table is missing or past its term."""

    def __init__(self, repo: SourcesRepository, sites: SiteRegistry, publish: Publish,
                 http: Callable[[], httpx.AsyncClient], *, players: PlayerRegistry, clock: Callable[[], datetime] = utc_now,
                 term: Term | None = None, safeguard_s: float = TABLE_READ_S, recent: int | None = None,
                 holding: Callable[[], AbstractAsyncContextManager] | None = None,
                 off: Callable[[], frozenset[str]] | None = None,
                 blocked: Callable[[str], str | None] | None = None,
                 ask: Callable[[str, dict], Awaitable[Any]] | None = None) -> None:
        self._repo, self._sites, self._publish, self._http, self._players = repo, sites, publish, http, players
        # The reason a page cannot be read now for want of the provider that reads it (modules/sources/provider/gate.py), or
        # None: a read of such a page is not begun, it says why.
        self._blocked = blocked if blocked is not None else (lambda url: None)
        # The players switched off for every source, as they are when a read begins.
        self._off = off if off is not None else (lambda: frozenset())
        self._clock, self._safeguard_s, self._recent = clock, safeguard_s, recent
        self._ask = ask
        self._term = term if term is not None else Term()
        # What a read holds for its whole length (the browser of the module, kept between the rows of the page).
        self._holding = holding if holding is not None else _nothing
        self._log = get_logger("sources")
        self._reading: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()  # the pages whose read the user stopped: a verdict, unlike a stop of the process
        # The pages whose matter changed while a read of them went (the rows to read): that read may not have it, so the page
        # is read once more when it ends.
        self._again: dict[str, str] = {}
        # Makes "look at the stored table, then decide to read" one step with respect to the end of a read.
        self._gate = asyncio.Lock()

    # --- requests -----------------------------------------------------------------------

    async def get(self, request: TableGetRequest) -> dict:
        """The reply of `sources.table.get` (`TableGetReply`): the table is given as it is stored, its rows the field of
        columns they are kept as — nothing is parsed, no model is made of thousands of rows."""
        state, url, stored, why = await self._get(request)
        record = stored.record() if stored is not None else None
        fresh = self._fresh_until_of(record.url, record.read_at) if record is not None else None
        return {"url": url, "state": state.value, "table": record.model_dump() if record is not None else None,
                "reason": why, "fresh_until": fresh}

    def fresh_until(self, table: ReleaseTable | None) -> str | None:
        """The end of the term of a table: its read and the term of its site (term.py); none for a table never read."""
        return None if table is None else self._fresh_until_of(table.url, table.read_at)

    def _fresh_until_of(self, url: str, read_at: str | None) -> str | None:
        if not read_at:
            return None
        try:
            moment = datetime.fromisoformat(read_at)
        except ValueError:
            return None
        return (moment + self._term.of(url)).astimezone(timezone.utc).isoformat()

    async def fresh_of(self, urls: list[str]) -> list[TableReady]:
        """The stored tables of the addresses (by their canonical page) with the end of their term; an address with no
        table read is left out."""
        read = {page["url"]: page["read_at"] for page in await self._repo.pages() if page["read_at"]}
        found = {}
        for url in urls:
            resolved = self._resolve(url)
            key = resolved[1] if resolved is not None else url
            if key in read:
                table = ReleaseTable(url=key, site="", read_at=read[key])
                found[key] = TableReady(url=key, read_at=read[key], fresh_until=self.fresh_until(table))
        return list(found.values())

    async def fresh_of_site(self, site: str) -> list[TableReady]:
        """The stored tables of a site with the end of their term: what a change of its term changes."""
        found = []
        for page in await self._repo.pages():
            if site_of(page["url"]) == site and page["read_at"]:
                table = ReleaseTable(url=page["url"], site="", read_at=page["read_at"])
                found.append(TableReady(url=page["url"], read_at=page["read_at"], fresh_until=self.fresh_until(table)))
        return found

    async def _get(self, request: TableGetRequest) -> tuple[TableState, str, Stored | None, str | None]:
        """How the page stands for the request: its state, its canonical address, what is stored of it (its last
        complete table, if any) and the reason of a failure."""
        found = self._resolve(request.url)
        if found is None:
            # No site reads the address now (the provider of the socket is not up and was never heard of): the page is not read,
            # but the table it had still serves, as after any failed read — a source never loses its rows for a missing reader.
            try:
                held = await self._repo.stored(request.url)
            except Exception:  # noqa: BLE001 - a store that cannot be read has no table to give; the refusal is the answer
                self._log.exception("the table of %s could not be read for a page no site reads", request.url)
                held = None
            return TableState.FAILED, request.url, held, self._blocked(request.url) or reason("site_not_found")
        site, key = found
        # The record is read outside the gate, so the pages of a series are read side by side; in the gate it is the one the
        # decision is made on unless the page was written since, and then it is read again there.
        seen = self._repo.version(key)
        stored = await self._repo.stored(key)
        async with self._gate:
            if self._repo.version(key) != seen:
                stored = await self._repo.stored(key)
            if not request.refresh:
                return self._as_it_is(key, stored)
            if stored.head is not None and self._fresh_at(key, stored.head["read_at"]):
                return TableState.READY, key, stored, None
            if getattr(site, "first_read_by_user", False) and (stored.head is None or not request.by_sync):
                # The page of such a site is begun by the user's command; a page nobody has read waits for it, and a page
                # that was read is read again by a synchronization only (its rows are the setting of its source).
                return self._as_it_is(key, stored)
            if key not in self._reading:
                if (why := self._blocked(key)) is not None:
                    # The page would be read by a provider that is not up: no read begins, the last table stays in force.
                    return TableState.FAILED, key, stored, why
                self._launch(site, key, rows=request.rows)
            return TableState.READING, key, stored, None

    def _as_it_is(self, key: str, stored: Stored) -> tuple[TableState, str, Stored | None, str | None]:
        """The page as it stands, with no read: a recount of a layout must not cost a read of the site. A read that goes
        is `reading`, a last read that failed is `failed` with its reason, any other complete table is `ready` however old,
        and a page nothing was read of is `absent`. All of it with the last complete table, when there is one."""
        if key in self._reading:
            return TableState.READING, key, stored, None
        if stored.state == "failed":
            return TableState.FAILED, key, stored, stored.reason
        if stored.head is not None:
            return TableState.READY, key, stored, None
        return TableState.ABSENT, key, None, None

    async def read(self, request: TableGetRequest, *, changed: bool = False) -> None:
        """Read the page now, term or not, unless a read of it is already going. The user asked for it: the read takes
        nothing that an earlier read left for sharing (`ReadContext.fresh`). `changed`: what the page is read from changed
        (the tracks of a release laid out anew) — no one asked to read the site, so the read is not `fresh` (a tracker's
        page is not checked on the tracker for a change of its layout); a read that goes may have begun before, so the
        page is read once more when it ends; a second ask of the same matter makes no second read."""
        found = self._resolve(request.url)
        if found is None:
            self._publish(FAILED_TOPIC, TableFailed(url=request.url, reason=self._blocked(request.url) or reason("site_not_found"))
                          .model_dump(mode="json"))
            return
        site, key = found
        if key in self._reading:
            if changed:
                self._again[key] = request.rows
            return
        if (why := self._blocked(key)) is not None:
            self._publish(FAILED_TOPIC, TableFailed(url=key, reason=why).model_dump(mode="json"))
            return
        self._launch(site, key, fresh=not changed, rows=request.rows)

    async def cancel(self, url: str) -> bool:
        """Stop the read of a page the user does not want any more: the read ends failed (`read_cancelled`), so it is
        not begun again at the start, and the last complete table still serves. False when no read of it goes."""
        found = self._resolve(url)
        task = self._reading.get(found[1]) if found is not None else None
        if task is None:
            return False
        self._again.pop(found[1], None)
        self._cancelled.add(found[1])
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return True

    def keys_of(self, urls: list[str]) -> set[str]:
        """The keys of the tables the addresses name (the canonical form of each); an address no site reads names none."""
        return {found[1] for found in map(self._resolve, urls) if found is not None}

    async def forget(self, listed: set[str], was_listed: set[str]) -> list[str]:
        """Forget the pages no source lists (`listed` holds the keys that are listed): a page that was listed a moment ago
        (`was_listed`) goes at once, a page that never was (a preview, a page left from before this was kept) goes when its
        table is past the term, so that a page the user looked at and is about to add is not lost. A read that goes is
        stopped first: its end must not write the page again. Returns the keys forgotten."""
        async with self._gate:
            gone = [page["url"] for page in await self._repo.pages() if page["url"] not in listed
                    and (page["url"] in was_listed or not self._fresh_at(page["url"], page["read_at"]))]
        stopping = [self._reading[key] for key in gone if key in self._reading]
        for key in gone:
            self._again.pop(key, None)
            if key in self._reading:
                self._cancelled.add(key)
                self._reading[key].cancel()
        await asyncio.gather(*stopping, return_exceptions=True)
        async with self._gate:
            await self._repo.forget_pages(gone)
        return gone

    def _fresh_at(self, url: str, read_at: str | None) -> bool:
        try:
            age = self._clock() - datetime.fromisoformat(read_at or "")
        except (TypeError, ValueError):
            return False
        # A table "from the future" means the clock moved back: it is not trusted.
        return timedelta(0) <= age < self._term.of(url)

    def resolve(self, url: str) -> SiteResolveReply:
        """Which site reads the address and its canonical form, with no network."""
        found = self._resolve(url)
        if not found:
            return SiteResolveReply()
        return SiteResolveReply(site=found[0].name, canonical_url=found[1], source_type=found[0].source_type)

    async def resume(self) -> None:
        """Begin again the reads that a stop interrupted; their consumers ask again by themselves when they start."""
        async with self._gate:
            for page in await self._repo.pages_being_read():
                found = self._resolve(page["url"])
                if found is None:
                    await self._conclude_unreadable(page["url"], page["site"])
                elif getattr(found[0], "first_read_by_user", False):
                    # A read of a page that waits for the user is not begun again by a start: its rows are the user's to ask.
                    problem = reason("read_cancelled")
                    await self._repo.mark_failed(found[1], found[0].name, problem)
                    self._publish(FAILED_TOPIC, TableFailed(url=found[1], reason=problem).model_dump(mode="json"))
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
        return self._fresh_at(table.url, table.read_at)

    def _stamp(self) -> str:
        return self._clock().astimezone(timezone.utc).isoformat()

    def _launch(self, site: Site, key: str, *, fresh: bool = False, rows: str = "all") -> None:
        """Claim the page for one read; no await between the check of the caller and this, so the claim is exclusive."""
        task = asyncio.create_task(self._run(site, key, fresh, rows), name=f"sources.table:{key}")
        self._reading[key] = task
        # A net for a task that ends without concluding (a stop): the page must not stay claimed.
        task.add_done_callback(lambda done: self._reading.pop(key, None) if self._reading.get(key) is done else None)

    async def _run(self, site: Site, key: str, fresh: bool, rows: str = "all") -> None:
        table: ReleaseTable | None = None
        problem: str | None = None
        began = time.monotonic()
        self._publish(TRACE_TOPIC, {"url": key, "site": site.name, "kind": "begin", "fresh": fresh})
        try:
            await self._repo.mark_reading(key, site.name, self._stamp())
            async with self._holding():
                table = await self._read(site, key, fresh, rows)
        except asyncio.CancelledError:
            if key not in self._cancelled:
                raise  # a stop of the process: the page stays `reading` and is read again at the start
            self._cancelled.discard(key)
            asyncio.current_task().uncancel()
            problem = reason("read_cancelled")
        except SiteError as exc:
            problem = exc.reason
        except Exception as exc:  # noqa: BLE001 — an unexpected failure is a failed read, not a silent task death
            self._log.exception("reading %s failed unexpectedly", key)
            problem = reason("read_failed", cause=_cause(exc))
        async with self._gate:
            problem = await self._settle(site, key, table, problem)
            self._reading.pop(key, None)
        self._publish(TRACE_TOPIC, {"url": key, "site": site.name, "kind": "end", "ok": problem is None,
                                    "reason": problem, "seconds": round(time.monotonic() - began, 1),
                                    **(read_summary(table) if problem is None and table is not None else {})})
        if problem is None and table is not None:
            self._publish(READY_TOPIC, TableReady(url=key, read_at=table.read_at or "",
                                                  fresh_until=self.fresh_until(table)).model_dump(mode="json"))
        else:
            self._log.warning("table of %s not read: %s", key, describe(problem))
            self._publish(FAILED_TOPIC, TableFailed(url=key, reason=problem or "").model_dump(mode="json"))
        again = self._again.pop(key, None)
        if again is not None and key not in self._reading:
            self._launch(site, key, rows=again)

    async def _read(self, site: Site, key: str, fresh: bool, spec: str = "all") -> ReleaseTable:
        pace = Pace()

        def progress(phase: str, current: int, total: int) -> None:
            self._publish(PROGRESS_TOPIC, TableProgress(url=key, phase=phase, current=current, total=total,
                                                        rate=pace.rate(phase, current)).model_dump(mode="json"))

        stored = (await self._repo.stored(key)).table
        off = self._off()
        context = ReadContext(http=self._http(), progress=progress, recent=self._recent, fresh=fresh,
                              last_rows=tuple(stored.rows) if stored is not None else (), off=off, rows=spec, ask=self._ask)
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
        # Which video each row is, read by its player from its link: the one place a table learns it, whatever site read it.
        rows = [row.model_copy(update={"video": self._players.video_of(row.player, row.link)}) for row in draft.rows]
        # What this read did not ask stays as the last read left it: a player switched off was not asked (a file downloaded
        # from one of its rows must not look withdrawn by a source nobody asked), and a read of the first or the last
        # rows of each line did not take the others. A read of the whole page replaces the table.
        if stored is not None and (off or limit_of(context.rows) is not None):
            rows = _merged(stored.rows, rows, off, limit_of(context.rows) is not None)
        if draft.unread:
            rows = rows + await self._kept(key, rows, draft.unread)
        return ReleaseTable(url=key, site=site.name, names=draft.names, title=draft.title, site_voice=draft.site_voice, channel=draft.channel,
                            read_at=self._stamp(), complete=True, rows=rows, unread=draft.unread)

    async def _kept(self, key: str, rows: list[TableRow], unread: list[UnreadPart]) -> list[TableRow]:
        """The rows of the last complete table of every part this read did not get; a row whose video this read listed
        again is not kept twice."""
        if not rows:
            raise SiteError(unread[0].reason)
        stored = await self._repo.stored(key)
        if stored.table is None:
            kept: list[TableRow] = []
        elif not stored.parted:
            raise SiteError(unread[0].reason)
        else:
            parts, keys = {row.part for row in rows} | {""}, {row.key for row in rows}
            kept = [row for row in stored.table.rows if row.part not in parts and row.key not in keys]
        self._log.warning("table of %s read without %d of its parts (%s); %d rows kept from the read of %s", key,
                          len(unread), "; ".join(f"{each.part}: {describe(each.reason)}" for each in unread), len(kept),
                          stored.table.read_at if stored.table is not None else "none")
        return kept

    async def _settle(self, site: Site, key: str, table: ReleaseTable | None, problem: str | None) -> str | None:
        """Write the outcome before any event: the stored table, or the failure. Returns the reason of a failure."""
        if problem is None and table is not None:
            try:
                await self._repo.save_table(table)
                return None
            except Exception as exc:  # noqa: BLE001 — a table that cannot be stored is a failed read
                self._log.exception("table of %s could not be stored", key)
                problem = reason("read_failed", cause=_cause(exc))
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
