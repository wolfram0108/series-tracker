"""Pydantic models of the gateway: the shapes of its answers and the bodies of its requests.

The ADDITIVE RULE: by default `response_model` cuts the fields a model lacks, so that the shape of an
answer does not change silently the base model is `extra="allow"` (extra fields pass through). A route with
optional fields takes `response_model_exclude_none=True`, or FastAPI adds a `null` field.

New routes are built on the models of core/contracts without copies of their fields. A request body that
knows its fields is a model with `extra="forbid"`: a field the model lacks (a removed one too) is refused
by name, not lost.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.contracts import SeriesSource, SourceType


class ApiModel(BaseModel):
    """The base: extra fields are not cut off (the additive invariant)."""
    model_config = ConfigDict(extra="allow")


class RequestModel(BaseModel):
    """A request body: an unknown field is refused by its name."""
    model_config = ConfigDict(extra="forbid")


class OkResponse(ApiModel):
    """The standard success: {success: true[, message]}."""
    success: bool
    message: str | None = None


class ReadCancelled(ApiModel):
    """The read of a page is stopped: it ends failed with «чтение остановлено», the former table serves; false — it was not going."""
    success: bool
    cancelled: bool = False


class SyncCancelled(ApiModel):
    """The sync of a series is stopped: all it did is interrupted, nothing is applied; false — it was not going."""
    success: bool
    cancelled: bool = False


class SyncScopeBody(RequestModel):
    """What a sync of the series covers when it is not all of it: a season, or an episode of it (the sync of a video series)."""
    season: int
    episode: int | None = None


class FailuresResetBody(RequestModel):
    """The level whose failed attempts are reset: a season, an episode of it, a voice-over group, the cuts out of compilations.
    Without a body, the whole series."""
    season: int | None = None
    episode: int | None = None
    voice: str | None = None
    kind: Literal["download", "align", "cut"] | None = None


class FailuresReset(ApiModel):
    """The failed attempts of the series are reset: how many voice-overs begin anew (the work goes with the next sync)."""
    success: bool
    reset: int = 0


class ErrorResponse(ApiModel):
    """An error (it goes out through JSONResponse; here it is for the documentation of
    `responses={code: {"model": ErrorResponse}}`)."""
    success: bool = False
    error: str


# --- the «scan and queues» block (api_system) ------------------------------

class ScheduleValue(RequestModel):
    """The times of day of the syncs and of the checks of the torrent releases (calc/schedule.py), «HH:MM»."""

    syncs: list[str] = Field(default_factory=list)
    torrent_mode: Literal["between", "free"] = "between"
    torrent_free: list[str] = Field(default_factory=list)
    torrent_between: int = 1


class ScheduleBody(RequestModel):
    schedule: ScheduleValue


class ScannerStatus(ApiModel):
    scanner_enabled: bool
    is_scanning: bool
    next_scan_time: str | None = None
    next_check_time: str | None = None
    schedule: ScheduleValue
    # The times of day the releases are checked, as the schedule makes them (calc/schedule.py).
    checks: list[str] = Field(default_factory=list)


class QueueTask(ApiModel):
    """A task of a queue (agent, downloads, progress). The fields are dynamic — extra='allow' lets everything through;
    nothing needs to be fixed hard."""


class ConformJobChange(ApiModel):
    """The reply to the stop or deletion of a job of the alignment service."""

    done: bool


# --- the «series» block (api_series) ----------------------------------------

class DynamicObject(ApiModel):
    """A dynamic answer (the shape depends on the data or the kind of the series): chapters, composition, media items, previews.
    extra='allow' lets everything through — nothing is cut off; the shape needs not be fixed hard."""


class ErrorOnly(ApiModel):
    """The body of an error of the form {error: ...} (without success) — for the documentation."""
    error: str


class TmdbInfo(ApiModel):
    """The TMDB mapping of a series; the fields are dynamic (tmdb_id, series_name, year, poster_path, last_updated, ...)."""


class SeriesObject(ApiModel):
    """A series (list and card). The fields that are always there are declared; the conditional ones
    (downloaded_episodes_count in the list) pass through
    extra='allow'. The address, type and profile are fields of the sources (`sources`), not of the series.
    Do NOT apply exclude_none to a series: the null fields (quality_override, tmdb_info) must stay."""
    id: int
    name: str
    name_en: str
    site: str
    save_path: str
    season: str | None = None  # nullable in the database
    sources: list[SeriesSource]
    auto_scan_enabled: bool
    is_busy: bool
    statuses: list[str]
    quality: str | None = None
    quality_override: str | None = None
    resolution_override: str | None = None
    ignored_seasons: str | None = None
    ignored_episodes: str | None = None
    last_scan_time: str | None = None
    tmdb_info: TmdbInfo | None = None
    # The folder a save asked for, where the next sync moves the files (I-59); the card only.
    planned_save_path: str | None = None


class SourceDraft(RequestModel):
    """A source of a series that does not exist yet: the fields of `SeriesSource` the caller chooses."""
    url: str
    source_type: SourceType
    parser_profile_id: int | None = None
    vibix_season: int | None = None
    players_off: list[str] = Field(default_factory=list)
    rows_per_line: str | None = None


class SourceAdd(SourceDraft):
    """A source added to a series; without `position` it goes to the end of the list."""
    position: int | None = None


class SourceChange(RequestModel):
    """The fields of a source a caller may change; a field that is not sent stays, `null` clears a profile or a choice."""
    url: str | None = None
    parser_profile_id: int | None = None
    vibix_season: int | None = None
    players_off: list[str] | None = None
    rows_per_line: str | None = None
    position: int | None = None

    @model_validator(mode="after")
    def _only_a_choice_may_be_cleared(self):
        # The profile and the Vibix season are choices that can be taken back; the rest of the fields always has a value.
        for name in ("url", "players_off", "rows_per_line", "position"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be cleared")
        return self


class CreatedSource(ApiModel):
    id: int


class ChangedSource(ApiModel):
    """`applied` holds the fields that differed from the stored ones; nothing applied is no change and no event."""
    success: bool
    applied: dict[str, Any] = Field(default_factory=dict)


class SeriesFields(RequestModel):
    """The fields of the series itself (not of its sources), as the add and the edit forms send them."""
    name: str | None = None
    name_en: str | None = None
    site: str | None = None
    save_path: str | None = None
    season: str | None = None
    quality: str | None = None
    quality_override: str | None = None
    resolution_override: str | None = None
    auto_scan_enabled: bool | None = None
    ignored_seasons: str | None = None
    ignored_episodes: str | None = None
    tmdb_data: dict[str, Any] | None = None


class SeriesCreate(SeriesFields):
    """A new series: its own fields and at least one source. The address, kind and profile of the series are the
    fields of a source, so the series model does not know `url`, `source_type`, `parser_profile_id`,
    `vk_search_mode` nor `vk_quality_priority`."""
    name: str
    name_en: str
    site: str
    save_path: str
    sources: list[SourceDraft] = Field(min_length=1)


class SeriesUpdate(SeriesFields):
    """The properties of a series to save; only the fields sent change."""


class CreatedSeries(ApiModel):
    success: bool
    series_id: int


# --- the output section (api_output) -----------------------------------------

class OutputSettingsSet(RequestModel):
    """The body of the command `composition.settings.set` without the series, which the path names; the fields
    are those of core.contracts.SettingsSet without `series_id`."""
    season: int | None = None
    episode: int | None = None
    compilation: str | None = None
    lever: str
    target: str | None = None
    value: Any = None


class OutputSettingsBatch(RequestModel):
    """The body of the command `composition.settings.set_many` without the series: the levers to set, each as the body of
    one lever, applied in order in one write."""
    items: list[OutputSettingsSet] = Field(min_length=1)


class OutputSettingsReset(RequestModel):
    """The body of the command `composition.settings.reset` without the series; `queue` takes every lever of that queue
    on the level back (the markup reference, stage 14)."""
    season: int | None = None
    episode: int | None = None
    compilation: str | None = None
    lever: str | None = None
    target: str | None = None
    queue: str | None = None


class VoiceOrderBody(RequestModel):
    """The body of the command `composition.voice_order.set` without the series: a season and its voice-overs in order."""
    season: int
    labels: list[str]


class VoiceTitleBody(RequestModel):
    """The body of the command `composition.voice_title.set` without the series: a voice-over group and the name of its
    track in the containers (empty: the name of the group)."""
    label: str
    title: str


class OutputSwitchBody(RequestModel):
    """The body of the command `scan.output.switch` without the series: episodes of a season (one or many at once), or
    without `episodes` the whole season, off or on."""
    season: int
    episodes: list[int] | None = Field(default=None, min_length=1)
    off: bool


# --- the test of rules on a page (api_sources) -------------------------------

class FreshRequest(RequestModel):
    """The pages (canonical addresses) whose freshness a window shows."""

    urls: list[str] = Field(max_length=500)


class TableReadRequest(RequestModel):
    """Read the page of an address now."""

    url: str
    # How many rows of each line of a title of the provider to read: `all`, `N` (the first N) or `-N` (the last N); none is all.
    rows: str | None = None


class RulesTestRequest(RequestModel):
    """The test of a profile on a page: the rows of the page as the constructor gets them, each with its answer.
    With `lines` (typed, or the file names of a release) the profile is tried on those lines and no page is read."""
    url: str = ""
    profile_id: int
    series_season: str | int | None = None
    # False asks again after a read ended: the window reads nothing, it only takes the table that is there.
    refresh: bool = True
    lines: list[str] | None = None

    @model_validator(mode="after")
    def _names_a_page_or_lines(self) -> "RulesTestRequest":
        if self.lines is None and not self.url:
            raise ValueError("an address or lines are needed")
        return self
