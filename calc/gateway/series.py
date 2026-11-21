"""The series as the window shows it: the card of the catalog with the mapping of TMDB and the count of the downloaded added, the
dates in ISO, and the packets of the events of a series."""
from __future__ import annotations

import os
from typing import Any, Iterable, Mapping

from core.contracts import SeriesCard, SeriesUpdated, TmdbMapping


def iso(value: Any) -> Any:
    """The naive UTC text of the database ('%Y-%m-%d %H:%M:%S.%f') as ISO with `T` (the contract of the old API, `datetime.isoformat()`);
    what is no text stays."""
    return value.replace(" ", "T", 1) if isinstance(value, str) else value


def downloaded_total(counts: Iterable[Mapping[int, int]], series_id: int) -> int:
    """The episodes the series holds. Each owner counts its own rows and a series has rows with one of them only, so the sum needs no kind."""
    return sum(each.get(series_id, 0) for each in counts)


def series_object(series: SeriesCard, mapping: TmdbMapping | None, **added: Any) -> dict:
    """The series as the window shows it: the card the catalog answered written as data with its date in ISO, the mapping of TMDB (its
    date in ISO too) and the fields the gateway adds. What a module answered is its own and is not changed."""
    tmdb = {**mapping.model_dump(), "last_updated": iso(mapping.last_updated)} if mapping else None
    return {**series.model_dump(mode="json"), "last_scan_time": iso(series.last_scan_time), "tmdb_info": tmdb, **added}


def added_packet(card: SeriesCard) -> dict:
    """The packet of a series that was added: the whole card, the date in ISO."""
    return {**card.model_dump(mode="json"), "last_scan_time": iso(card.last_scan_time)}


def updated_packet(event: SeriesUpdated) -> dict:
    """The packet of a series that changed: its number as `id` and the fields told, which are the delta the window merges into the card
    (the statuses and `is_busy` always among them)."""
    return {"id": event.series_id, **event.model_dump(mode="json", exclude_unset=True, exclude={"series_id"})}


def source_file_names(files: Iterable[Mapping[str, Any]]) -> list[str]:
    """The names of the files of the torrents of a series, as the test of the rules takes them."""
    return [os.path.basename(each["original_path"]) for each in files]
