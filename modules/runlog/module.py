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
ones go when a new run opens.
Without ST_LOG_DIR (a script, a test that names no folder) nothing is written.

Queries, commands, events: none of its own; it only looks at the bus and the log (no subscription, no answer)."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core import BaseModule, compute, storage
from core.contracts.release_table import TABLE_TRACE
from core.contracts.work import RUN_CHANGED
from core.envelope import Envelope

KEEP = 50  # dumps kept per series: the disk of the service is not endless, the last syncs are what is looked into


READS = "reads"  # the folder of the dumps of the reads of pages, beside the folders of the series


@dataclass
class _Run:
    """A dump being written: of the sync of a series (`subject` {series_id}) or of the read of a page ({url})."""
    subject: dict
    path: str
    lines: list = field(default_factory=list)  # entries waiting to be written, in order
    closing: bool = False

    @property
    def name(self) -> str:
        return f"сериал {self.subject['series_id']}" if "series_id" in self.subject else f"чтение {self.subject['url']}"


def page_folder(url: str) -> str:
    """The address of a page as the name of the folder of its dumps."""
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", url.split("://", 1)[-1]).strip("_")[:100] or "page"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _line(entry) -> str:
    kind, at, body = entry
    if kind == "bus":
        body = {"kind": body.kind, "topic": body.topic, "cid": body.correlation_id, "reply_to": body.reply_to,
                "admission": body.admission, "payload": body.payload}
    return json.dumps({"t": at, "e": kind, **body}, ensure_ascii=False, default=repr)


def _pack(entries: list) -> bytes:
    return gzip.compress(("\n".join(_line(each) for each in entries) + "\n").encode("utf-8"), compresslevel=6)


def _append(path: str, data: bytes) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab") as out:
        out.write(data)


def _prune(folder: str, keep: int) -> None:
    if not os.path.isdir(folder):
        return
    names = sorted(name for name in os.listdir(folder) if name.endswith(".jsonl.gz"))
    for name in names[:max(0, len(names) - keep)]:
        os.remove(os.path.join(folder, name))


class _Records(logging.Handler):
    """Every line of the log, from any thread, into the open runs."""

    def __init__(self, module: "RunlogModule") -> None:
        super().__init__(logging.DEBUG)
        self.module = module

    def emit(self, record: logging.LogRecord) -> None:
        if record.name == RunlogModule.name or not self.module.runs:
            return
        try:
            body = {"level": record.levelname, "group": record.name, "message": record.getMessage(),
                    "exception": logging.Formatter().formatException(record.exc_info) if record.exc_info else None}
        except Exception:  # noqa: BLE001 — a line that cannot be made is named, not lost silently
            body = {"level": record.levelname, "group": record.name, "message": repr(record.msg), "exception": None}
        entry = ("log", _now(), body)
        if threading.get_ident() == self.module.loop_thread:
            self.module.take(entry)
        else:
            try:
                self.module.loop.call_soon_threadsafe(self.module.take, entry)
            except RuntimeError:  # the loop is closed: the process is ending, its runs are closed already
                pass


class RunlogModule(BaseModule):
    name = "runlog"

    def __init__(self, bus, folder: str | None, keep: int = KEEP) -> None:
        self.folder = folder
        self.keep = keep
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
        self.spawn(self._writer(), name="runlog:writer", lasting=True)

    async def on_stop(self) -> None:
        if self._untap is not None:
            self._untap()
        if self._handler is not None:
            logging.getLogger().removeHandler(self._handler)
        for run in self.runs.values():
            if not run.closing:
                run.lines.append(("close", _now(), {**run.subject, "stopped": True}))
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
        payload = env.payload if isinstance(env.payload, dict) else {}
        if env.topic == RUN_CHANGED and payload.get("active"):
            series_id = int(payload["series_id"])
            if series_id not in self.runs or self.runs[series_id].closing:
                self._open(series_id, {"series_id": series_id}, str(series_id))
        elif env.topic == TABLE_TRACE and payload.get("kind") == "begin":
            # A read inside the run of a series is in the dump of that run; one outside any is a run of its own.
            url = str(payload.get("url") or "")
            in_a_sync = any(isinstance(key, int) and not run.closing for key, run in self.runs.items())
            if url and not in_a_sync and (url not in self.runs or self.runs[url].closing):
                self._open(url, {"url": url}, os.path.join(READS, page_folder(url)))
        self.take(("bus", _now(), env))
        ended = (int(payload["series_id"]) if env.topic == RUN_CHANGED and not payload.get("active")
                 else str(payload.get("url") or "") if env.topic == TABLE_TRACE and payload.get("kind") == "end" else None)
        run = self.runs.get(ended) if ended not in (None, "") else None
        if run is not None and not run.closing:
            run.lines.append(("close", _now(), {**run.subject, "stopped": False}))
            run.closing = True

    def _open(self, key: int | str, subject: dict, folder: str) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = os.path.join(self.folder, folder, f"{stamp}-{os.getpid()}.jsonl.gz")
        run = _Run(subject, path)
        run.lines.append(("open", _now(), {**subject, "pid": os.getpid(), "path": path}))
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
                lines, run.lines = run.lines, []
                if lines:
                    try:
                        await storage.call(_append, run.path, await compute.run(_pack, lines))
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
