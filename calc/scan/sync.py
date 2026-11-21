"""How a source stands after a sync asked for its table, and what a command a restart found calls for.

A table is read by `sources`; the step of a sync asks for it and then waits for the verdict of that module (`ready` or `failed`), never for a
clock. The ceiling of the wait is a safeguard: when it is reached the source counts as failed (`table_wait_timeout`), it is not taken for ready.
The asking and the waiting are the module's (modules/scan/sync.py); this says what the answer and the verdict come to."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Mapping

from core.contracts import OpenRun, OpenRuns, SeriesSource, SourceSync, TableGetReply, TableState
from core.reasons import reason


@dataclass(frozen=True)
class Verdict:
    """What `sources` said of a page once its read ended."""

    ok: bool
    reason: str | None = None


def settled(source: SeriesSource, reply: TableGetReply, step: str) -> SourceSync | None:
    """How a source stands by the answer to the request for its table: `ready` (read now or within its term), `stale` (the read failed and
    the last complete table stays in force) or `skipped` (it failed and there is no table). None while the page is being read: the verdict
    of the read decides. `step` is the topic the request went to, for the words of a failure."""
    if reply.state == TableState.READY:
        return SourceSync(source_id=source.id, url=reply.url, state="ready", reason=None)
    if reply.state == TableState.READING:
        return None
    if reply.state == TableState.ABSENT:
        # A page of the provider nobody has read waits for the user's read: the sync has nothing of it to wait for.
        failure = reason("table_not_read", url=reply.url)
    else:
        failure = reply.reason if reply.state == TableState.FAILED else None
        if failure is None:
            failure = reason("scan_step_failed", step=step)
    return _lacking(source, reply, failure)


def read(source: SeriesSource, reply: TableGetReply, verdict: Verdict | None, step: str) -> SourceSync:
    """How a source stands once the read of its page ended with `verdict` (none: the safeguard ran out first)."""
    if verdict is None:
        failure = reason("table_wait_timeout", url=reply.url)
    elif verdict.ok:
        return SourceSync(source_id=source.id, url=reply.url, state="ready", reason=None)
    else:
        failure = verdict.reason or reason("scan_step_failed", step=step)
    return _lacking(source, reply, failure)


def _lacking(source: SeriesSource, reply: TableGetReply, failure: str) -> SourceSync:
    held = reply.table is not None and reply.table.complete
    return SourceSync(source_id=source.id, url=reply.url, state="stale" if held else "skipped", reason=failure)


def open_runs(tasks: list[Mapping], as_of: int) -> OpenRuns:
    """The runs of the syncs that are open, as their commands are written: each with its snapshot."""
    return OpenRuns(runs=tuple(OpenRun(series_id=task["series_id"], snapshot=(task["results_data"] or {}).get("snapshot"))
                               for task in tasks if (task["results_data"] or {}).get("video_sync")), as_of=as_of)


def resume_step(task: Mapping, owned: Collection[int], running: Collection[int]) -> str:
    """What a restart does with a command it found in the table of tasks: `legacy` (a task of the torrent scan that left with stage 15:
    it goes), `owned` (the scan of this very command is running in this process: nothing), `working` (the pass was over: the run waits for
    its works again), `again` (a command that came after the restart is the scan of the series already: it goes) or `run` (the sync is run
    again from its written command)."""
    results = task["results_data"]
    if not results.get("video_sync"):
        return "legacy"
    if task["id"] in owned:
        return "owned"
    if results.get("phase") == "working":
        return "working"
    if task["series_id"] in running:
        return "again"
    return "run"
