"""The sources of a series: where its table comes from and which constructor profile reads it."""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class SourceType(StrEnum):
    TORRENT = "torrent"
    VK_VIDEO = "vk_video"
    WEB_VIDEO = "web_video"


class SeriesKind(StrEnum):
    """What a series is to the rest of the system; any source that is not a torrent makes it `video`."""

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
    vk_search_mode: str = "search"
    vibix_season: int | None = None
    players_off: list[str] = Field(default_factory=list)


class SourceEvent(BaseModel):
    """`series.source.added`, `.updated` and `.removed`."""

    series_id: int
    source_id: int
