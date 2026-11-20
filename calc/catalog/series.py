"""Pure steps of the series of the catalog: the names that become paths, the cards and the packets told of them.

Each takes what the module has read (rows, statuses, who is busy) and gives the model the module hands over or tells; none
reads the bus, the base or the clock.
"""
from __future__ import annotations

import os
from typing import Collection, Iterable, Mapping, Sequence

from core.contracts import (SeriesCard, SeriesListReply, SeriesSource, SeriesStatusChanged, SeriesUpdated, SourceEvent)
from core.fsnames import sanitize_name, sanitize_path
from core.reasons import reason

# Columns that end up as path components on disk; the owner normalises them on every write so no client can store an
# unusable name.
_PATH_FIELDS = ("save_path",)
_NAME_FIELDS = ("name_en", "quality_override", "resolution_override")


def fs_safe(fields: dict) -> dict:
    """The fields of a series with the names and the path made safe for the disk; a path that is not absolute is refused."""
    out = dict(fields)
    for key in _PATH_FIELDS:
        if isinstance(out.get(key), str):
            out[key] = sanitize_path(out[key])
            if out[key] and not os.path.isabs(out[key]):
                raise ValueError(reason("save_path_relative", path=out[key]))
    for key in _NAME_FIELDS:
        if isinstance(out.get(key), str):
            out[key] = sanitize_name(out[key])
    return out


def absolute_path(path: str) -> str:
    """A saved path of a series: made safe, and absolute (`library` writes it after a move)."""
    path = sanitize_path(path)
    if not os.path.isabs(path):
        raise ValueError(reason("save_path_relative", path=path))
    return path


def card_of(row: Mapping, statuses: Sequence[str], busy: bool) -> SeriesCard:
    """The series of a stored row (its columns and its sources) with what it is doing now."""
    return SeriesCard(**row, statuses=tuple(statuses), is_busy=busy)


def list_of(rows: Iterable[Mapping], statuses: Mapping[int, Sequence[str]], busy: Collection[int]) -> SeriesListReply:
    """Every series of the rows, each with its statuses and whether its number is among the busy ones."""
    return SeriesListReply(series=tuple(card_of(row, statuses[row["id"]], row["id"] in busy) for row in rows))


def updated_of(series_id: int, applied: Mapping, statuses: Sequence[str], busy: bool) -> SeriesUpdated:
    """What was written to a card, with the statuses and `is_busy` every delta of the card must carry (finding 38)."""
    return SeriesUpdated(series_id=series_id, **applied, statuses=tuple(statuses), is_busy=busy)


def touched_of(series_id: int, stamp: str) -> SeriesUpdated:
    """The new scan time of a series for the interface: the stamp of the base with `T`, as the HTTP route writes it."""
    return SeriesUpdated(series_id=series_id, last_scan_time=stamp.replace(" ", "T", 1))


def status_of(series_id: int, statuses: Sequence[str], busy: bool) -> SeriesStatusChanged:
    return SeriesStatusChanged(series_id=series_id, statuses=tuple(statuses), is_busy=busy)


def source_event(series_id: int, source_id: int, source: SeriesSource | None, changed: Iterable[str] = ()) -> SourceEvent:
    """A source that came, changed (with the fields that changed) or went (no source)."""
    return SourceEvent(series_id=series_id, source_id=source_id, source=source, changed=tuple(changed))
