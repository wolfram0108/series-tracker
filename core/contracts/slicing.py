"""The telemetry of the slicing of a compilation: one card to a task of the queue, in the standard of the downloads and
the packing (core/contracts/downloads.py, packing.py): the series it is for, the compilation, the episodes it is cut into
with how far each is. The slicing module keeps it; a task that is done leaves the queue, one that failed stays with its
error until the user starts it again."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# `pending` waits for the worker, `slicing` is the one the worker cuts now, `error` stopped on an episode.
SlicingStatus = Literal["pending", "slicing", "error"]
EpisodeState = Literal["pending", "active", "done", "failed"]


class SlicingEpisode(BaseModel):
    """An episode of the compilation: `done` when its file is cut, `active` the one being cut, `failed` the one the cut
    stopped on."""

    number: int
    state: EpisodeState = "pending"


class SlicingCard(BaseModel):
    id: int
    series_id: int
    unique_id: str
    status: SlicingStatus
    file: str = ""
    season: int | None = None
    episode_start: int | None = None
    episode_end: int | None = None
    episodes: list[SlicingEpisode] = Field(default_factory=list)
    # The reason of the stop (core/reasons.py), as the task keeps it.
    error: str | None = None
    created_at: str = ""
