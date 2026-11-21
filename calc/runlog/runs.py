"""What opens and closes a run of the dump, and what an entry of the log is."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from calc.runlog.lines import page_folder
from core.contracts import RunChanged, TableTrace

# The folder of the dumps of the reads of pages, beside the folders of the series.
READS = "reads"


@dataclass(frozen=True, slots=True)
class Signal:
    """An event that moves a run: `key` is the series (a number) or the address of the page being read, `subject` what the run is of,
    `folder` the folder under the root of the dumps its file goes into, `opens` whether the event opens the run (it closes it otherwise)."""

    key: int | str
    subject: dict
    folder: str
    opens: bool


def sync_signal(payload: Any) -> Signal:
    """The signal of `scan.run.changed`: a sync of a series opens its run when it becomes active and closes it when it is not."""
    run = RunChanged.model_validate(payload)
    return Signal(run.series_id, {"series_id": run.series_id}, str(run.series_id), run.active)


def read_signal(payload: Any, in_a_sync: bool) -> Signal | None:
    """The signal of `sources.table.trace`, or none. A read of a page that begins while no sync of a series is open (`in_a_sync`) is a
    run of its own, between `begin` and `end` of its page; a read inside the run of a series is in the dump of that run."""
    trace = TableTrace.model_validate(payload)
    if not trace.url or trace.kind == "begin" and in_a_sync:
        return None
    return Signal(trace.url, {"url": trace.url}, f"{READS}/{page_folder(trace.url)}", trace.kind == "begin")


def run_name(subject: dict) -> str:
    """How a run is named in the log of the service."""
    return f"сериал {subject['series_id']}" if "series_id" in subject else f"чтение {subject['url']}"


def log_body(record: logging.LogRecord) -> dict:
    """A line of the log as the body of an entry: its level, group, text and the exception when there is one. A line that cannot be made
    is told by its raw text, not lost."""
    try:
        return {"level": record.levelname, "group": record.name, "message": record.getMessage(),
                "exception": logging.Formatter().formatException(record.exc_info) if record.exc_info else None}
    except Exception:  # noqa: BLE001
        return {"level": record.levelname, "group": record.name, "message": repr(record.msg), "exception": None}
