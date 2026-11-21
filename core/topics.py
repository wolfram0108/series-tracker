"""The registry of the topics of the bus: everything known about a topic, written once.

A topic is a record, `Topic`: its name, its kind, the module that owns it, the models of what is sent to it and of the
answer, and the fuse of a request for it. The bus knows none of it and only delivers; `BaseModule` reads the record: a
request goes as an envelope of the kind of its topic and waits under its fuse. Each record is a constant named after its
topic (`CATALOG_SERIES_GET` for `catalog.series.get`); `TOPICS` finds a record by the name.

Kind. `query` asks and changes nothing; `command` changes the state of its owner or of the world outside (a file, the
torrent client, a service) and may be answered; `event` tells a fact that has happened, and nobody answers it. A topic
that both reads and changes is a query when its change is a cache, a counter or a view counted on demand: the owner keeps
what it needs and every reader gets the same answer. A read that also acts at the word of the caller (`refresh`) is split
into a query and a command (`sources.table.get` and `sources.table.refresh`); a topic that still has such a word says so in
its note.

Owner. The module whose handler answers a query or a command, or whose fact an event tells. A protocol that several
modules speak (`series.purged`, the contributions to a status, the work of a run) is owned by the module that gives it
its meaning; the assembly of the process (`run`) owns the one topic it tells itself.

`request` is the model of what is sent to the topic (the payload of a query, a command or an event) and `reply` the
model of the answer, both from `core.contracts`. `None` where the payload is still a dictionary: the model comes with the
pass of the module that owns the topic.

Fuse. The price of an answer is known by the module that gives it, not by the one that asks, so the owner of a topic
states its fuse here, once, and `deadline_of` gives it (times `units` for a topic whose work is one step for each unit).
A fuse is a safeguard, not a way to wait: the expiry of one is a failed step, and the answer that comes after it is not
looked for. Callers do not choose a number. A topic that is not here is asked as a query under the standard fuse (a test,
a one-off wrapper).

Fuses nest. A request that is made of steps lives longer than all of them together, so its fuse is derived from theirs by
`after` (its own work and the steps it awaits, one after another): an asker that gave up before its answer finished would
report a timeout for an action the answerer went on to apply. A request whose length is the sum of an unbounded number of
steps (a scan of a torrent series: one step for every torrent) has no fuse of its own (`fuse=None`): each of its steps has
one, and a number that fires in the middle of a working scan would report a failure of something that goes on.

None of the numbers was measured as a price: they are the longest fuse any caller gave the topic before this table
existed, kept so that no step is cut shorter than it was.

The sections go in the order of what their fuses await, because `after` names the constants defined above it: a cycle or
a misspelled step is an error at import.
The order: auth, catalog, rules, settings, conform, sources, voices, composition, downloads, gateway, library, metadata,
planner, renaming, run, scan, torrents, trackerauth, unpack.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final, Literal, Mapping

from pydantic import BaseModel

from .contracts import (
    ManifestApplied, MirrorsSaved, MirrorsSet, PageUrl, PagesCheck, PagesCheckReply, PagesForgotten, StreamReply,
    TableCancel, TableCancelled, TableRead, TableRefresh, TablesFresh, TablesFreshReply, TermSaved, TermSet, TermsReply,
    TorrentFile, TorrentFileDrop, TorrentFileGet, TrackersReply, VideoFetch, VideoFetched, VideoFetchStop, VideoFetchStopped,
    VideoStream,
    CredentialsChanged, CredentialsReply, CredentialsSaved, CredentialsSet, FetchReply, FetchRequest, SecretReply, ServiceRef,
    MapGet, MapListReply, MapReply, MapSaved, MapSet, SeasonsRecompute, SeasonsRecounted, TmdbDetails, TmdbDetailsRequest,
    TmdbSearchReply, TmdbSearchRequest,
    AdminState, AlignmentJob, BusyContribution, CompositionChanged, CompositionFailed, CompositionGetRequest,
    CompositionPreviewReply, CompositionPreviewRequest, CompositionReply, ConformParams, ConformSettingsDraft,
    ConformSettingsSaved, ConformSettingsView, GroupsResolveReply, GroupsResolveRequest, LoginReply, OutputSwitch,
    PasswordSaved, PlanDelete, PlanForget, PlannerView, PlayersListReply, PlayersListRequest, ProviderSaved, ProviderSettingsDraft, ProviderView,
    QueuesGet, QueuesReply, ReverseReply, ReverseSubmit, RulesApplyReply, RulesApplyRequest, RulesProfileChanged,
    RunChanged, SavedPaths, SeriesCard, SeriesDeleted, SeriesListReply, SeriesPurged, SeriesReadProgress,
    SeriesStatusChanged, SeriesUpdated, SettingsReset, SettingsSet, SettingsSetMany, SettingValue, SettingValues,
    SiteResolveReply, SiteResolveRequest, SourceEvent, SourcesReply, SourcesSettingsView, StatusContribution,
    SubmitReply, TableFailed, TableGetReply, TableGetRequest, TableProgress, TableReady, TitleSearchReply,
    TitleSearchRequest, VoiceOrderSet, VoiceSettingsDraft, VoiceSettingsSaved, VoiceSettingsView, VoiceTitleSet,
    WorkState, WorkWithdrawn,
    DirectoriesGet, DirectoryListing, Relocated, RelocationFinished, RelocationOf, RelocationPath, RelocationPlan,
    RelocationPlanned, RelocationStarted,
    CardRemove, CardRemoved, ConformFolders, ConformQueue, DaemonUp, DownloadFetch, DownloadLimits, DownloadQueue, FetchCancel,
    FetchCancelled, FetchesWithdrawn, FetchFinished, FetchStarted, JobCancelled, JobFolders, JobFoldersOf, JobFound, JobKey, JobKeys,
    JobRef, JobRemoved, JobSubmit, LimitsSaved, LimitsSet, OpenRuns, OutputSwitched, PlotReply, PlotRef, QueueCleared, RunCancelled,
    ScanStarted, ScanStatus, ScheduleSaved, ScheduleSet, SeriesRun, SeriesStop, SyncResult, EditAdmitted, EditBegin, EditEnd,
    SeriesRef, VideoProgress,
    Applied, Created, Done, DownloadedCounts, FailuresForgotten, FilesHeld, PackJournals, PackPicture, PackQueue, PackRemoved,
    PageChanged, PlanDropped, PlanRecorded, Recounted, ReleaseReadProgress, SeasonsHeld, SeriesDownloaded, SeriesRecount,
    SseClients, SyncAccepted, SyncStopped, SyncTrace, TableTrace,
)
from .contracts.provider import ProviderState
from .contracts.torrent import (PageReleases, ReleaseRef, ReleasesChecked, ReleasesReply, ReleaseView, ReleaseWatch, StagingFolder,
                                StagingSaved, StagingSet, TorrentTasks)

TopicKind = Literal["query", "command", "event"]
QUERY: Final = "query"
COMMAND: Final = "command"
EVENT: Final = "event"


@dataclass(frozen=True, slots=True)
class Topic:
    """What is known of one topic. `fuse` is the seconds a request for it waits, `None` for a topic whose request has no
    fuse of its own and for an event, which nobody asks."""

    name: str
    kind: TopicKind
    owner: str
    request: type[BaseModel] | None = None
    reply: type[BaseModel] | None = None
    fuse: float | None = None


# A read or a write of one record in the store of the module that answers.
QUICK_S = 10.0
# The rest of the queries: the answer asks a neighbour or looks at the disk; also the fuse of a topic not listed.
STANDARD_S = 30.0
# The answer writes a file, registers something or asks an external service.
SERVICE_S = 60.0
# The answer walks the files of a series, drives the torrent client or captures a stream with a browser.
HEAVY_S = 120.0
# The answer fetches a file from a tracker.
FETCH_S = 180.0
# The answer applies a profile of rules to a table or reads a page of a site; a table of a thousand rows is minutes.
RULES_S = 300.0
# The answer renames or registers the files of a whole series.
BATCH_S = 600.0
# The detection of a change that does not end: a sync waits for the changes admitted before it, and the longest of them
# (a deep adoption renames a whole series) answers within BATCH_S. Past it the wait is told in the log as a failure; the
# admission is not released (a write that still comes would land in the sync).
EDIT_STUCK_S = BATCH_S

# A read of one page of a site, measured on small releases (M-7, M-8: 0.42 s a row at ruchime, 0.14 s at dunhuatv); the
# largest release known, about 1200 rows, is about 8 minutes, so twice that is under this value.
TABLE_READ_S = 1800.0
# The wait of a scan for the tables of its sources: twice a read, as the read itself may start late.
TABLE_WAIT_S = 2 * TABLE_READ_S


def after(own: float, *steps: Topic) -> float | None:
    """The fuse of a request made of steps: its own work and the fuses of the topics it awaits one after another; `None`
    when one of them has none."""
    inner = [step.fuse for step in steps]
    return None if any(fuse is None for fuse in inner) else own + sum(inner)


def each(count: int, step: Topic) -> tuple[Topic, ...]:
    """A step awaited `count` times, for `after`."""
    return (step,) * count


# --- auth --------------------------------------------------------------------------------------------------------
AUTH_EXISTS = Topic("auth.exists", QUERY, "auth", reply=AdminState, fuse=QUICK_S)
# A password found right also renews an outdated hash of it, in the table of the module itself.
AUTH_LOGIN = Topic("auth.login", QUERY, "auth", reply=LoginReply, fuse=QUICK_S)
AUTH_PASSWORD_SET = Topic("auth.password.set", COMMAND, "auth", reply=PasswordSaved, fuse=QUICK_S)

# --- catalog -----------------------------------------------------------------------------------------------------
# Answered as the models themselves (immutable): a module of the process reads them as they are, the gateway writes them for the window.
CATALOG_SERIES_CREATE = Topic("catalog.series.create", COMMAND, "catalog", reply=SeriesCard, fuse=STANDARD_S)
CATALOG_SERIES_DELETE = Topic("catalog.series.delete", COMMAND, "catalog", reply=Done, fuse=STANDARD_S)
CATALOG_SERIES_GET = Topic("catalog.series.get", QUERY, "catalog", reply=SeriesCard, fuse=STANDARD_S)
CATALOG_SERIES_LIST = Topic("catalog.series.list", QUERY, "catalog", reply=SeriesListReply, fuse=STANDARD_S)
CATALOG_SERIES_SET_SAVE_PATH = Topic("catalog.series.set_save_path", COMMAND, "catalog", reply=Done, fuse=STANDARD_S)
CATALOG_SERIES_TOUCH_SCAN_TIME = Topic("catalog.series.touch_scan_time", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_UPDATE = Topic("catalog.series.update", COMMAND, "catalog", reply=Applied, fuse=STANDARD_S)
CATALOG_SOURCES_ADD = Topic("catalog.sources.add", COMMAND, "catalog", reply=Created, fuse=QUICK_S)
CATALOG_SOURCES_BY_PROFILE = Topic("catalog.sources.by_profile", QUERY, "catalog", reply=SourcesReply, fuse=STANDARD_S)
CATALOG_SOURCES_LIST = Topic("catalog.sources.list", QUERY, "catalog", reply=SourcesReply, fuse=QUICK_S)
CATALOG_SOURCES_REMOVE = Topic("catalog.sources.remove", COMMAND, "catalog", reply=Done, fuse=QUICK_S)
CATALOG_SOURCES_UPDATE = Topic("catalog.sources.update", COMMAND, "catalog", reply=Applied, fuse=QUICK_S)
CATALOG_VIEWING_START = Topic("catalog.viewing.start", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_VIEWING_STOP = Topic("catalog.viewing.stop", COMMAND, "catalog", fuse=STANDARD_S)
SERIES_ADDED = Topic("series.added", EVENT, "catalog", request=SeriesCard)
SERIES_BUSY_CHANGED = Topic("series.busy.changed", EVENT, "catalog", request=SeriesStatusChanged)
# Protocol of the contributions to the status of a series: published by library and scan, read by catalog.
SERIES_BUSY_CONTRIBUTION = Topic("series.busy.contribution", EVENT, "catalog", request=BusyContribution)
SERIES_DELETED = Topic("series.deleted", EVENT, "catalog", request=SeriesDeleted)
# The report of a module to the catalog after series.deleted; published by every module that holds data of a series.
SERIES_PURGED = Topic("series.purged", EVENT, "catalog", request=SeriesPurged)
SERIES_SOURCE_ADDED = Topic("series.source.added", EVENT, "catalog", request=SourceEvent)
SERIES_SOURCE_REMOVED = Topic("series.source.removed", EVENT, "catalog", request=SourceEvent)
SERIES_SOURCE_UPDATED = Topic("series.source.updated", EVENT, "catalog", request=SourceEvent)
SERIES_STATUS_CHANGED = Topic("series.status.changed", EVENT, "catalog", request=SeriesStatusChanged)
# Protocol of the contributions to the status of a series: published by torrents, scan and planner.
SERIES_STATUS_CONTRIBUTION = Topic("series.status.contribution", EVENT, "catalog", request=StatusContribution)
# Published by the catalog (a written card, a touch of the scan time) and by metadata (the aggregate of TMDB): what changed.
SERIES_UPDATED = Topic("series.updated", EVENT, "catalog", request=SeriesUpdated)

# --- rules -------------------------------------------------------------------------------------------------------
RULES_APPLY = Topic("rules.apply", QUERY, "rules", request=RulesApplyRequest, reply=RulesApplyReply, fuse=RULES_S)
RULES_FORMAT_FILENAME = Topic("rules.format_filename", QUERY, "rules", fuse=STANDARD_S)
RULES_FORMAT_TORRENT_FILE = Topic("rules.format_torrent_file", QUERY, "rules", fuse=SERVICE_S)
RULES_PROFILE_CHANGED = Topic("rules.profile.changed", EVENT, "rules", request=RulesProfileChanged)
RULES_PROFILES_CREATE = Topic("rules.profiles.create", COMMAND, "rules", reply=Created, fuse=QUICK_S)
RULES_PROFILES_DELETE = Topic(
    "rules.profiles.delete", COMMAND, "rules", reply=Done,
    fuse=after(QUICK_S, CATALOG_SOURCES_BY_PROFILE, CATALOG_SOURCES_BY_PROFILE))
RULES_PROFILES_EXPORT = Topic("rules.profiles.export", QUERY, "rules", fuse=QUICK_S)
RULES_PROFILES_LIST = Topic("rules.profiles.list", QUERY, "rules", fuse=QUICK_S)
RULES_PROFILES_UPDATE = Topic("rules.profiles.update", COMMAND, "rules", reply=Done, fuse=QUICK_S)
RULES_RULES_ADD = Topic("rules.rules.add", COMMAND, "rules", reply=Created, fuse=QUICK_S)
RULES_RULES_DELETE = Topic("rules.rules.delete", COMMAND, "rules", reply=Done, fuse=QUICK_S)
RULES_RULES_LIST = Topic("rules.rules.list", QUERY, "rules", fuse=QUICK_S)
RULES_RULES_REORDER = Topic("rules.rules.reorder", COMMAND, "rules", reply=Done, fuse=QUICK_S)
RULES_RULES_UPDATE = Topic("rules.rules.update", COMMAND, "rules", reply=Done, fuse=QUICK_S)

# --- settings ----------------------------------------------------------------------------------------------------
# The packet tells the value of a secret as an empty one (SettingValue).
SETTINGS_CHANGED = Topic("settings.changed", EVENT, "settings", request=SettingValue)
SETTINGS_PATHS_ADD = Topic("settings.paths.add", COMMAND, "settings", reply=SavedPaths, fuse=QUICK_S)
SETTINGS_PATHS_LIST = Topic("settings.paths.list", QUERY, "settings", reply=SavedPaths, fuse=QUICK_S)
SETTINGS_PATHS_REMOVE = Topic("settings.paths.remove", COMMAND, "settings", reply=SavedPaths, fuse=QUICK_S)
SETTINGS_VALUE_GET = Topic("settings.value.get", QUERY, "settings", reply=SettingValue, fuse=STANDARD_S)
SETTINGS_VALUE_SET = Topic("settings.value.set", COMMAND, "settings", reply=SettingValue, fuse=QUICK_S)
SETTINGS_VALUES_BY_PREFIX = Topic("settings.values.by_prefix", QUERY, "settings", reply=SettingValues, fuse=QUICK_S)
SETTINGS_VALUES_SET_MANY = Topic("settings.values.set_many", COMMAND, "settings", reply=SettingValues, fuse=QUICK_S)

# --- conform -----------------------------------------------------------------------------------------------------
CONFORM_CANCEL = Topic("conform.cancel", COMMAND, "conform", request=JobRef, reply=JobCancelled, fuse=STANDARD_S)
CONFORM_DAEMON_UP = Topic("conform.daemon.up", EVENT, "conform", request=DaemonUp)
CONFORM_JOB_FIND = Topic("conform.job.find", QUERY, "conform", request=JobKey, reply=JobFound, fuse=STANDARD_S)
CONFORM_JOB_FINISHED = Topic("conform.job.finished", EVENT, "conform", request=AlignmentJob)
# Pure: no service is asked. Where the service is told to write the tracks of an episode folder and to keep its cache.
CONFORM_JOB_FOLDERS = Topic(
    "conform.job.folders", QUERY, "conform", request=JobFoldersOf, reply=JobFolders, fuse=QUICK_S)
CONFORM_JOB_GET = Topic(
    "conform.job.get", QUERY, "conform", request=JobRef, reply=AlignmentJob,
    fuse=after(STANDARD_S, SETTINGS_VALUE_GET, SETTINGS_VALUE_GET))
CONFORM_JOB_KEYS = Topic("conform.job.keys", QUERY, "conform", reply=JobKeys, fuse=STANDARD_S)
CONFORM_JOB_REMOVED = Topic("conform.job.removed", EVENT, "conform", request=JobRemoved)
CONFORM_PARAMS_SET = Topic(
    "conform.params.set", COMMAND, "conform", request=ConformParams, reply=ConformSettingsSaved,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUES_BY_PREFIX))
CONFORM_PLOT = Topic("conform.plot", QUERY, "conform", request=PlotRef, reply=PlotReply, fuse=STANDARD_S)
CONFORM_QUEUE_CHANGED = Topic("conform.queue.changed", EVENT, "conform", request=ConformQueue)
CONFORM_REVERSE = Topic("conform.reverse", COMMAND, "conform", request=ReverseSubmit, reply=ReverseReply, fuse=STANDARD_S)
CONFORM_SETTINGS_CHANGED = Topic("conform.settings.changed", EVENT, "conform", request=ConformSettingsView)
CONFORM_SETTINGS_GET = Topic(
    "conform.settings.get", QUERY, "conform", reply=ConformSettingsView,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX))
CONFORM_SETTINGS_SET = Topic(
    "conform.settings.set", COMMAND, "conform", request=ConformSettingsDraft, reply=ConformSettingsSaved,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUES_BY_PREFIX))
CONFORM_SUBMIT = Topic("conform.submit", COMMAND, "conform", request=JobSubmit, reply=SubmitReply, fuse=SERVICE_S)
CONFORM_WORKSPACE = Topic("conform.workspace", QUERY, "conform", reply=ConformFolders, fuse=after(QUICK_S, SETTINGS_VALUE_GET))

# --- sources -----------------------------------------------------------------------------------------------------
# The reads of sources (a table, a check of pages, a search, a stream) wait for the first look at the provider when a page of its world
# is asked; they take no order from one another, so their handlers are concurrent and a wait does not hold the queue.
SOURCES_PAGES_CHECK = Topic(
    "sources.pages.check", QUERY, "sources", request=PagesCheck, reply=PagesCheckReply, fuse=STANDARD_S)
SOURCES_PAGES_FORGOTTEN = Topic("sources.pages.forgotten", EVENT, "sources", request=PagesForgotten)
# The network is read, the table is not written. Nobody asks it over the bus: it leaves with the old torrent pipeline (stage 15 of the
# mission voice-alignment, step 11), `sources.releases.get` is its live user (inside the module). Its answer stays a dictionary.
SOURCES_PARSE = Topic("sources.parse", QUERY, "sources", fuse=RULES_S)
SOURCES_PLAYERS_LIST = Topic(
    "sources.players.list", QUERY, "sources", request=PlayersListRequest, reply=PlayersListReply,
    fuse=QUICK_S)
SOURCES_PROVIDER_CHANGED = Topic("sources.provider.changed", EVENT, "sources", request=ProviderView)
# Takes the next number of the picture: a read. The socket of the provider: its settings with the sources of the series,
# and the provider asked.
SOURCES_PROVIDER_GET = Topic(
    "sources.provider.get", QUERY, "sources", reply=ProviderView,
    fuse=after(SERVICE_S, CATALOG_SERIES_LIST, CONFORM_WORKSPACE))
SOURCES_PROVIDER_MANIFEST = Topic("sources.provider.manifest", EVENT, "sources", request=ManifestApplied)
SOURCES_PROVIDER_SET = Topic(
    "sources.provider.set", COMMAND, "sources", request=ProviderSettingsDraft, reply=ProviderSaved,
    fuse=after(SERVICE_S, CATALOG_SERIES_LIST, CONFORM_WORKSPACE, SETTINGS_VALUE_GET, SETTINGS_VALUES_SET_MANY))
SOURCES_PROVIDER_STATE = Topic("sources.provider.state", EVENT, "sources", request=ProviderState)
SOURCES_RELEASES_GET = Topic(
    "sources.releases.get", QUERY, "sources", request=PageUrl, reply=PageReleases, fuse=RULES_S)
SOURCES_SETTINGS_CHANGED = Topic("sources.settings.changed", EVENT, "sources", request=SourcesSettingsView)
SOURCES_SETTINGS_GET = Topic("sources.settings.get", QUERY, "sources", reply=SourcesSettingsView, fuse=QUICK_S)
SOURCES_SITE_RESOLVE = Topic(
    "sources.site.resolve", QUERY, "sources", request=SiteResolveRequest, reply=SiteResolveReply,
    fuse=QUICK_S)
SOURCES_TABLE_CANCEL = Topic(
    "sources.table.cancel", COMMAND, "sources", request=TableCancel, reply=TableCancelled, fuse=STANDARD_S)
SOURCES_TABLE_FAILED = Topic("sources.table.failed", EVENT, "sources", request=TableFailed)
SOURCES_TABLE_FRESH = Topic("sources.table.fresh", EVENT, "sources", request=TableReady)
# The page as it stands, read from nothing: the last complete table with how the page stands (ready, reading, failed, absent).
# Asking the page to be read is `sources.table.refresh`.
SOURCES_TABLE_GET = Topic(
    "sources.table.get", QUERY, "sources", request=TableGetRequest, reply=TableGetReply,
    fuse=SERVICE_S)
SOURCES_TABLE_PROGRESS = Topic("sources.table.progress", EVENT, "sources", request=TableProgress)
SOURCES_TABLE_READ = Topic("sources.table.read", COMMAND, "sources", request=TableRead, fuse=STANDARD_S)
SOURCES_TABLE_READY = Topic("sources.table.ready", EVENT, "sources", request=TableReady)
# Reads the page if its table is not current (the term, a page that waits for the user, a provider that is up) and answers how the
# page stands after that: the same answer as the query, taken in the step that decided.
SOURCES_TABLE_REFRESH = Topic(
    "sources.table.refresh", COMMAND, "sources", request=TableRefresh, reply=TableGetReply, fuse=SERVICE_S)
# What the read of a page does, for the dump of a run (kinds `begin` and `end`); the fields a kind does not tell are not in the packet.
SOURCES_TABLE_TRACE = Topic("sources.table.trace", EVENT, "sources", request=TableTrace)
SOURCES_TABLES_FRESH = Topic(
    "sources.tables.fresh", QUERY, "sources", request=TablesFresh, reply=TablesFreshReply, fuse=STANDARD_S)
SOURCES_TERMS_GET = Topic("sources.terms.get", QUERY, "sources", reply=TermsReply, fuse=STANDARD_S)
SOURCES_TERMS_SET = Topic(
    "sources.terms.set", COMMAND, "sources", request=TermSet, reply=TermSaved,
    fuse=STANDARD_S)
# The search of the titles: the sources of the provider asked (a session opened, two requests, again after a refusal)
# and a page of the search.
SOURCES_TITLES_SEARCH = Topic(
    "sources.titles.search", QUERY, "sources", request=TitleSearchRequest, reply=TitleSearchReply,
    fuse=HEAVY_S)
SOURCES_TORRENT_FILE_DROP = Topic(
    "sources.torrent_file.drop", COMMAND, "sources", request=TorrentFileDrop, fuse=STANDARD_S)
# A miss fetches the file and writes the cache of the module: a cache is no state of the module another reads, so it is a query.
SOURCES_TORRENT_FILE_GET = Topic(
    "sources.torrent_file.get", QUERY, "sources", request=TorrentFileGet, reply=TorrentFile, fuse=FETCH_S)
SOURCES_TRACKER_SET_MIRRORS = Topic(
    "sources.tracker.set_mirrors", COMMAND, "sources", request=MirrorsSet, reply=MirrorsSaved, fuse=QUICK_S)
SOURCES_TRACKERS_LIST = Topic("sources.trackers.list", QUERY, "sources", reply=TrackersReply, fuse=QUICK_S)
# A file a player downloads itself is as long as its episode: the asker watches its progress events under its fuse of
# silence.
SOURCES_VIDEO_FETCH = Topic(
    "sources.video.fetch", COMMAND, "sources", request=VideoFetch, reply=VideoFetched, fuse=None)
SOURCES_VIDEO_FETCH_STOP = Topic(
    "sources.video.fetch.stop", COMMAND, "sources", request=VideoFetchStop, reply=VideoFetchStopped, fuse=QUICK_S)
# How far a download goes: the file `target` and the figures the player or the provider gives (all optional, they differ by player).
SOURCES_VIDEO_PROGRESS = Topic("sources.video.progress", EVENT, "sources", request=VideoProgress)
# A link of Vibix also writes the manifest file and the key: a cache, as the file of a release.
SOURCES_VIDEO_STREAM = Topic(
    "sources.video.stream", QUERY, "sources", request=VideoStream, reply=StreamReply, fuse=HEAVY_S)

# --- voices ------------------------------------------------------------------------------------------------------
VOICES_GROUPS_RESOLVE = Topic(
    "voices.groups.resolve", QUERY, "voices", request=GroupsResolveRequest, reply=GroupsResolveReply,
    fuse=STANDARD_S)
# The order of sources lists the players of the pages of every series.
VOICES_PLAYERS_GET = Topic(
    "voices.players.get", QUERY, "voices", reply=PlayersListReply,
    fuse=after(QUICK_S, CATALOG_SERIES_LIST, SOURCES_PLAYERS_LIST))
VOICES_SETTINGS_CHANGED = Topic("voices.settings.changed", EVENT, "voices", request=VoiceSettingsView)
VOICES_SETTINGS_EXPORT = Topic("voices.settings.export", QUERY, "voices", fuse=QUICK_S)
# The order of sources holds every player the sources know.
VOICES_SETTINGS_GET = Topic(
    "voices.settings.get", QUERY, "voices", reply=VoiceSettingsView,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX, SOURCES_PLAYERS_LIST))
VOICES_SETTINGS_SET = Topic(
    "voices.settings.set", COMMAND, "voices", request=VoiceSettingsDraft, reply=VoiceSettingsSaved,
    fuse=after(QUICK_S, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_BY_PREFIX,
               SOURCES_PLAYERS_LIST))

# --- composition -------------------------------------------------------------------------------------------------
COMPOSITION_CHANGED = Topic("composition.changed", EVENT, "composition", request=CompositionChanged)
COMPOSITION_FAILED = Topic("composition.failed", EVENT, "composition", request=CompositionFailed)
# Answered as the model itself (immutable): the gateway writes it for the window, a module of the process reads it as it is.
COMPOSITION_GET = Topic(
    "composition.get", QUERY, "composition", request=CompositionGetRequest, reply=CompositionReply,
    fuse=STANDARD_S)
# Mixed (`refresh`): the table is asked by `sources.table.refresh`, which begins a read of the page when its table is not current
# (the fuse of that command is the one of the query below); with `lines` a pure query. A preview reads the table, applies the
# profile to its lines and asks for the groups of the names, one after another.
COMPOSITION_PREVIEW = Topic(
    "composition.preview", QUERY, "composition", request=CompositionPreviewRequest, reply=CompositionPreviewReply,
    fuse=after(QUICK_S, SOURCES_TABLE_GET, RULES_APPLY, VOICES_GROUPS_RESOLVE, VOICES_SETTINGS_GET))
COMPOSITION_QUEUES_GET = Topic(
    "composition.queues.get", QUERY, "composition", request=QueuesGet, reply=QueuesReply,
    fuse=after(QUICK_S, VOICES_SETTINGS_GET))
# A recount asks the rules once for each source of the series: the fuse of the table is for one source.
COMPOSITION_RECOMPUTE = Topic(
    "composition.recompute", COMMAND, "composition", request=SeriesRecount, reply=Recounted, fuse=RULES_S)
COMPOSITION_SERIES_READING = Topic("composition.series.reading", EVENT, "composition", request=SeriesReadProgress)
COMPOSITION_SETTINGS_RESET = Topic(
    "composition.settings.reset", COMMAND, "composition", request=SettingsReset, reply=Done,
    fuse=STANDARD_S)
# A lever is checked against the series and the groups of the voice-overs before it is written.
COMPOSITION_SETTINGS_SET = Topic(
    "composition.settings.set", COMMAND, "composition", request=SettingsSet, reply=Done,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
COMPOSITION_SETTINGS_SET_MANY = Topic(
    "composition.settings.set_many", COMMAND, "composition", request=SettingsSetMany, reply=Done,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
COMPOSITION_VOICE_ORDER_SET = Topic(
    "composition.voice_order.set", COMMAND, "composition", request=VoiceOrderSet, reply=Done,
    fuse=STANDARD_S)
COMPOSITION_VOICE_TITLE_SET = Topic(
    "composition.voice_title.set", COMMAND, "composition", request=VoiceTitleSet, reply=Done,
    fuse=STANDARD_S)

# --- downloads ---------------------------------------------------------------------------------------------------
DOWNLOADS_CANCEL = Topic("downloads.cancel", COMMAND, "downloads", request=FetchCancel, reply=FetchCancelled, fuse=STANDARD_S)
DOWNLOADS_CARD_REMOVE = Topic("downloads.card.remove", COMMAND, "downloads", request=CardRemove, reply=CardRemoved, fuse=STANDARD_S)
# Sent as a command nobody answers: the end of the fetch is told by `downloads.fetch.finished`.
DOWNLOADS_FETCH = Topic("downloads.fetch", COMMAND, "downloads", request=DownloadFetch, fuse=STANDARD_S)
DOWNLOADS_FETCH_FINISHED = Topic("downloads.fetch.finished", EVENT, "downloads", request=FetchFinished)
DOWNLOADS_FETCH_STARTED = Topic("downloads.fetch.started", EVENT, "downloads", request=FetchStarted)
DOWNLOADS_LIMITS_GET = Topic("downloads.limits.get", QUERY, "downloads", reply=DownloadLimits, fuse=STANDARD_S)
DOWNLOADS_LIMITS_SET = Topic(
    "downloads.limits.set", COMMAND, "downloads", request=LimitsSet, reply=LimitsSaved, fuse=STANDARD_S)
DOWNLOADS_QUEUE_CHANGED = Topic("downloads.queue.changed", EVENT, "downloads", request=DownloadQueue)
DOWNLOADS_QUEUE_CLEAR = Topic("downloads.queue.clear", COMMAND, "downloads", reply=QueueCleared, fuse=STANDARD_S)
DOWNLOADS_QUEUE_GET = Topic("downloads.queue.get", QUERY, "downloads", reply=DownloadQueue, fuse=STANDARD_S)
DOWNLOADS_WITHDRAW = Topic(
    "downloads.withdraw", COMMAND, "downloads", request=SeriesRef, reply=FetchesWithdrawn, fuse=STANDARD_S)

# --- gateway -----------------------------------------------------------------------------------------------------
# The count of the connected windows (protocol: gateway publishes, others read).
GATEWAY_SSE_CLIENTS = Topic("gateway.sse.clients", EVENT, "gateway", request=SseClients)

# --- library -----------------------------------------------------------------------------------------------------
LIBRARY_DIRECTORIES_LIST = Topic(
    "library.directories.list", QUERY, "library", request=DirectoriesGet, reply=DirectoryListing, fuse=STANDARD_S)
# One move for each file, each with its own (core.storage.MOVE_TIMEOUT_S).
LIBRARY_RELOCATION_APPLY = Topic(
    "library.relocation.apply", COMMAND, "library", request=RelocationOf, reply=Relocated, fuse=None)
LIBRARY_RELOCATION_FINISHED = Topic("library.relocation.finished", EVENT, "library", request=RelocationFinished)
LIBRARY_RELOCATION_GET = Topic(
    "library.relocation.get", QUERY, "library", request=RelocationOf, reply=RelocationPath, fuse=STANDARD_S)
LIBRARY_RELOCATION_PLAN = Topic(
    "library.relocation.plan", COMMAND, "library", request=RelocationPlan, reply=RelocationPath, fuse=STANDARD_S)
LIBRARY_RELOCATION_PLANNED = Topic("library.relocation.planned", EVENT, "library", request=RelocationPlanned)
LIBRARY_RELOCATION_STARTED = Topic("library.relocation.started", EVENT, "library", request=RelocationStarted)

# --- metadata ----------------------------------------------------------------------------------------------------
# The read of TMDB over the network: a query (it changes nothing here).
METADATA_DETAILS = Topic(
    "metadata.details", QUERY, "metadata", request=TmdbDetailsRequest, reply=TmdbDetails, fuse=STANDARD_S)
METADATA_MAP_GET = Topic("metadata.map.get", QUERY, "metadata", request=MapGet, reply=MapReply, fuse=QUICK_S)
METADATA_MAP_LIST = Topic("metadata.map.list", QUERY, "metadata", reply=MapListReply, fuse=QUICK_S)
METADATA_MAP_SET = Topic("metadata.map.set", COMMAND, "metadata", request=MapSet, reply=MapSaved, fuse=QUICK_S)
METADATA_SEARCH = Topic(
    "metadata.search", QUERY, "metadata", request=TmdbSearchRequest, reply=TmdbSearchReply, fuse=STANDARD_S)
# The count of the episodes of a series of several seasons: asks the catalog, the plan and TMDB, writes the mapping, tells the card.
METADATA_SEASONS_RECOMPUTE = Topic(
    "metadata.seasons.recompute", COMMAND, "metadata", request=SeasonsRecompute, reply=SeasonsRecounted, fuse=STANDARD_S)

# --- planner -----------------------------------------------------------------------------------------------------
# A deletion and a forgetting are written once the plan of the series is counted again.
PLANNER_DELETE = Topic(
    "planner.delete", COMMAND, "planner", request=PlanDelete, reply=PlanRecorded,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
PLANNER_DELETE_CANCEL = Topic(
    "planner.delete.cancel", COMMAND, "planner", request=PlanDelete, reply=PlanDropped,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
PLANNER_DOWNLOADED_COUNTS = Topic("planner.downloaded_counts", QUERY, "planner", reply=DownloadedCounts, fuse=QUICK_S)
PLANNER_FAILURES_FORGET = Topic(
    "planner.failures.forget", COMMAND, "planner", request=PlanForget, reply=FailuresForgotten,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
# The files of a series also look for a journal in its folder.
PLANNER_FILES_GET = Topic(
    "planner.files.get", QUERY, "planner", reply=FilesHeld, fuse=after(QUICK_S + STANDARD_S, CATALOG_SERIES_GET))
PLANNER_JOURNAL_CHANGED = Topic("planner.journal.changed", EVENT, "planner", request=PackJournals)
PLANNER_PACK_CHANGED = Topic("planner.pack.changed", EVENT, "planner", request=PackQueue)
PLANNER_PACK_GET = Topic("planner.pack.get", QUERY, "planner", reply=PackPicture, fuse=STANDARD_S)
PLANNER_PACK_REMOVE = Topic("planner.pack.remove", COMMAND, "planner", reply=PackRemoved, fuse=STANDARD_S)
# As planner.series.get. The references are counted from the layout as the view is: the layout, the settings of the
# voice-overs, the names. The answer is a tuple of `Reference` (a row of models has no model of its own).
PLANNER_REFERENCES_GET = Topic(
    "planner.references.get", QUERY, "planner",
    fuse=after(STANDARD_S, CATALOG_SERIES_GET, COMPOSITION_GET, VOICES_SETTINGS_GET))
PLANNER_SEASONS = Topic("planner.seasons", QUERY, "planner", reply=SeasonsHeld, fuse=QUICK_S)
PLANNER_SERIES_CHANGED = Topic("planner.series.changed", EVENT, "planner", request=PlannerView)
# A projection counted on demand (and published) when it is not there yet: a query. The view of the plan reads the table
# of the series. Answered as the model itself (immutable): the gateway writes it for the window.
PLANNER_SERIES_GET = Topic(
    "planner.series.get", QUERY, "planner", reply=PlannerView,
    fuse=after(STANDARD_S, CATALOG_SERIES_GET, COMPOSITION_GET, VOICES_SETTINGS_GET))
# The sync of the plan of a series is written down and started: its work is told by work.series.state.
PLANNER_SYNC = Topic("planner.sync", COMMAND, "planner", reply=SyncAccepted, fuse=QUICK_S)
PLANNER_SYNC_CANCEL = Topic("planner.sync.cancel", COMMAND, "planner", reply=SyncStopped, fuse=STANDARD_S)
# What the sync counted and did, step by step, for the dump of a run (modules/runlog).
PLANNER_SYNC_TRACE = Topic("planner.sync.trace", EVENT, "planner", request=SyncTrace)
# Takes the next number of the state (the order of the states): a read.
PLANNER_WORK_GET = Topic("planner.work.get", QUERY, "planner", reply=WorkState, fuse=QUICK_S)
# Published by planner and by the torrent pipeline; the meaning is the plan's.
SERIES_DOWNLOADED_CHANGED = Topic("series.downloaded.changed", EVENT, "planner", request=SeriesDownloaded)
# The owner of an object tells that its work is not needed (WORK_WITHDRAWN of core.contracts.work).
WORK_WITHDRAWN = Topic("work.withdrawn", EVENT, "planner", request=WorkWithdrawn)

# --- renaming ----------------------------------------------------------------------------------------------------
RENAMING_PROCESS_TORRENT = Topic("renaming.process_torrent", COMMAND, "renaming", fuse=RULES_S)

# --- run ---------------------------------------------------------------------------------------------------------
# Told by run.py when every module is up; the owner is the assembly of the process, not a module.
SYSTEM_STARTED = Topic("system.started", EVENT, "run")

# --- scan --------------------------------------------------------------------------------------------------------
SCAN_ALL_START = Topic("scan.all.start", COMMAND, "scan", reply=ScanStarted, fuse=STANDARD_S)
SCAN_EDIT_BEGIN = Topic(
    "scan.edit.begin", COMMAND, "scan", request=EditBegin, reply=EditAdmitted, fuse=after(QUICK_S, CATALOG_SERIES_GET))
SCAN_EDIT_END = Topic("scan.edit.end", COMMAND, "scan", request=EditEnd, fuse=QUICK_S)
SCAN_OUTPUT_SWITCH = Topic(
    "scan.output.switch", COMMAND, "scan", request=OutputSwitch, reply=OutputSwitched,
    fuse=after(STANDARD_S, *each(4, CATALOG_SERIES_GET), CATALOG_SERIES_UPDATE, DOWNLOADS_QUEUE_GET, COMPOSITION_GET,
               VOICES_SETTINGS_GET))
# A stop of a sync: the run of the series, the work of its plan and the reads of its pages, one after another.
SCAN_RUN_CANCEL = Topic(
    "scan.run.cancel", COMMAND, "scan", request=SeriesStop, reply=RunCancelled,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, PLANNER_SYNC_CANCEL, SOURCES_TABLE_CANCEL))
# The run of a sync of a series (RUN_CHANGED of core.contracts.work).
SCAN_RUN_CHANGED = Topic("scan.run.changed", EVENT, "scan", request=RunChanged)
SCAN_RUNS_GET = Topic("scan.runs.get", QUERY, "scan", reply=OpenRuns, fuse=QUICK_S)
SCAN_SCHEDULE_SET = Topic(
    "scan.schedule.set", COMMAND, "scan", request=ScheduleSet, reply=ScheduleSaved, fuse=STANDARD_S)
# No fuse of its own: a sync is as long as the steps it is made of, each of which has one.
SCAN_SERIES_RUN = Topic("scan.series.run", COMMAND, "scan", request=SeriesRun, reply=SyncResult, fuse=None)
SCAN_STATUS_CHANGED = Topic("scan.status.changed", EVENT, "scan", request=ScanStatus)
SCAN_STATUS_GET = Topic("scan.status.get", QUERY, "scan", reply=ScanStatus, fuse=after(QUICK_S, *each(3, SETTINGS_VALUE_GET)))
# What an executor tells the run about its work of a series (WORK_STATE of core.contracts.work); the run is scan's.
WORK_SERIES_STATE = Topic("work.series.state", EVENT, "scan", request=WorkState)

# --- torrents ----------------------------------------------------------------------------------------------------
# The topics of the old torrent pipeline (the registry `torrents.db.*`, its queue, the preview of the add window) take no models of
# their own: they leave with the pipeline (stage 15 of the mission voice-alignment, step 11); the counts of the downloaded are the one
# answer the window reads next to the planner's, so they have the model of the planner's.
TORRENTS_ADD =Topic("torrents.add", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_COMPOSITION = Topic("torrents.composition", QUERY, "torrents", fuse=900.0)
TORRENTS_DB_ACTIVE = Topic("torrents.db.active", QUERY, "torrents", fuse=SERVICE_S)
TORRENTS_DB_ADD = Topic("torrents.db.add", COMMAND, "torrents", fuse=QUICK_S)
TORRENTS_DB_DEACTIVATE_ALL = Topic("torrents.db.deactivate_all", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_DB_DOWNLOADED_COUNTS = Topic(
    "torrents.db.downloaded_counts", QUERY, "torrents", reply=DownloadedCounts, fuse=STANDARD_S)
TORRENTS_DB_FILES_FOR_SERIES = Topic("torrents.db.files.for_series", QUERY, "torrents", fuse=QUICK_S)
TORRENTS_DB_FILES_LIST = Topic("torrents.db.files.list", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_DB_FILES_UPSERT = Topic("torrents.db.files.upsert", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_DB_HISTORY = Topic("torrents.db.history", QUERY, "torrents", fuse=QUICK_S)
# The monitor names the series of the old pipeline and the series whose torrent source is the page of a release.
TORRENTS_DB_PROGRESS_LIST = Topic(
    "torrents.db.progress.list", QUERY, "torrents", reply=TorrentTasks,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, CATALOG_SERIES_LIST, SOURCES_SITE_RESOLVE))
TORRENTS_DELETE = Topic("torrents.delete", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_DRIVE_INCOMPLETE = Topic("torrents.drive_incomplete", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_FILES_GET = Topic("torrents.files.get", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_FS_VERIFY = Topic("torrents.fs.verify", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_INFO_GET = Topic("torrents.info.get", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_PAUSE = Topic("torrents.pause", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_PREVIEW_FAILED = Topic("torrents.preview.failed", EVENT, "torrents")
TORRENTS_PREVIEW_FETCH = Topic("torrents.preview.fetch", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_PREVIEW_READY = Topic("torrents.preview.ready", EVENT, "torrents")
TORRENTS_PROGRESS_CHANGED = Topic("torrents.progress.changed", EVENT, "torrents", request=TorrentTasks)
TORRENTS_QUEUE_CHANGED = Topic("torrents.queue.changed", EVENT, "torrents")
TORRENTS_QUEUE_GET = Topic("torrents.queue.get", QUERY, "torrents", fuse=QUICK_S)
TORRENTS_RECHECK = Topic("torrents.recheck", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_REGISTER = Topic("torrents.register", COMMAND, "torrents", fuse=BATCH_S)
TORRENTS_RELEASE_CHANGED = Topic("torrents.release.changed", EVENT, "torrents", request=ReleaseView)
# The releases of a tracker's page: the page read on the tracker, a new version's file fetched, the client driven.
TORRENTS_RELEASE_CHECK = Topic(
    "torrents.release.check", COMMAND, "torrents", request=PageUrl, reply=ReleasesChecked,
    fuse=after(HEAVY_S, SOURCES_SITE_RESOLVE, SOURCES_RELEASES_GET, SOURCES_TORRENT_FILE_GET))
TORRENTS_RELEASE_DOWNLOAD = Topic(
    "torrents.release.download", COMMAND, "torrents", request=ReleaseRef, reply=ReleaseView,
    fuse=after(HEAVY_S, SOURCES_SITE_RESOLVE, SOURCES_RELEASES_GET, SOURCES_TORRENT_FILE_GET))
TORRENTS_RELEASE_DROP_FILES = Topic("torrents.release.drop_files", COMMAND, "torrents", request=ReleaseRef, fuse=STANDARD_S)
TORRENTS_RELEASE_WATCH = Topic(
    "torrents.release.watch", COMMAND, "torrents", request=ReleaseWatch, reply=ReleaseView, fuse=STANDARD_S)
TORRENTS_RELEASES_GET = Topic(
    "torrents.releases.get", QUERY, "torrents", request=PageUrl, reply=ReleasesReply, fuse=STANDARD_S)
TORRENTS_RENAME_FILE = Topic("torrents.rename_file", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_RESUME = Topic("torrents.resume", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_SEASONS = Topic("torrents.seasons", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_SET_LOCATION = Topic("torrents.set_location", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_STAGING_CHANGED = Topic("torrents.staging.changed", EVENT, "torrents", request=StagingFolder)
TORRENTS_STAGING_GET = Topic("torrents.staging.get", QUERY, "torrents", reply=StagingFolder, fuse=STANDARD_S)
TORRENTS_STAGING_SET = Topic(
    "torrents.staging.set", COMMAND, "torrents", request=StagingSet, reply=StagingSaved, fuse=STANDARD_S)

# --- trackerauth -------------------------------------------------------------------------------------------------
TRACKERAUTH_CREDENTIALS_CHANGED = Topic("trackerauth.credentials.changed", EVENT, "trackerauth", request=CredentialsChanged)
# The credentials of a service without its password: whether one is set, never the password.
TRACKERAUTH_CREDENTIALS_GET = Topic(
    "trackerauth.credentials.get", QUERY, "trackerauth", request=ServiceRef, reply=CredentialsReply, fuse=QUICK_S)
TRACKERAUTH_CREDENTIALS_SET = Topic(
    "trackerauth.credentials.set", COMMAND, "trackerauth", request=CredentialsSet, reply=CredentialsSaved, fuse=QUICK_S)
# The fetch of a page of a service with credentials; it covers an anti-bot challenge solved by a browser (up to 45 s).
TRACKERAUTH_FETCH = Topic("trackerauth.fetch", COMMAND, "trackerauth", request=FetchRequest, reply=FetchReply, fuse=150.0)
# The password of a service, to the eye of its field in the settings; masked in every dump of the bus (core/secrets.py).
TRACKERAUTH_SECRET_GET = Topic(
    "trackerauth.secret.get", QUERY, "trackerauth", request=ServiceRef, reply=SecretReply, fuse=QUICK_S)

# --- unpack ------------------------------------------------------------------------------------------------------
UNPACK_ASSIGN = Topic("unpack.assign", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_AUTO = Topic("unpack.auto", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_EDITOR_CHANGED = Topic("unpack.editor.changed", EVENT, "unpack", request=PageChanged)
# The editor of a page: its releases, then the profile of each of the five tabs applied, one after another. It changes nothing;
# the profiles of the tabs are made by `unpack.editor.open`.
UNPACK_EDITOR_GET = Topic(
    "unpack.editor.get", QUERY, "unpack",
    fuse=after(QUICK_S, SOURCES_SITE_RESOLVE, TORRENTS_RELEASES_GET, *each(5, RULES_APPLY)))
# The window opens the editor of a page with it: the missing profiles of the five tabs are made (the first opening of the page).
UNPACK_EDITOR_OPEN = Topic(
    "unpack.editor.open", COMMAND, "unpack",
    fuse=after(QUICK_S, SOURCES_SITE_RESOLVE, *each(5, RULES_PROFILES_CREATE)))
UNPACK_FOLDER_DELETE = Topic("unpack.folder.delete", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_FOLDER_SET = Topic("unpack.folder.set", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_READ_PROGRESS = Topic("unpack.read.progress", EVENT, "unpack", request=ReleaseReadProgress)
UNPACK_REMOVE = Topic("unpack.remove", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_TABLE_CHANGED = Topic("unpack.table.changed", EVENT, "unpack", request=PageChanged)
# The layout of a tracker's page: its releases, then the profile of each of the five tabs applied, one after another.
UNPACK_TABLE_GET = Topic(
    "unpack.table.get", QUERY, "unpack",
    fuse=after(QUICK_S, SOURCES_SITE_RESOLVE, TORRENTS_RELEASES_GET, *each(5, RULES_APPLY)))
UNPACK_UNASSIGN = Topic("unpack.unassign", COMMAND, "unpack", fuse=STANDARD_S)


_RECORDS = tuple(record for record in globals().values() if isinstance(record, Topic))
TOPICS: Mapping[str, Topic] = MappingProxyType({record.name: record for record in _RECORDS})
if len(TOPICS) != len(_RECORDS):
    raise RuntimeError("a topic is registered twice")


def deadline_of(topic: Topic | str, units: int = 1) -> float | None:
    """The fuse of a request for `topic`: the record's number times `units` for a topic whose work is one step for each
    unit (a source of a series), `None` for a topic that has none of its own, the standard fuse for one not listed."""
    known = topic if isinstance(topic, Topic) else TOPICS.get(topic)
    if known is None:
        return STANDARD_S * max(1, units)
    return None if known.fuse is None else known.fuse * max(1, units)
