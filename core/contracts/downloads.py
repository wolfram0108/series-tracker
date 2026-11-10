"""The telemetry of a download: one card to one need of a plan (an episode and what it needs), the same for every download.

The owner of the work (the planner) tells with each fetch what it is for (`DownloadNeed`) and which variant it takes
(`DownloadVariant`), in the marks of the window «Выход»: the figure and colour of the source, the number of the voice-over in
the order of its season. The executor (modules/downloads) keeps the card of the need through every fetch the plan gives it:
its stages with their time, a line of attempts per variant, the plan rebuilt on the next variant, the figures of the stage
under way. A card is kept while the sync of its series runs; after it only a failed card and a finished one whose plan was
rebuilt stay, until the user removes them or the next sync of the series begins.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# The stages of a download, in order: waiting for the others, the player gives the link and the length of the video, the
# file comes, ffmpeg repacks what yt-dlp fetched, the length of the file is checked against the length the source told.
Stage = Literal["queue", "link", "download", "remux", "check"]
STAGES: tuple[Stage, ...] = ("queue", "link", "download", "remux", "check")
StageState = Literal["pending", "active", "done", "failed", "skipped"]
CardStatus = Literal["queued", "link", "download", "remux", "check", "done", "failed"]
NeedKind = Literal["video", "dub", "picture"]
Method = Literal["", "ranged", "ffmpeg", "hls", "ytdlp_hls", "ytdlp_dash"]


class DownloadVariant(BaseModel):
    """A row of a source as the window «Выход» marks it: the figure of the source in its series and its colour, the place,
    the player, the height of the row."""

    source_id: int
    source_n: int = 0
    source_color: int = 0
    place: str = ""
    player: str
    player_label: str = ""
    height: int | None = None


class DownloadNeed(BaseModel):
    """What the plan needs: `key` names the need across every variant the plan takes for it; `episode` is the label of the
    episode or the range of a compilation (`s06e08`, `s01e09-e12`); `voice_number` is the place of the voice-over in the
    order of its season; `cap` the highest the download takes (None: the video whole)."""

    key: str
    series_id: int
    series_name: str = ""
    sync_started: str = ""
    episode: str = ""
    kind: NeedKind
    group: str | None = None
    voice_number: int | None = None
    cap: int | None = None


class StageView(BaseModel):
    name: Stage
    state: StageState = "pending"
    seconds: float = 0.0


class AttemptLine(BaseModel):
    """One line of the log: the last attempt of a variant (`attempt` of `of`, how it went and how long the variant took),
    or the plan rebuilt on a variant (`replan`), or no variant left (`exhausted`)."""

    kind: Literal["attempt", "replan", "exhausted"]
    variant: DownloadVariant | None = None
    attempt: int = 0
    of: int = 0
    outcome: Literal["running", "failed", "done"] = "running"
    stage: Stage | None = None
    seconds: float = 0.0


class Figures(BaseModel):
    """What the stage under way and the end tell: the share, the megabytes, the speed (MB/s, or ×N of ffmpeg), the time
    left; the length the source told; what was got."""

    progress: int | None = None
    done_mb: float | None = None
    total_mb: float | None = None
    speed: float | None = None
    speed_x: float | None = None
    eta: int | None = None
    source_seconds: float | None = None
    got_seconds: float | None = None
    got_height: int | None = None
    size_mb: float | None = None


class DownloadCard(BaseModel):
    id: str
    need: DownloadNeed
    variant: DownloadVariant | None = None
    status: CardStatus = "queued"
    method: Method = ""
    stages: list[StageView] = Field(default_factory=lambda: [StageView(name=each) for each in STAGES])
    log: list[AttemptLine] = Field(default_factory=list)
    figures: Figures = Field(default_factory=Figures)
    replanned: bool = False
    position: int | None = None
    running: int = 0
    limit: int = 0
    queued_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0
