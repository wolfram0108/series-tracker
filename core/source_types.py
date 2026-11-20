"""Source type classifications and the torrent source of a series."""
from __future__ import annotations

from core.contracts import SeriesCard, SeriesSource, SourceType

_VIDEO_SOURCE_TYPES = frozenset({SourceType.VK_VIDEO, SourceType.WEB_VIDEO})


def is_video_source(source_type: str | None) -> bool:
    """True for the type of a source that is a video one (VK or web); a series is told apart by its `kind`."""
    return source_type in _VIDEO_SOURCE_TYPES


def torrent_source(series: SeriesCard) -> SeriesSource | None:
    """The torrent source of a series as `catalog.series.get` answers it; None for a series that has none."""
    return next((source for source in series.sources if source.source_type == SourceType.TORRENT), None)


def torrent_profile_id(series: SeriesCard) -> int | None:
    """The constructor profile of the torrent source of a series; None if there is no source or no profile set."""
    source = torrent_source(series)
    return source.parser_profile_id if source else None
