"""The aggregator of the statuses of a series (Р-11): pure logic, no bus and no database.

The status of a series is a computed value: the union of the contributions (folds) of the modules that own tasks, plus the
ephemeral `viewing`. Nothing is stored in the database; contributions live in memory and are restored by the providers
publishing them when the process starts. The module that owns the aggregator keeps its instance; the machine itself holds
no I/O and no clock.

The hierarchy repeats the old STATUS_HIERARCHY: it is the source of the order of the pills in the interface (a design
contract): the first in the list is drawn first.
"""
from __future__ import annotations

HIERARCHY = [
    "error", "scanning", "checking", "aligning", "packing",
    "renaming", "metadata", "activating",
    "downloading", "idle", "queued", "ready", "viewing", "waiting",
]
# scanning: an open run of a sync of the series; aligning: a voice-over job is with the alignment service (voices shows
# the download of voice-over files as downloading); packing: the containers of its episodes are being built (downloads).
# idle is a stalled torrent download (qBit stalledDL): a status of its own beside downloading (Р-11, revision). Its colour in
# the interface is that of downloading; only the pill differs. It counts as activity like downloading does, so it
# suppresses waiting (it is in _WAITING_SUPPRESSORS below).
# queued is a torrent queued in qBit (queuedDL): a status of its own too, with its own (light brown) colour of the layer.
# It suppresses waiting the same way.

# viewing and waiting are deliberately not forbidden in contributions: waiting is part of the VK fold (the meaning "there is
# something pending in the plan"), while viewing comes only by commands and is not allowed in contributions.
ALLOWED_FLAGS = frozenset(HIERARCHY) - {"viewing"}

# waiting is the fact "there is something to wait for"; with real activity it is not shown (the semantics of the old
# sync_vk_statuses/_sync_waiting_status, whose behaviour was smeared over two conflicting mechanisms). ready and viewing do not
# count as activity: "ready, waiting" (a part is downloaded, the rest waits) is a valid pair.
_WAITING_SUPPRESSORS = frozenset(HIERARCHY) - {"ready", "viewing", "waiting"}


class StatusAggregator:
    def __init__(self) -> None:
        # series_id -> source -> the set of active flags
        self._contrib: dict[int, dict[str, frozenset[str]]] = {}
        self._viewing: set[int] = set()
        # the set last given out; a series not in it stands at the default one, `waiting`
        self._published: dict[int, tuple[str, ...]] = {}

    # --- incoming changes ----------------------------------------------------

    def set_contribution(self, series_id: int, source: str,
                         flags: dict[str, bool]) -> list[str] | None:
        """Takes the fold of a source. Returns the new set of statuses if it changed, otherwise None."""
        unknown = set(flags) - ALLOWED_FLAGS
        if unknown:
            raise ValueError(
                f"недопустимые флаги в свёртке '{source}': {sorted(unknown)}")
        active = frozenset(name for name, on in flags.items() if on)
        per_series = self._contrib.setdefault(series_id, {})
        if active:
            per_series[source] = active
        else:
            per_series.pop(source, None)  # all false: the contribution is withdrawn
            if not per_series:
                self._contrib.pop(series_id, None)
        return self._diff(series_id)

    def set_viewing(self, series_id: int, on: bool) -> list[str] | None:
        if on:
            self._viewing.add(series_id)
        else:
            self._viewing.discard(series_id)
        return self._diff(series_id)

    def clear_all_viewing(self) -> dict[int, list[str]]:
        """Drops viewing everywhere (the last SSE client went away). Returns the sets that changed, by series."""
        affected, self._viewing = self._viewing, set()
        changes = {}
        for series_id in affected:
            statuses = self._diff(series_id)
            if statuses is not None:
                changes[series_id] = statuses
        return changes

    def forget(self, series_id: int) -> None:
        """The series is deleted: its contributions are forgotten."""
        self._contrib.pop(series_id, None)
        self._viewing.discard(series_id)
        self._published.pop(series_id, None)

    # --- reading -------------------------------------------------------------

    def statuses(self, series_id: int) -> list[str]:
        active: set[str] = set()
        for flags in self._contrib.get(series_id, {}).values():
            active |= flags
        if active & _WAITING_SUPPRESSORS:
            active.discard("waiting")
        # waiting is settled BEFORE viewing: a view is no activity (the semantics of the original: "viewing, waiting" with
        # the modal of an idle series open).
        if not active:
            active = {"waiting"}
        if series_id in self._viewing:
            active.add("viewing")
        return sorted(active, key=HIERARCHY.index)

    # --- internal ------------------------------------------------------------

    def _diff(self, series_id: int) -> list[str] | None:
        current = tuple(self.statuses(series_id))
        if self._published.get(series_id, ("waiting",)) == current:
            return None
        self._published[series_id] = current
        return list(current)
