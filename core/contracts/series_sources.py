"""The sources of a series: where its table comes from and which constructor profile reads it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    TORRENT = "torrent"
    VK_VIDEO = "vk_video"
    WEB_VIDEO = "web_video"


class SeriesKind(StrEnum):
    """What a series is to the rest of the system: a series has sources of one kind, a torrent one or a video one."""

    TORRENT = "torrent"
    VIDEO = "video"


class SeriesSource(BaseModel):
    """`url` is a page address, or `channel|query` for a VK channel; the choices belong to the source, not the address."""

    id: int
    series_id: int
    position: int = 0
    url: str
    source_type: SourceType
    parser_profile_id: int | None = None
    vibix_season: int | None = None
    players_off: list[str] = Field(default_factory=list)
    # The colour of the source in its series (0–15 of the palette of the interface): given once when it comes, kept by it.
    color: int = 0


class SourceEvent(BaseModel):
    """`series.source.added`, `.updated` and `.removed`: with the source as it is stored now, so that whoever keeps the
    sources of a series takes the change from the packet itself; none for a source that went."""

    series_id: int
    source_id: int
    source: SeriesSource | None = None


class SourceChanged(SourceEvent):
    """The packet of the event stream for the three: what happened to the source (`added`, `updated`, `removed`)."""

    what: str
