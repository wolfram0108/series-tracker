"""The one notice that says work is not needed any more.

Whoever owns an object writes down that it is not wanted and tells it to nobody in particular: the module that does
the work for it (a download, a job at a service) hears the notice, stops, and removes what it made. The owner does not
know who works for the object, and the executor does not ask the owner what to do.
"""
from __future__ import annotations

from pydantic import BaseModel

WORK_WITHDRAWN = "work.withdrawn"


class WorkWithdrawn(BaseModel):
    """`keys` are the keys of the work the owner gave out (`dub:<id>` for a voice-over, `file:<id>` for a file read by
    several); without `keys` all the work of the series is not needed."""

    series_id: int
    keys: list[str] | None = None
