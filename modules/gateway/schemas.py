"""Pydantic models of the gateway: the shapes of its answers and the bodies of its requests.

The ADDITIVE RULE: by default `response_model` cuts the fields a model lacks, so that the shape of an
answer does not change silently the base model is `extra="allow"` (extra fields pass through). A route with
optional fields takes `response_model_exclude_none=True`, or FastAPI adds a `null` field.

New routes are built on the models of core/contracts without copies of their fields. A request body that
knows its fields is a model with `extra="forbid"`: a field the model lacks (a removed one too) is refused
by name, not lost.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.contracts import DubItem, SeriesKind, SeriesSource, SourceType


class ApiModel(BaseModel):
    """База: лишние поля не отрезаются (additive-инвариант)."""
    model_config = ConfigDict(extra="allow")


class RequestModel(BaseModel):
    """A request body: an unknown field is refused by its name."""
    model_config = ConfigDict(extra="forbid")


class OkResponse(ApiModel):
    """Стандартный успех: {success: true[, message]}."""
    success: bool
    message: str | None = None


class ErrorResponse(ApiModel):
    """Ошибка (отдаётся через JSONResponse; здесь — для документации
    `responses={code: {"model": ErrorResponse}}`)."""
    success: bool = False
    error: str


# --- блок «скан и очереди» (api_system) -------------------------------------

class ScannerStatus(ApiModel):
    scanner_enabled: bool
    is_scanning: bool
    is_awaiting_tasks: bool
    scan_interval: int
    next_scan_time: str | None = None


class QueueTask(ApiModel):
    """Задача очереди (агент/загрузки/прогресс). Поля динамические —
    extra='allow' пропускает всё; жёстко фиксировать не требуется."""


class ConformJobChange(ApiModel):
    """The reply to the stop or deletion of a job of the alignment service."""

    done: bool


# --- блок «серии» (api_series) ----------------------------------------------

class DynamicObject(ApiModel):
    """Динамический ответ (форма зависит от данных/типа серии): главы,
    композиция, медиа-элементы, превью. extra='allow' пропускает всё —
    ничего не отрезается; жёстко фиксировать форму не требуется."""


class ErrorOnly(ApiModel):
    """Тело ошибки вида {error: ...} (без success) — для документации."""
    error: str


class TmdbInfo(ApiModel):
    """TMDB-маппинг серии; поля динамические (tmdb_id, series_name, year,
    poster_path, last_updated, ...)."""


class VoiceDubs(ApiModel):
    """The voice-overs of a series; the shape of a record is the contract model core.contracts.DubItem."""
    items: list[DubItem]


class ParseUrlRequest(RequestModel):
    url: str


class ParsedUrl(ApiModel):
    """A parsed torrent page for the add form. A page that a site reads is not a torrent page: it is refused
    with the reason `not_a_torrent_page` and shown by `composition.preview`."""
    success: bool
    title: dict[str, Any] | None = None
    torrents: list[dict[str, Any]] = []
    tracker_info: dict[str, Any] | None = None


class SeriesObject(ApiModel):
    """A series (list and card). The fields that are always there are declared; the conditional ones
    (tracker_info for a series with a tracker address, downloaded_episodes_count in the list) pass through
    extra='allow'. The address, type and profile are fields of the sources (`sources`), not of the series.
    Do NOT apply exclude_none to a series: the null fields (quality_override, tmdb_info) must stay."""
    id: int
    name: str
    name_en: str
    site: str
    save_path: str
    season: str | None = None  # в БД season nullable
    kind: SeriesKind
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


class SourceDraft(RequestModel):
    """A source of a series that does not exist yet: the fields of `SeriesSource` the caller chooses."""
    url: str
    source_type: SourceType
    parser_profile_id: int | None = None
    vk_search_mode: str = "search"
    vibix_season: int | None = None
    players_off: list[str] = Field(default_factory=list)


class SourceAdd(SourceDraft):
    """A source added to a series; without `position` it goes to the end of the list."""
    position: int | None = None


class SourceChange(RequestModel):
    """The fields of a source a caller may change; a field that is not sent stays, `null` clears a profile or a choice."""
    url: str | None = None
    parser_profile_id: int | None = None
    vk_search_mode: str | None = None
    vibix_season: int | None = None
    players_off: list[str] | None = None
    position: int | None = None

    @model_validator(mode="after")
    def _only_a_choice_may_be_cleared(self):
        # The profile and the Vibix season are choices that can be taken back; the rest of the fields always has a value.
        for name in ("url", "vk_search_mode", "players_off", "position"):
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
    torrents: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def _torrents_belong_to_a_torrent_source(self):
        if self.torrents and not any(each.source_type == SourceType.TORRENT for each in self.sources):
            raise ValueError("torrents are the releases of a torrent source")
        return self


class SeriesUpdate(SeriesFields):
    """The properties of a series to save; only the fields sent change."""


class NamingPreviewItem(RequestModel):
    """A release of the add form: a magnet is fetched through qBit, a link is a .torrent."""
    torrent_id: str | int | None = None
    magnet: str | None = None
    link: str | None = None


class NamingPreviewRequest(RequestModel):
    items: list[NamingPreviewItem] = Field(default_factory=list)


class NamingPreviewAccepted(ApiModel):
    accepted: list[str | int | None]


class CreatedSeries(ApiModel):
    success: bool
    series_id: int


# --- the output section (api_output) -----------------------------------------

class OutputSettingsSet(RequestModel):
    """The body of the command `composition.settings.set` without the series, which the path names; the fields
    are those of core.contracts.SettingsSet (a test keeps them equal)."""
    season: int | None = None
    episode: int | None = None
    lever: str
    target: str | None = None
    value: Any = None


class OutputSettingsReset(RequestModel):
    """The body of the command `composition.settings.reset` without the series."""
    season: int | None = None
    episode: int | None = None
    lever: str | None = None
    target: str | None = None


# --- the test of rules on a page (api_sources) -------------------------------

class RulesTestRequest(RequestModel):
    """The test of a profile on a page: the rows of the page as the constructor gets them, each with its answer."""
    url: str
    options: dict[str, str] = Field(default_factory=dict)
    profile_id: int
    series_season: str | int | None = None
