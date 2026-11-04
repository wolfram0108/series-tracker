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
    vk_search_mode: str = "search"
    vibix_season: int | None = None
    players_off: list[str] = Field(default_factory=list)
    # The voice-over of the rows that have no name of their own and none from the constructor (a VK channel gives none).
    voice_name: str = ""

    def read_options(self, *, inventory: bool = False) -> dict[str, str]:
        """The choices that change what the site is asked for; the others are applied to the table afterwards.

        `inventory` is a scan of the schedule: the quick search of a VK channel may leave out a new upload without a sign,
        so such a scan reads the channel whole (the table is shared by the series of one channel for the term of a table)."""
        return {"vk_search_mode": "get_all" if inventory else self.vk_search_mode}


class SourceEvent(BaseModel):
    """`series.source.added`, `.updated` and `.removed`."""

    series_id: int
    source_id: int


class SourceChanged(SourceEvent):
    """The packet of the event stream for the three: what happened to the source (`added`, `updated`, `removed`)."""

    what: str
