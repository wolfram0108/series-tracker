"""Module runlog: the dump of every sync — a file per run from which the whole of it is read back afterwards.

A run is the time between `scan.run.changed` active true and active false of a series (the pass of the scan, the reads of
the pages, the plan and every work it gave out). While a run is open its dump takes, in the order they happen:
  bus — every envelope published on the bus (`Bus.tap`): event, command, query, reply, whole, with its topic,
      correlation id and the topic of the answer — the pages read and their tables, the layout, the rules, the downloads
      with their links, the jobs of the alignment service, the plan of the planner with its inputs (`planner.sync.trace`);
  log — every line of the log of the service at any level, with its group and the exception.
The process is one: what happens while two runs are open is in both dumps.

File: <ST_LOG_DIR>/runs/<series_id>/<start, UTC>-<pid>.jsonl.gz — a JSON line per entry, gzip members appended as the
lines come (a file cut by a stop is read up to its last member). The first line is `open`, the last `close` (`stopped`
when the process stopped first). The newest KEEP files of a series stay, the older ones go when a new run opens.
Without ST_LOG_DIR (a script, a test that names no folder) nothing is written.

Queries, commands, events: none of its own; it only looks at the bus and the log (no subscription, no answer)."""
from __future__ import annotations

import asyncio
import gzip
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core import BaseModule, compute, storage
from core.contracts.work import RUN_CHANGED
from core.envelope import Envelope

KEEP = 50  # dumps kept per series: the disk of the service is not endless, the last syncs are what is looked into


@dataclass
class _Run:
    series_id: int
    path: str
    lines: list = field(default_factory=list)  # entries waiting to be written, in order
    closing: bool = False


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
        self.runs: dict[int, _Run] = {}
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
                run.lines.append(("close", _now(), {"series_id": run.series_id, "stopped": True}))
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
        opening = env.topic == RUN_CHANGED and isinstance(env.payload, dict) and env.payload.get("active")
        if opening:
            series_id = int(env.payload["series_id"])
            if series_id not in self.runs or self.runs[series_id].closing:
                self._open(series_id)
        self.take(("bus", _now(), env))
        if env.topic == RUN_CHANGED and isinstance(env.payload, dict) and not env.payload.get("active"):
            run = self.runs.get(int(env.payload["series_id"]))
            if run is not None and not run.closing:
                run.lines.append(("close", _now(), {"series_id": run.series_id, "stopped": False}))
                run.closing = True

    def _open(self, series_id: int) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        path = os.path.join(self.folder, str(series_id), f"{stamp}-{os.getpid()}.jsonl.gz")
        run = _Run(series_id, path)
        run.lines.append(("open", _now(), {"series_id": series_id, "pid": os.getpid(), "path": path}))
        self.runs[series_id] = run

    def take(self, entry) -> None:
        for run in self.runs.values():
            if not run.closing:
                run.lines.append(entry)
        self._wake.set()

    # --- the writer -----------------------------------------------------------------------------------------------

    async def _writer(self) -> None:
        pruned: set[int] = set()
        while True:
            await self._wake.wait()
            self._wake.clear()
            for series_id, run in list(self.runs.items()):
                if series_id not in pruned:
                    pruned.add(series_id)
                    await storage.call(_prune, os.path.dirname(run.path), self.keep - 1)
                lines, run.lines = run.lines, []
                if lines:
                    try:
                        await storage.call(_append, run.path, await compute.run(_pack, lines))
                    except Exception:  # noqa: BLE001 — the dump never stops the service; its loss is in the log
                        self.log.exception("сериал %d: часть дампа прогона не записана (%d строк)", series_id, len(lines))
                if run.closing and not run.lines:
                    self.runs.pop(series_id, None)
                    pruned.discard(series_id)
                    self.log.info("сериал %d: дамп прогона записан — %s", series_id, run.path)
            if not any(run.lines for run in self.runs.values()):
                self._idle.set()
            else:
                self._wake.set()
            await asyncio.sleep(0)
