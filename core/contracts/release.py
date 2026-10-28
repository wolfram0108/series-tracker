"""What a video provider tells about a release: the title, the episodes and the voice-over catalog."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReleaseTitle(BaseModel):
    ru: str | None = None
    en: str | None = None


class ReleaseSource(BaseModel):
    """One voice-over of a release at one player, with the episodes it really has.

    `links` (episode number -> address) is given only to the voices module."""

    model_config = ConfigDict(extra="allow")

    url: str
    player: str
    studio: str
    kind: str = ""
    first: int | None = None
    last: int | None = None
    count: int = 0
    ranges: str = ""
    episodes: list[int] = Field(default_factory=list)
    links: dict[str, str] | None = None


class Release(BaseModel):
    """The result of `parse_release` of every provider; a provider with a voice catalog adds `sources`."""

    model_config = ConfigDict(extra="allow")

    canonical_url: str
    title: ReleaseTitle
    titles: list[str] = Field(default_factory=list)
    resolution: int | None = None
    videos: list[dict[str, Any]] = Field(default_factory=list)
    episodes_count: int = 0
    provider: str
    source_type: str
    releases: list[dict[str, Any]] = Field(default_factory=list)
    players: dict[str, Any] = Field(default_factory=dict)
    sources: list[ReleaseSource] = Field(default_factory=list)
    selected_source: str | None = None
