"""The plan of a video series as the window reads it (modules/planner, stage 12): the asset with the provenance of every
file, the goal of every episode as the last sync counted it, the attempts that failed and the commands «Удалить» that wait."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .frozen import Frozen
from .reference import Reference


class PlanRowRef(Frozen):
    """A row of a source table: the source, the key of the row and the video behind it as its host numbers it."""

    source_id: int
    row_key: str
    video: str = ""


class PlanSnapshot(Frozen):
    """Where a file came from, written once when it was obtained: it stays when the source forgets the row."""

    source: str = ""
    player: str = ""
    group: str | None = None
    height: int = 0
    link: str = ""
    label: str = ""


class PlanAssetVideo(PlanSnapshot):
    row: PlanRowRef
    cut_of: PlanRowRef | None = None
    # Whether the row of the video was in the layout the last sync read: None before any sync of the planner.
    online: bool | None = None


class PlanAssetTrack(PlanSnapshot):
    row: PlanRowRef
    online: bool | None = None


class PlanVariant(Frozen):
    """A variant of a queue of single videos: a voice-over at a player of a source, as the window lists it."""

    group: str
    source_id: int
    player: str


class PlanSubtitle(Frozen):
    """A subtitles track (DESIGN.md 7.1.1): its row, its name, the player of its row, its language and its type; in a goal
    also how it is obtained — `take` (from the file of its row) or `reuse` (from the file that holds it)."""

    row: PlanRowRef
    name: str
    player: str
    language: str = ""
    subtitle_type: str = ""
    # How it lies in the container (the output's levers): the default one, forced.
    default: bool = False
    forced: bool = False
    kind: Literal["take", "reuse"] | None = None


class PlanAsset(Frozen):
    """A file of the library: one video and the tracks aligned to it; `off` is hidden from the media server (`.off`)."""

    name: str
    off: bool = False
    episode: tuple[int, ...]
    video: PlanAssetVideo
    tracks: tuple[PlanAssetTrack, ...] = Field(default_factory=tuple)
    # The variant of the queue of the markup reference whose timeline the video lies on (its own for a video that is no cut).
    timeline: PlanVariant | None = None
    subtitles: tuple[PlanSubtitle, ...] = Field(default_factory=tuple)


class PlanGoalVideo(PlanSnapshot):
    """How the video of an episode is obtained: `asset` (the file holds it), `download`, `cut` (out of a compilation)."""

    kind: Literal["asset", "download", "cut"]
    row: PlanRowRef
    file: str | None = None
    # The compilation the video is cut out of (a cut, or a file that holds a cut video).
    cut_of: PlanRowRef | None = None


class PlanGoalTrack(PlanSnapshot):
    """How a voice-over of an episode is obtained: `reuse` (the file holds it aligned), `download`, `realign` (out of the
    file it lies in, aligned to the new video), `timeline` (the sound of the single video a cut video is laid onto: taken
    from its download, in step with no alignment). `own` is the own sound of a video the episode no longer has."""

    voice: str
    kind: Literal["reuse", "download", "realign", "timeline"]
    row: PlanRowRef
    own: bool = False


class PlanEpisode(Frozen):
    slot: tuple[int, ...]
    name: str
    off: bool = False
    video: PlanGoalVideo
    tracks: tuple[PlanGoalTrack, ...] = Field(default_factory=tuple)
    # The voice-over groups the user switched off for the episode: the plan takes no track of theirs.
    voices_off: tuple[str, ...] = Field(default_factory=tuple)
    # The voice-over of the markup reference of the episode (stage 14): its sound is the last of the file, out of the
    # order of the season; none without one.
    markup: str | None = None
    # The variant of the queue of the markup reference the plan takes for the episode: none while the episode follows its reference.
    markup_variant: PlanVariant | None = None
    # The subtitles of the file in its order, and the variants it takes that are on no timeline of its video: they wait for
    # the alignment of subtitles to another reference (stage 15, step 10); the group of such a variant is its name.
    subtitles: tuple[PlanSubtitle, ...] = Field(default_factory=tuple)
    unaligned: tuple[PlanVariant, ...] = Field(default_factory=tuple)


class PlanFailure(Frozen):
    """An attempt that failed: a download of a row, an alignment of a voice-over to a video, a cut of an episode out of a
    compilation by its single video. The plan passes over it until the user resets the failures."""

    kind: Literal["download", "align", "cut"]
    row: PlanRowRef
    video: PlanRowRef | None = None
    # Where the attempt belongs, as the layout the view is counted by has its row: the episodes the row gives (a compilation:
    # every one it holds; [season, episode] or [season, episode, part]) and its voice-over group. Empty when the row is
    # offline: the failure is still counted, only not placed.
    episodes: tuple[tuple[int, ...], ...] = Field(default_factory=tuple)
    voice: str | None = None
    # The variant of its row in the layout (voice-over, source, player), as the queues name their rows: a queue marks the
    # row whose attempt failed. None when the row is offline.
    variant: PlanVariant | None = None
    # The series lacks nothing for it: every episode of its row has a file holding what the attempt was for (another
    # source gave it). Only a failure not covered makes the series an error.
    covered: bool = False


class PlanProblem(Frozen):
    """A step of the last sync that failed for the moment (a crash of the service, a download cut short): no failure, the
    next sync tries again. `slot` is the episode (none for the download of a compilation), `voice` the voice-over of an
    alignment or of a download of a voice-over."""

    slot: tuple[int, ...] | None = None
    kind: str
    voice: str | None = None
    reason: str = ""


class PlanCommand(Frozen):
    """A command «Удалить» that waits for the next sync: of a file (`video`) or of a track in it (`track`, `dub`)."""

    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None


class PlannerView(Frozen):
    series_id: int
    running: bool = False
    # The reference of every episode and why it is that one, told by the plan.
    references: tuple[Reference, ...] = Field(default_factory=tuple)
    assets: tuple[PlanAsset, ...] = Field(default_factory=tuple)
    episodes: tuple[PlanEpisode, ...] = Field(default_factory=tuple)
    failures: tuple[PlanFailure, ...] = Field(default_factory=tuple)
    # The steps of the last sync that failed for the moment, with why (PlanProblem).
    problems: tuple[PlanProblem, ...] = Field(default_factory=tuple)
    commands: tuple[PlanCommand, ...] = Field(default_factory=tuple)


class PlanDelete(BaseModel):
    """The command «Удалить» of the window: a file of the asset, or a track in it."""

    series_id: int
    kind: Literal["video", "track"]
    name: str
    dub: PlanRowRef | None = None


class PlanForget(BaseModel):
    """The reset of the failed attempts of a series: of all of them, or of those that belong to a level of it — a season, an
    episode of it, a voice-over group, the cuts out of compilations. The attempts of the other levels stay: the plan goes
    on passing over them, so a reference that stands because another one failed does not go back to the one that failed."""

    series_id: int
    season: int | None = None
    episode: int | None = None
    voice: str | None = None
    kind: Literal["download", "align", "cut"] | None = None

    @property
    def whole(self) -> bool:
        return self.season is None and self.episode is None and self.voice is None and self.kind is None

    def holds(self, failure: dict) -> bool:
        """Whether a failure as the view places it (its `kind`, `episodes`, `voice`) belongs to the level. A failure the
        view cannot place (its row is offline) belongs to no level but the whole series."""
        if self.kind is not None and failure["kind"] != self.kind:
            return False
        if self.voice is not None and failure["voice"] != self.voice:
            return False
        if self.season is None and self.episode is None:
            return True
        return any((self.season is None or slot[0] == self.season) and (self.episode is None or slot[1] == self.episode)
                   for slot in failure["episodes"])


class SyncScope(BaseModel):
    """What a sync of a video series covers when it is not the whole series: a season, or an episode of it. The plan of the
    series is counted whole, and what is carried out is the groups of the plan that hold a place of the scope — the episodes
    and files that share work go together, so the episode of a compilation brings the compilation, its download and every cut
    laid on it. The work of the other groups waits for their own sync; their files are not touched."""

    season: int
    episode: int | None = None

    @classmethod
    def of(cls, payload: dict | None) -> "SyncScope | None":
        """The scope a request names (`season`, `episode`), none when it names none; an episode without its season is no scope."""
        found = payload or {}
        if found.get("season") is None:
            if found.get("episode") is not None:
                raise ValueError("an episode is of a season")
            return None
        return cls(season=int(found["season"]), episode=None if found.get("episode") is None else int(found["episode"]))

    def holds(self, slot) -> bool:
        """Whether a place of the plan ([season, episode] or [season, episode, part]) is of the scope."""
        return slot[0] == self.season and (self.episode is None or slot[1] == self.episode)

    def named(self) -> dict:
        return {"season": self.season, **({} if self.episode is None else {"episode": self.episode})}


class SyncAccepted(Frozen):
    """The reply of `planner.sync`: the sync of the series was taken up, or it was not, for the `reason` (a code of
    core/reasons: one is under way already)."""

    accepted: bool
    reason: str | None = None


class SyncStopped(Frozen):
    """The reply of `planner.sync.cancel`: whether a sync of the series was stopped (false: none went, or it was past the point
    where it can be stopped)."""

    cancelled: bool


class PlanRecorded(Frozen):
    """The reply of `planner.delete`: the command «Удалить» waits for the next sync."""

    recorded: bool


class PlanDropped(Frozen):
    """The reply of `planner.delete.cancel`: whether the command that waited was taken back (false: there was none)."""

    dropped: bool


class FailuresForgotten(Frozen):
    """The reply of `planner.failures.forget`: how many failed attempts were forgotten."""

    reset: int


class DownloadedCounts(Frozen):
    """The reply of `planner.downloaded_counts` (and of the registry of the old torrent pipeline): the episodes a series holds,
    by the number of the series; a series that holds none is not listed."""

    counts: dict[int, int]


class SeasonsHeld(Frozen):
    """The reply of `planner.seasons`: the seasons the files of a series are of, in order."""

    seasons: tuple[int, ...]


class FilesHeld(Frozen):
    """The reply of `planner.files.get`: the files of the asset of a series, relative to its folder, with the journal a stop cut
    short and what it put aside."""

    names: tuple[str, ...]


class SeriesDownloaded(Frozen):
    """The event `series.downloaded.changed`: the series holds another number of episodes (specials aside); the card of the window
    shows it at once."""

    series_id: int
    count: int


class SyncTrace(Frozen):
    """The event `planner.sync.trace`, for the dump of a run (modules/runlog): what the sync of a series counted and did, step by
    step. `kind` names the step (`plan`, with the tables the plan was made of), `run` is the stamp of the sync's start,
    `data` is what the step tells, as plain data."""

    series_id: int
    run: str
    kind: str
    download: bool
    data: dict[str, Any]

    def wire(self) -> dict[str, Any]:
        """The packet as the bus carries it. `data` is plain already (and megabytes for the tables of a plan), so it goes as it is,
        not copied by a dump."""
        return {**self.model_dump(exclude={"data"}), "data": self.data}
