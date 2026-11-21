"""Module runlog: the dump of every sync and of every read of a page outside one — a file per run from which the whole
of it is read back afterwards.

A run is the time between `scan.run.changed` active true and active false of a series (the pass of the scan, the reads of
the pages, the plan and every work it gave out). A read of a page that begins while no run of a series is open (a source
added in a window, «Перечитать») is a run of its own, between `sources.table.trace` `begin` and `end` of its page: the
`end` carries what the read came to, player by player. A read inside a run of a series is in the dump of that run. While
a run is open its dump takes, in the order they happen:
  bus — every envelope published on the bus (`Bus.tap`): event, command, query, reply, whole, with its topic,
      correlation id and the topic of the answer — the pages read and their tables, the layout, the rules, the downloads
      with their links, the jobs of the alignment service, the plan of the planner with its inputs (`planner.sync.trace`);
  log — every line of the log of the service at any level, with its group and the exception.
The process is one: what happens while two runs are open is in both dumps.

File: <ST_LOG_DIR>/runs/<series_id>/<start, UTC>-<pid>.jsonl.gz, and for a read of a page
<ST_LOG_DIR>/runs/reads/<the address of the page as a name>/<start, UTC>-<pid>.jsonl.gz — a JSON line per entry, gzip
members appended as the lines come (a file cut by a stop is read up to its last member). The first line is `open`, the
last `close` (`stopped` when the process stopped first). The newest KEEP files of a series or of a page stay, the older
ones go when a new run opens and at the start; and all the dumps together keep within BUDGET bytes: the oldest go, whatever they are of,
when a run opens and at the start (a sync of hours is a dump of a gigabyte, and the disk is the disk of the database).
Without ST_LOG_DIR (a script, a test that names no folder) nothing is written.

The count is in `calc/runlog`: what an entry is as a line (a payload told without its secrets, models of any depth included), which
dumps stay, which event opens or closes a run. Here are the disk and the threads: the files, the writer, the handler of the log.

Queries, commands, events: none of its own; it only looks at the bus and the log (no subscription, no answer)."""
from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from calc.runlog.lines import BUS, CLOSE, LOG, OPEN, pack
from calc.runlog.retention import SUFFIX, oldest_first, surplus
from calc.runlog.runs import log_body, read_signal, run_name, sync_signal
from core import compute, storage
from core.envelope import Envelope
from core.module import BaseModule
from core.topics import SCAN_RUN_CHANGED, SOURCES_TABLE_TRACE

KEEP = 3  # dumps kept per series and per page: the last syncs are what is looked into
BUDGET = 2 * 1024 ** 3  # bytes of all the dumps together; the dumps being written are never removed


@dataclass
class _Run:
    """A dump being written: of the sync of a series (`subject` {series_id}) or of the read of a page ({url})."""
    subject: dict
    path: str
    lines: list = field(default_factory=list)  # entries waiting to be written, in order
    closing: bool = False

    @property
    def name(self) -> str:
        return run_name(self.subject)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab") as out:
        out.write(data)


def _prune(folder: str, keep: int) -> None:
    if not os.path.isdir(folder):
        return
    for name in surplus(os.listdir(folder), keep):
        os.remove(os.path.join(folder, name))


def _prune_all(root: str, keep: int) -> None:
    """Every folder of dumps under `root` keeps its newest `keep`: a series that has not synced since keeps no more."""
    for folder, _dirs, _names in os.walk(root):
        _prune(folder, keep)


def _within(root: str, budget: int, writing: set[str]) -> list[str]:
    """The oldest dumps under `root` go until all of them together keep within `budget` bytes; the dumps being written
    stay. Returns the files removed."""
    found = []
    for folder, _dirs, names in os.walk(root):
        for name in names:
            if name.endswith(SUFFIX):
                path = os.path.join(folder, name)
                try:
                    found.append((os.stat(path).st_mtime, os.path.getsize(path), path))
                except OSError:
                    continue
    total = sum(size for _at, size, _path in found)
    removed = []
    for size, path in oldest_first(found, writing):
        if total <= budget:
            break
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        removed.append(path)
    return removed


class _Records(logging.Handler):
    """Every line of the log, from any thread, into the open runs."""

    def __init__(self, module: "RunlogModule") -> None:
        super().__init__(logging.DEBUG)
        self.module = module

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == RunlogModule.name or not self.module.runs:
            return
        entry = (LOG, _now(), log_body(record))
        if threading.get_ident() == self.module.loop_thread:
            self.module.take(entry)
        else:
            try:
                self.module.loop.call_soon_threadsafe(self.module.take, entry)
            except RuntimeError:  # the loop is closed: the process is ending, its runs are closed already
                pass


class RunlogModule(BaseModule):
    name = "runlog"

    def __init__(self, bus, folder: str | None, keep: int = KEEP, budget: int = BUDGET) -> None:
        self.folder = folder
        self.keep = keep
        self.budget = budget
        self.runs: dict[int | str, _Run] = {}  # by the series, or by the address of the page being read
        self.loop: asyncio.AbstractEventLoop | None = None
        self.loop_thread = 0
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._untap = None
        self._handler: _Records | None = None
        super().__init__(bus)

    async def on_start(self) -> None:
        if not self.folder:
            return
        self.loop = asyncio.get_running_loop()
        self.loop_thread = threading.get_ident()
        self._untap = self.bus.tap(self._seen)
        self._handler = _Records(self)
        logging.getLogger().addHandler(self._handler)
        await storage.call(_prune_all, self.folder, self.keep)
        await self._keep_within()
        self.spawn(self._writer(), name="runlog:writer", lasting=True)

    async def _keep_within(self) -> None:
        """All the dumps together within the budget: the oldest go."""
        writing = {run.path for run in self.runs.values()}
        try:
            removed = await storage.call(_within, self.folder or "", self.budget, writing)
        except Exception:  # noqa: BLE001 — a dump that cannot be removed is named, the service goes on
            self.log.exception("старые дампы не убраны")
            return
        if removed:
            self.log.info("старые дампы убраны, чтобы все дампы уложились в %d МБ: %d файлов", self.budget // 1024 ** 2,
                          len(removed))

    async def on_stop(self) -> None:
        if self._untap is not None:
            self._untap()
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
        for run in self.runs.values():
            if not run.closing:
                run.lines.append((CLOSE, _now(), {**run.subject, "stopped": True}))
                run.closing = True
        self._wake.set()
        await self.written()

    async def written(self) -> None:
        """Every entry taken so far is in its file and every closed run is done."""
        while any(run.lines for run in self.runs.values()) or not self._idle.is_set():
            self._wake.set()
            self._idle.clear()
            await self._idle.wait()

    # --- what comes in --------------------------------------------------------------------------------------------

    def _seen(self, env: Envelope) -> None:
        # Only the two events that move a run are read, as the models of their records; every envelope is taken into the open runs.
        signal = None
        if env.topic == SCAN_RUN_CHANGED.name:
            signal = sync_signal(env.payload)
        elif env.topic == SOURCES_TABLE_TRACE.name:
            in_a_sync = any(isinstance(key, int) and not run.closing for key, run in self.runs.items())
            signal = read_signal(env.payload, in_a_sync)
        if signal is not None and signal.opens:
            held = self.runs.get(signal.key)
            if held is None or held.closing:
                self._open(signal.key, signal.subject, signal.folder)
        self.take((BUS, _now(), env))
        run = self.runs.get(signal.key) if signal is not None and not signal.opens else None
        if run is not None and not run.closing:
            run.lines.append((CLOSE, _now(), {**run.subject, "stopped": False}))
            run.closing = True

    def _open(self, key: int | str, subject: dict, folder: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = os.path.join(self.folder, folder, f"{stamp}-{os.getpid()}.jsonl.gz")
        run = _Run(subject, path)
        run.lines.append((OPEN, _now(), {**subject, "pid": os.getpid(), "path": path}))
        self.runs[key] = run

    def take(self, entry) -> None:
        for run in self.runs.values():
            if not run.closing:
                run.lines.append(entry)
        self._wake.set()

    # --- the writer -----------------------------------------------------------------------------------------------

    async def _writer(self) -> None:
        pruned: set[int | str] = set()
        while True:
            await self._wake.wait()
            self._wake.clear()
            for key, run in list(self.runs.items()):
                if key not in pruned:
                    pruned.add(key)
                    await storage.call(_prune, os.path.dirname(run.path), self.keep - 1)
                    await self._keep_within()
                lines, run.lines = run.lines, []
                if lines:
                    try:
                        await storage.call(_append, run.path, await compute.run(pack, lines))
                    except Exception:  # noqa: BLE001 — the dump never stops the service; its loss is in the log
                        self.log.exception("%s: часть дампа не записана (%d строк)", run.name, len(lines))
                if run.closing and not run.lines:
                    # A run of the same subject opened meanwhile holds the key: only this one's entry goes.
                    if self.runs.get(key) is run:
                        self.runs.pop(key, None)
                    pruned.discard(key)
                    self.log.info("%s: дамп записан — %s", run.name, run.path)
            if not any(run.lines for run in self.runs.values()):
                self._idle.set()
            else:
                self._wake.set()
            await asyncio.sleep(0)
