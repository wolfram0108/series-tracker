"""A series of the catalog as the other modules read it, and what the catalog tells of its changes.

The fields of each model stand in the order the window and the stream of events have always written them, so that what
reaches the interface is the same text.
"""
from __future__ import annotations

from typing import Any, Final

from .frozen import Frozen
from .series_sources import SeriesSource


class SeriesCard(Frozen):
    """The series: the columns of its row, its sources by position, what it is doing now (`statuses`) and whether a work
    holds it (`is_busy`). The answer of `catalog.series.get`, `.list` and `.create`, and the packet of `series.added`."""

    id: int
    name: str
    name_en: str
    site: str
    save_path: str
    season: str | None = None
    quality: str | None = None
    last_scan_time: str | None = None
    auto_scan_enabled: bool
    quality_override: str | None = None
    resolution_override: str | None = None
    ignored_seasons: str | None = None
    ignored_episodes: str | None = None
    sources: tuple[SeriesSource, ...]
    statuses: tuple[str, ...]
    is_busy: bool


# The columns of a series that a caller may write (the rest are the table's: the number, the scan time that only a touch renews),
# in the order a card tells them (`SeriesUpdated`).
WRITABLE_FIELDS: Final = ("name", "name_en", "site", "save_path", "season", "quality", "quality_override", "resolution_override",
                         "auto_scan_enabled", "ignored_seasons", "ignored_episodes")


class SeriesListReply(Frozen):
    """`catalog.series.list`: every series, by number."""

    series: tuple[SeriesCard, ...]


class SourcesReply(Frozen):
    """`catalog.sources.list` and `catalog.sources.by_profile`: sources by series, position and number."""

    sources: tuple[SeriesSource, ...]


class SeriesUpdated(Frozen):
    """`series.updated`: what changed, and only that. A field is told when it is set: a card edited tells the fields written
    with its `statuses` and `is_busy`, a touch of the scan time tells `last_scan_time`, a recount of the TMDB aggregate tells
    `tmdb_info`. The packet is written without the fields that were not set (`exclude_unset`), as the stream sends it."""

    series_id: int
    name: str | None = None
    name_en: str | None = None
    site: str | None = None
    save_path: str | None = None
    season: str | None = None
    quality: str | None = None
    quality_override: str | None = None
    resolution_override: str | None = None
    auto_scan_enabled: bool | None = None
    ignored_seasons: str | None = None
    ignored_episodes: str | None = None
    last_scan_time: str | None = None
    tmdb_info: dict[str, Any] | None = None
    statuses: tuple[str, ...] | None = None
    is_busy: bool | None = None


class SeriesDeleted(Frozen):
    """`series.deleted`: the owners of the tables of the series clean theirs and report with `series.purged`."""

    series_id: int


class StatusContribution(Frozen):
    """`series.status.contribution`: the fold of a module that owns tasks, told at its start (after its own reconcile) and at
    every change of it: the flags of the statuses it holds on the series, all false when its contribution is withdrawn."""

    source: str
    series_id: int
    flags: dict[str, bool]


class BusyContribution(Frozen):
    """`series.busy.contribution`: whether the module `source` holds the card of the series with work of its own."""

    source: str
    series_id: int
    busy: bool


class SeriesPurged(Frozen):
    """`series.purged`: the report of the owner of series tables to the catalog, after `series.deleted`: its data of the series
    is cleaned."""

    series_id: int
    owner: str


class SeriesStatusChanged(Frozen):
    """`series.status.changed` and `series.busy.changed`: the whole delta of the card, whichever of the two changed."""

    series_id: int
    statuses: tuple[str, ...]
    is_busy: bool
