"""The telemetry of a download and what the executor of the downloads is asked and tells (`modules/downloads`): one card to one
need of a plan (an episode and what it needs), the same for every download.

The owner of the work (the planner) tells with each fetch what it is for (`DownloadNeed`) and which variant it takes
(`DownloadVariant`), in the marks of the window «Выход»: the figure and colour of the source, the number of the voice-over in
the order of its season. The executor (modules/downloads) keeps the card of the need through every fetch the plan gives it:
its stages with their time, a line of attempts per variant, the plan rebuilt on the next variant, the figures of the stage
under way. A card is kept while the sync of its series runs; after it only a failed card and a finished one whose plan was
rebuilt stay, until the user removes them or the next sync of the series begins.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, computed_field

from .frozen import Frozen

# The stages of a download, in order: waiting for the others, the player gives the link and the length of the video, the
# file comes, ffmpeg repacks what yt-dlp fetched, the length of the file is checked against the length the source told.
Stage = Literal["queue", "link", "download", "remux", "check"]
STAGES: tuple[Stage, ...] = ("queue", "link", "download", "remux", "check")
StageState = Literal["pending", "active", "done", "failed", "skipped"]
CardStatus = Literal["queued", "link", "download", "remux", "check", "done", "failed"]
NeedKind = Literal["video", "dub", "picture"]
Method = Literal["", "ranged", "ffmpeg", "hls", "ytdlp_hls", "ytdlp_dash"]


class DownloadVariant(Frozen):
    """A row of a source as the window «Выход» marks it: the figure of the source in its series and its colour, the place,
    the player, the height of the row."""

    source_id: int
    source_n: int = 0
    source_color: int = 0
    place: str = ""
    player: str
    player_label: str = ""
    height: int | None = None


class DownloadNeed(Frozen):
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


class StageView(Frozen):
    name: Stage
    state: StageState = "pending"
    seconds: float = 0.0


class AttemptLine(Frozen):
    """One line of the log: the last attempt of a variant (`attempt` of `of`, how it went and how long the variant took),
    or the plan rebuilt on a variant (`replan`), or no variant left (`exhausted`)."""

    kind: Literal["attempt", "replan", "exhausted"]
    variant: DownloadVariant | None = None
    attempt: int = 0
    of: int = 0
    outcome: Literal["running", "failed", "done"] = "running"
    stage: Stage | None = None
    seconds: float = 0.0


class Figures(Frozen):
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


class DownloadCard(Frozen):
    id: str
    need: DownloadNeed
    variant: DownloadVariant | None = None
    status: CardStatus = "queued"
    method: Method = ""
    stages: tuple[StageView, ...] = Field(default_factory=lambda: tuple(StageView(name=each) for each in STAGES))
    log: tuple[AttemptLine, ...] = Field(default_factory=tuple)
    figures: Figures = Field(default_factory=Figures)
    replanned: bool = False
    position: int | None = None
    running: int = 0
    limit: int = 0
    queued_at: str = ""
    finished_at: str = ""
    seconds: float = 0.0


# --- what the executor of the downloads is asked and what it tells -----------------------------------------------------------------

THREADS_PER_FILE = 16


class DownloadLimits(Frozen):
    """How many downloads go at once, by the way a file comes (`downloads.limits.get`): by many connections `threads_per_file` of
    the `threads_total` (as many at once as fit), by one connection a place of `single_at_once`, by a provider of the socket a place
    of `provider_at_once`. The defaults are what the module takes while a setting is not stored."""

    threads_total: int = 2 * THREADS_PER_FILE
    threads_per_file: int = THREADS_PER_FILE
    single_at_once: int = 4
    provider_at_once: int = 2

    @computed_field  # type: ignore[prop-decorator]
    @property
    def multi_at_once(self) -> int:
        """Downloads by many connections at once: as many as their threads fit in the total, one at least."""
        return max(1, self.threads_total // self.threads_per_file)


class LimitsSet(BaseModel):
    """`downloads.limits.set`: the limits to change, as the window writes them (the module checks each is a whole number from 1 up);
    a field that is not sent is not changed."""

    threads_total: Any = None
    threads_per_file: Any = None
    single_at_once: Any = None
    provider_at_once: Any = None


class LimitsSaved(Frozen):
    """The reply of `downloads.limits.set`: the limits now, or the reason a value was refused (nothing is changed then)."""

    ok: bool
    limits: DownloadLimits | None = None
    reason: str | None = None


class DownloadQueue(Frozen):
    """`downloads.queue.get` and the event `downloads.queue.changed`: the cards of the downloads, and how many of them wait or go."""

    tasks: tuple[DownloadCard, ...]
    count: int


class FetchCancel(BaseModel):
    """`downloads.cancel`: the fetch to stop, `fetch:<key>` (or the key)."""

    task_id: int | str


class FetchCancelled(Frozen):
    cancelled: bool


class CardRemove(BaseModel):
    """`downloads.card.remove`: the card that stayed after its sync."""

    id: str


class CardRemoved(Frozen):
    removed: bool


class QueueCleared(Frozen):
    """The reply of `downloads.queue.clear`: how many waiting fetches were dropped."""

    deleted: int


class FetchesWithdrawn(Frozen):
    """The reply of `downloads.withdraw`: how many fetches of the series were stopped and waited for."""

    stopped: int


class SeriesRef(BaseModel):
    """`downloads.withdraw`: the series whose fetches the owner does not need."""

    series_id: int


class DownloadFetch(BaseModel):
    """`downloads.fetch`: the stream behind `url` into the absolute `path`. `key` is the fetch (one waiting or under way is not asked
    twice); `need` and `variant` tell what it is for and which row it takes; `whole` is the video of a reference, `ytdlp` lets the
    downloader take over a link whose stream request failed, `max_height` caps the picture. `place` is the place of the episode the
    fetch is for in its series: the queue gives out the fetches of a sync episode by episode, not in the order they came."""

    key: str | None = None
    url: str | None = None
    path: str | None = None
    max_height: int | None = None
    series_id: int | None = None
    whole: bool | None = None
    ytdlp: bool | None = None
    need: DownloadNeed | None = None
    variant: DownloadVariant | None = None
    place: int | None = None


class FetchStarted(Frozen):
    """The event `downloads.fetch.started`: a fetch left the queue."""

    key: str | None
    series_id: int | None
    path: str | None


class FetchFinished(Frozen):
    """The event `downloads.fetch.finished`: how a fetch ended. `transient`: the failure said nothing of the source (a module of ours
    did not answer, a name was busy), so the owner asks the same source again at its next round."""

    key: str | None
    ok: bool
    error: str
    transient: bool
    path: str | None
    series_id: int | None


class Progress(Frozen):
    """How far a download goes, as any downloader or player tells it: every field is optional, a report names what it knows. The
    stage (`phase`) is `link`, `download`, `remux` or `check`; `progress` is the share of the stage, `dlspeed` bytes a second,
    `speed` the multiple of the real time a repack goes at; `route` is for people, no decision reads it."""

    method: str | None = None
    phase: str | None = None
    progress: int | float | None = None
    eta: int | float | None = None
    total_size_mb: int | float | None = None
    dlspeed: int | float | None = None
    speed: int | float | None = None
    source_seconds: int | float | None = None
    got_seconds: int | float | None = None
    route: str | None = None


class VideoProgress(Progress):
    """The event `sources.video.progress`: the progress of the download of the file `target`, which a player or a provider takes itself. The
    event carries the figures as the player told them (their order is theirs); the producer checks them against this model first, and the
    consumer reads them with it."""

    target: str
