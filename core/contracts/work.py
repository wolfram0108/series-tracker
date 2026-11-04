"""The one notice that says work is not needed any more.

Whoever owns an object writes down that it is not wanted and tells it to nobody in particular: the module that does
the work for it (a download, a job at a service) hears the notice, stops, and removes what it made. The owner does not
know who works for the object, and the executor does not ask the owner what to do.
"""
from __future__ import annotations

from pydantic import BaseModel

WORK_WITHDRAWN = "work.withdrawn"
# A sync of a series is one run: it opens with the sync and closes when every work it gave out has ended (scan owns it).
RUN_CHANGED = "scan.run.changed"
# An executor tells whether it has live work of a series (downloads, voices); the run closes when nobody has.
WORK_STATE = "work.series.state"


class RunChanged(BaseModel):
    """`active` true: a sync of the series runs, the series is busy and its layout and plan stay as the sync found them
    (the snapshot); false: every work of it ended, edits are allowed and what was owed meanwhile is counted."""

    series_id: int
    active: bool


class WorkState(BaseModel):
    """Whether `owner` has live work of the series: a download that waits or runs, a voice-over admitted and not at
    its end. Sent when it changes, and asked as `<owner>.work.get {series_id}` -> WorkState."""

    owner: str
    series_id: int
    active: bool


class WorkWithdrawn(BaseModel):
    """`keys` are the keys of the work the owner gave out (`dub:<id>` for a voice-over, `file:<id>` for a file read by
    several); without `keys` all the work of the series is not needed."""

    series_id: int
    keys: list[str] | None = None
