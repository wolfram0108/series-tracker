"""The fuse of a request over the bus.

The price of an answer is known by the module that gives it, not by the module that asks, so the owner of a topic states
its fuse here, once, and `BaseModule.request` takes it from this table. A fuse is a safeguard, not a way to wait: the
expiry of one is a failed step, and the answer that comes after it is not looked for. Callers do not choose a number; a
guard (`bus_deadlines`) refuses a number or a constant in the `timeout` of a request in `modules/`.

Fuses nest. A request that is made of steps lives longer than all of them together, so its fuse is derived from theirs
here (`_NESTED`: its own work and the steps it waits for, one after another), and the guard (`bus_deadlines`) reads the
handlers and refuses a fuse shorter than the sum of the fuses of the requests the handler awaits: an asker that gave up
before its answer finished would report a timeout for an action the answerer went on to apply. A request whose length is the sum of an unbounded number of steps (a scan of a torrent series: one step for every
torrent) has no fuse of its own: each of its steps has one, and a number that fires in the middle of a working scan
would report a failure of something that goes on.

None of the numbers below was measured as a price: they are the longest fuse any caller gave the topic before the table
existed, kept so that no step is cut shorter than it was. The measured prices of the queries go to `PARAMETERS.md`.
"""
from __future__ import annotations

# A read or a write of one record in the store of the module that answers.
QUICK_S = 10.0
# The rest of the queries: the answer asks a neighbour or looks at the disk; also the fuse of a topic not listed.
STANDARD_S = 30.0
# The answer writes a file, registers something or asks an external service.
SERVICE_S = 60.0
# The answer walks the files of a series, drives the torrent client or captures a stream with a browser.
HEAVY_S = 120.0
# The answer fetches a file from a tracker.
FETCH_S = 180.0
# The answer applies a profile of rules to a table or reads a page of a site; a table of a thousand rows is minutes.
RULES_S = 300.0
# The answer renames or registers the files of a whole series.
BATCH_S = 600.0
# A receiver of work answers once it has written the work down (a stuck receiver is all this number ends).
HANDOVER_S = 900.0
# The detection of a change that does not end: a sync waits for the changes admitted before it, and the longest of them
# (a deep adoption renames a whole series) answers within BATCH_S. Past it the wait is told in the log as a failure; the
# admission is not released (a write that still comes would land in the sync).
EDIT_STUCK_S = BATCH_S

# A read of one page of a site, measured on small releases (M-7, M-8: 0.42 s a row at ruchime, 0.14 s at dunhuatv); the
# largest release known, about 1200 rows, is about 8 minutes, so twice that is under this value.
TABLE_READ_S = 1800.0
# The wait of a scan for the tables of its sources: twice a read, as the read itself may start late.
TABLE_WAIT_S = 2 * TABLE_READ_S

_QUICK = (
    "auth.exists", "auth.login", "auth.password.set",
    "catalog.sources.add", "catalog.sources.list", "catalog.sources.remove", "catalog.sources.update",
    "metadata.map.get", "metadata.map.list", "metadata.map.set",
    "rules.profiles.export", "scan.edit.end", "scan.runs.get", "voices.settings.export",
    "rules.profiles.create", "rules.profiles.list", "rules.profiles.update",
    "rules.rules.add", "rules.rules.delete", "rules.rules.list", "rules.rules.reorder", "rules.rules.update",
    "settings.paths.add", "settings.paths.list", "settings.paths.remove",
    "settings.value.set", "settings.values.by_prefix", "settings.values.set_many",
    "sources.site.resolve", "sources.tracker.resolve", "sources.tracker.set_mirrors", "sources.trackers.list",
    "torrents.db.add", "torrents.db.files.for_series", "torrents.db.history",
    "torrents.queue.get",
    "trackerauth.credentials.get", "trackerauth.credentials.set",
    "planner.work.get", "planner.delete", "planner.delete.cancel", "planner.failures.forget",
)

_FUSES: dict[str, float] = {
    **dict.fromkeys(_QUICK, QUICK_S),
    **dict.fromkeys(("conform.submit", "rules.format_torrent_file", "slicing.chapters.mark", "slicing.source.delete",
                     "slicing.task.create", "torrents.db.active", "sources.table.get"), SERVICE_S),
    **dict.fromkeys(("slicing.verify", "sources.video.stream", "torrents.add",
                     "torrents.db.deactivate_all", "torrents.drive_incomplete", "torrents.fs.verify"), HEAVY_S),
    "sources.torrent_file.get": FETCH_S,
    **dict.fromkeys(("renaming.preview", "renaming.process_torrent", "rules.apply", "slicing.chapters.filtered",
                     "slicing.chapters.get", "sources.parse"), RULES_S),
    **dict.fromkeys(("renaming.reprocess", "torrents.register", "slicing.deep_adoption"), BATCH_S),
    "torrents.composition": 900.0,
    # The fetch of a page of a service with credentials; it covers an anti-bot challenge solved by a browser (up to 45 s).
    "trackerauth.fetch": 150.0,
    # The sync of the plan of a series is written down and started: its work is told by work.series.state.
    "planner.sync": QUICK_S,
    # A recount asks the rules once for each source of the series: the fuse of the table is for one source.
    "composition.recompute": RULES_S,
    # A preview reads the table, applies the profile to its lines and asks for the groups of the names, one after another.
    "composition.preview": SERVICE_S + RULES_S + STANDARD_S,
}

# A handler that asks others in turn: its own work and the steps it awaits, as the guard reads them from the handler (a step
# counts once for each place it is awaited; a step repeated for every unit of the request is scaled with `units`).
_NESTED: dict[str, tuple[float, tuple[str, ...]]] = {
    "conform.job.get": (STANDARD_S, ("settings.value.get", "settings.value.get")),
    "conform.settings.get": (QUICK_S, ("settings.value.get",) * 3),
    "conform.settings.set": (QUICK_S, ("settings.value.get",) * 3 + ("settings.values.set_many",)),
    "composition.queues.get": (QUICK_S, ("voices.settings.get",)),
    # The view of the plan reads the table of the series; the files of a series also look for a journal in its folder.
    "planner.series.get": (QUICK_S, ()),
    "planner.files.get": (QUICK_S + STANDARD_S, ("catalog.series.get",)),
    "conform.workspace": (QUICK_S, ("settings.value.get",)),
    "rules.profiles.delete": (QUICK_S, ("catalog.sources.by_profile",) * 2),
    "scan.edit.begin": (QUICK_S, ("catalog.series.get",)),
    "scan.output.switch": (STANDARD_S, ("catalog.series.get",) * 4 + (
        "catalog.series.update", "downloads.queue.get", "composition.get", "voices.settings.get")),
    "scan.status.get": (QUICK_S, ("settings.value.get",) * 3),
    "torrents.db.progress.list": (QUICK_S, ("catalog.series.get",)),
    "voices.settings.set": (QUICK_S, ("settings.values.set_many", "settings.values.by_prefix", "settings.values.by_prefix")),
}

# The topics whose request has no fuse of its own (see the top).
# The move of a series is one move for each file, each with its own (core.storage.MOVE_TIMEOUT_S).
_UNBOUNDED = frozenset({"scan.series.run", "scan.series.prepare", "library.relocation.apply"})

UNBOUNDED = None


def deadline_of(topic: str, units: int = 1) -> float | None:
    """The fuse of a request for `topic`: the table's number times `units` for a topic whose work is one step for each
    unit (a source of a series), `None` for a topic that has none of its own, the standard fuse for one not listed."""
    if topic in _UNBOUNDED:
        return UNBOUNDED
    if topic in _NESTED:
        own, steps = _NESTED[topic]
        inner = [deadline_of(step) for step in steps]
        return UNBOUNDED if any(each is None for each in inner) else (own + sum(inner)) * max(1, units)
    return _FUSES.get(topic, STANDARD_S) * max(1, units)


def topics() -> frozenset[str]:
    """The topics that the table names, for the check that none of them is a topic nobody answers."""
    return frozenset(_FUSES) | frozenset(_NESTED) | _UNBOUNDED
