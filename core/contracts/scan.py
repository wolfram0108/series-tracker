"""The scan: the schedule of the syncs, the open runs, the admission of a change of a series, and what a sync answers (`modules/scan`)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

from .frozen import Frozen
from .work import RunSnapshot


class ScheduleTimes(Frozen):
    """The schedule of the scheduled work as the window shows it: the times of day «HH:MM» of the syncs, and of the checks of the
    watched torrent releases (`between` the syncs, or `free` times of their own)."""

    syncs: tuple[str, ...]
    torrent_mode: str
    torrent_free: tuple[str, ...]
    torrent_between: int


class ScheduleSet(BaseModel):
    """`scan.schedule.set`: the schedule to keep, as the window writes it; the module checks it as a whole."""

    schedule: Any = None


class ScheduleSaved(Frozen):
    """The reply of `scan.schedule.set`: the schedule kept, or the reason it was refused (nothing is kept then)."""

    ok: bool
    schedule: ScheduleTimes | None = None
    reason: str | None = None


class ScanStatus(Frozen):
    """`scan.status.get` and the event `scan.status.changed`: the state of the scheduler, the next sync and the next check of the
    releases by the schedule, and the times of the checks."""

    scanner_enabled: bool
    is_scanning: bool
    next_scan_time: str | None
    next_check_time: str | None
    schedule: ScheduleTimes
    checks: tuple[str, ...]


class OpenRun(Frozen):
    """A run of a sync that is open, with the configuration it works by."""

    series_id: int
    snapshot: RunSnapshot | None


class OpenRuns(Frozen):
    """The reply of `scan.runs.get`: the open runs as their commands are written, and the generation `as_of` of the announcements of
    runs (`RunChanged.seq`) the reading is as of: a series whose announcement is newer is told by the announcement."""

    runs: tuple[OpenRun, ...]
    as_of: int


class SeriesRun(BaseModel):
    """`scan.series.run`: the series to synchronize; with `season` (and an `episode` of it) the sync covers it alone."""

    series_id: int
    download: bool = True
    season: int | None = None
    episode: int | None = None


class SourceSync(Frozen):
    """How a source stands after a sync read its table: `ready` (read now or within its term), `stale` (the read failed and the last
    complete table stays in force) or `skipped` (it failed and there is no table)."""

    source_id: int
    url: str
    state: Literal["ready", "stale", "skipped"]
    reason: str | None = None


class SyncResult(Frozen):
    """The reply of `scan.series.run`. `changed`: the plan of the series took a change. A sync the user stopped answers `cancelled`, one
    cut by the deletion of its series `aborted`; the fields that do not apply are not set."""

    changed: bool
    sources: tuple[SourceSync, ...] | None = None
    cancelled: bool | None = None
    aborted: bool | None = None


class SeriesStop(BaseModel):
    """`scan.run.cancel`: the series whose sync the user stops."""

    series_id: int


class RunCancelled(Frozen):
    """The reply of `scan.run.cancel`: whether any work of the sync was stopped."""

    cancelled: bool


class ScanStarted(Frozen):
    """The reply of `scan.all.start`: whether the pass over all series began (false: one is under way)."""

    started: bool


class EditBegin(BaseModel):
    """`scan.edit.begin`: a change of the series by the user begins; `token` is the caller's number of it, known before the grant."""

    series_id: int
    token: str | None = None


class EditEnd(BaseModel):
    """`scan.edit.end`: the change of that number is written whole."""

    series_id: int
    token: str | None = None


class EditAdmitted(Frozen):
    """The reply of `scan.edit.begin`: the change is admitted under `token`; `ended`: its end was told before its grant."""

    ok: bool
    token: str
    ended: bool | None = None


class OutputSwitched(Frozen):
    """The reply of `scan.output.switch`."""

    success: bool
