"""The sources of a series: where its table comes from and which constructor profile reads it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import Field, field_validator

from .frozen import Frozen


class SourceType(StrEnum):
    TORRENT = "torrent"
    VK_VIDEO = "vk_video"
    WEB_VIDEO = "web_video"


class SeriesSource(Frozen):
    """`url` is a page address, or `channel|query` for a VK channel; the choices belong to the source, not the address."""

    id: int
    series_id: int
    position: int = 0
    url: str
    source_type: SourceType
    parser_profile_id: int | None = None
    vibix_season: int | None = None
    players_off: tuple[str, ...] = Field(default_factory=tuple)
    # How many rows of each line of a title a provider source reads: `all`, `N` (the first N) or `-N` (the last N).
    rows_per_line: str = "all"
    # The colour of the source in its series (0–15 of the palette of the interface): given once when it comes, kept by it.
    color: int = 0

    @field_validator("rows_per_line", mode="before")
    @classmethod
    def _none_is_all(cls, value):
        return "all" if value is None else value


class SourceEvent(Frozen):
    """`series.source.added`, `.updated` and `.removed`: with the source as it is stored now, so that whoever keeps the
    sources of a series takes the change from the packet itself; none for a source that went."""

    series_id: int
    source_id: int
    source: SeriesSource | None = None
    # The fields an update changed: whoever reads the page of the source reads it again only for those that change what
    # is read (the address, the players, the season of a catalog).
    changed: tuple[str, ...] = Field(default_factory=tuple)


class SourceChanged(SourceEvent):
    """The packet of the event stream for the three: what happened to the source (`added`, `updated`, `removed`)."""

    what: str
