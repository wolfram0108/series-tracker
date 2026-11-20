"""The registry of the topics of the bus: everything known about a topic, written once.

A topic is a record, `Topic`: its name, its kind, the module that owns it, the models of what is sent to it and of the
answer, and the fuse of a request for it. The bus knows none of it and only delivers; `BaseModule` reads the record: a
request goes as an envelope of the kind of its topic and waits under its fuse. Each record is a constant named after its
topic (`CATALOG_SERIES_GET` for `catalog.series.get`); `TOPICS` finds a record by the name.

Kind. `query` asks and changes nothing; `command` changes the state of its owner or of the world outside (a file, the
torrent client, a service) and may be answered; `event` tells a fact that has happened, and nobody answers it. A topic
that both reads and changes is a query when its change is a cache, a counter or a view counted on demand: the owner keeps
what it needs and every reader gets the same answer. A read that also acts at the word of the caller (`refresh`) is a
query here until its owner splits it into a query and a command; the notes of such records say so.

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
    AlignmentJob, CompositionChanged, CompositionFailed, CompositionGetRequest, CompositionPreviewReply,
    CompositionPreviewRequest, CompositionReply, ConformParams, ConformSettingsDraft, ConformSettingsSaved,
    ConformSettingsView, GroupsResolveReply, GroupsResolveRequest, OutputSwitch, PlanDelete, PlanForget, PlannerView,
    PlayersListReply, PlayersListRequest, ProviderSaved, ProviderSettingsDraft, ProviderView, QueuesGet, QueuesReply,
    ReverseReply, ReverseSubmit, RulesApplyReply, RulesApplyRequest, RulesProfileChanged, RunChanged,
    SeriesReadProgress, SettingsReset, SettingsSet, SettingsSetMany, SiteResolveReply, SiteResolveRequest,
    SourceEvent, SourcesSettingsView, SubmitReply, TableFailed, TableGetReply, TableGetRequest, TableProgress,
    TableReady, TitleSearchReply, TitleSearchRequest, VoiceOrderSet, VoiceSettingsDraft, VoiceSettingsSaved,
    VoiceSettingsView, VoiceTitleSet, WorkState, WorkWithdrawn,
)
from .contracts.provider import ProviderState
from .contracts.torrent import PageReleases, ReleaseView

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
AUTH_EXISTS = Topic("auth.exists", QUERY, "auth", fuse=QUICK_S)
# A password found right also renews an outdated hash of it, in the table of the module itself.
AUTH_LOGIN = Topic("auth.login", QUERY, "auth", fuse=QUICK_S)
AUTH_PASSWORD_SET = Topic("auth.password.set", COMMAND, "auth", fuse=QUICK_S)

# --- catalog -----------------------------------------------------------------------------------------------------
CATALOG_SERIES_CREATE = Topic("catalog.series.create", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_DELETE = Topic("catalog.series.delete", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_GET = Topic("catalog.series.get", QUERY, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_LIST = Topic("catalog.series.list", QUERY, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_SET_SAVE_PATH = Topic("catalog.series.set_save_path", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_TOUCH_SCAN_TIME = Topic("catalog.series.touch_scan_time", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SERIES_UPDATE = Topic("catalog.series.update", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_SOURCES_ADD = Topic("catalog.sources.add", COMMAND, "catalog", fuse=QUICK_S)
CATALOG_SOURCES_BY_PROFILE = Topic("catalog.sources.by_profile", QUERY, "catalog", fuse=STANDARD_S)
CATALOG_SOURCES_LIST = Topic("catalog.sources.list", QUERY, "catalog", fuse=QUICK_S)
CATALOG_SOURCES_REMOVE = Topic("catalog.sources.remove", COMMAND, "catalog", fuse=QUICK_S)
CATALOG_SOURCES_UPDATE = Topic("catalog.sources.update", COMMAND, "catalog", fuse=QUICK_S)
CATALOG_VIEWING_START = Topic("catalog.viewing.start", COMMAND, "catalog", fuse=STANDARD_S)
CATALOG_VIEWING_STOP = Topic("catalog.viewing.stop", COMMAND, "catalog", fuse=STANDARD_S)
SERIES_ADDED = Topic("series.added", EVENT, "catalog")
SERIES_BUSY_CHANGED = Topic("series.busy.changed", EVENT, "catalog")
# Protocol of the contributions to the status of a series: published by library and scan, read by catalog.
SERIES_BUSY_CONTRIBUTION = Topic("series.busy.contribution", EVENT, "catalog")
SERIES_DELETED = Topic("series.deleted", EVENT, "catalog")
# The report of a module to the catalog after series.deleted; published by every module that holds data of a series.
SERIES_PURGED = Topic("series.purged", EVENT, "catalog")
SERIES_SOURCE_ADDED = Topic("series.source.added", EVENT, "catalog", request=SourceEvent)
SERIES_SOURCE_REMOVED = Topic("series.source.removed", EVENT, "catalog", request=SourceEvent)
SERIES_SOURCE_UPDATED = Topic("series.source.updated", EVENT, "catalog", request=SourceEvent)
SERIES_STATUS_CHANGED = Topic("series.status.changed", EVENT, "catalog")
# Protocol of the contributions to the status of a series: published by torrents, scan and planner.
SERIES_STATUS_CONTRIBUTION = Topic("series.status.contribution", EVENT, "catalog")
SERIES_UPDATED = Topic("series.updated", EVENT, "catalog")

# --- rules -------------------------------------------------------------------------------------------------------
RULES_APPLY = Topic("rules.apply", QUERY, "rules", request=RulesApplyRequest, reply=RulesApplyReply, fuse=RULES_S)
RULES_FORMAT_FILENAME = Topic("rules.format_filename", QUERY, "rules", fuse=STANDARD_S)
RULES_FORMAT_TORRENT_FILE = Topic("rules.format_torrent_file", QUERY, "rules", fuse=SERVICE_S)
RULES_PROFILE_CHANGED = Topic("rules.profile.changed", EVENT, "rules", request=RulesProfileChanged)
RULES_PROFILES_CREATE = Topic("rules.profiles.create", COMMAND, "rules", fuse=QUICK_S)
RULES_PROFILES_DELETE = Topic(
    "rules.profiles.delete", COMMAND, "rules",
    fuse=after(QUICK_S, CATALOG_SOURCES_BY_PROFILE, CATALOG_SOURCES_BY_PROFILE))
RULES_PROFILES_EXPORT = Topic("rules.profiles.export", QUERY, "rules", fuse=QUICK_S)
RULES_PROFILES_LIST = Topic("rules.profiles.list", QUERY, "rules", fuse=QUICK_S)
RULES_PROFILES_UPDATE = Topic("rules.profiles.update", COMMAND, "rules", fuse=QUICK_S)
RULES_RULES_ADD = Topic("rules.rules.add", COMMAND, "rules", fuse=QUICK_S)
RULES_RULES_DELETE = Topic("rules.rules.delete", COMMAND, "rules", fuse=QUICK_S)
RULES_RULES_LIST = Topic("rules.rules.list", QUERY, "rules", fuse=QUICK_S)
RULES_RULES_REORDER = Topic("rules.rules.reorder", COMMAND, "rules", fuse=QUICK_S)
RULES_RULES_UPDATE = Topic("rules.rules.update", COMMAND, "rules", fuse=QUICK_S)

# --- settings ----------------------------------------------------------------------------------------------------
SETTINGS_CHANGED = Topic("settings.changed", EVENT, "settings")
SETTINGS_PATHS_ADD = Topic("settings.paths.add", COMMAND, "settings", fuse=QUICK_S)
SETTINGS_PATHS_LIST = Topic("settings.paths.list", QUERY, "settings", fuse=QUICK_S)
SETTINGS_PATHS_REMOVE = Topic("settings.paths.remove", COMMAND, "settings", fuse=QUICK_S)
SETTINGS_VALUE_GET = Topic("settings.value.get", QUERY, "settings", fuse=STANDARD_S)
SETTINGS_VALUE_SET = Topic("settings.value.set", COMMAND, "settings", fuse=QUICK_S)
SETTINGS_VALUES_BY_PREFIX = Topic("settings.values.by_prefix", QUERY, "settings", fuse=QUICK_S)
SETTINGS_VALUES_SET_MANY = Topic("settings.values.set_many", COMMAND, "settings", fuse=QUICK_S)

# --- conform -----------------------------------------------------------------------------------------------------
CONFORM_CANCEL = Topic("conform.cancel", COMMAND, "conform", fuse=STANDARD_S)
CONFORM_DAEMON_UP = Topic("conform.daemon.up", EVENT, "conform")
CONFORM_JOB_FIND = Topic("conform.job.find", QUERY, "conform", fuse=STANDARD_S)
CONFORM_JOB_FINISHED = Topic("conform.job.finished", EVENT, "conform", request=AlignmentJob)
CONFORM_JOB_GET = Topic(
    "conform.job.get", QUERY, "conform", reply=AlignmentJob,
    fuse=after(STANDARD_S, SETTINGS_VALUE_GET, SETTINGS_VALUE_GET))
CONFORM_JOB_KEYS = Topic("conform.job.keys", QUERY, "conform", fuse=STANDARD_S)
CONFORM_JOB_REMOVED = Topic("conform.job.removed", EVENT, "conform")
CONFORM_PARAMS_SET = Topic(
    "conform.params.set", COMMAND, "conform", request=ConformParams, reply=ConformSettingsSaved,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUES_BY_PREFIX))
CONFORM_PLOT = Topic("conform.plot", QUERY, "conform", fuse=STANDARD_S)
CONFORM_QUEUE_CHANGED = Topic("conform.queue.changed", EVENT, "conform")
CONFORM_REVERSE = Topic("conform.reverse", COMMAND, "conform", request=ReverseSubmit, reply=ReverseReply, fuse=STANDARD_S)
CONFORM_SETTINGS_CHANGED = Topic("conform.settings.changed", EVENT, "conform", request=ConformSettingsView)
CONFORM_SETTINGS_GET = Topic(
    "conform.settings.get", QUERY, "conform", reply=ConformSettingsView,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX))
CONFORM_SETTINGS_SET = Topic(
    "conform.settings.set", COMMAND, "conform", request=ConformSettingsDraft, reply=ConformSettingsSaved,
    fuse=after(QUICK_S, SETTINGS_VALUES_BY_PREFIX, SETTINGS_VALUES_SET_MANY, SETTINGS_VALUES_BY_PREFIX))
CONFORM_SUBMIT = Topic("conform.submit", COMMAND, "conform", reply=SubmitReply, fuse=SERVICE_S)
CONFORM_WORKSPACE = Topic("conform.workspace", QUERY, "conform", fuse=after(QUICK_S, SETTINGS_VALUE_GET))

# --- sources -----------------------------------------------------------------------------------------------------
SOURCES_PAGES_CHECK = Topic("sources.pages.check", QUERY, "sources", fuse=STANDARD_S)
SOURCES_PAGES_FORGOTTEN = Topic("sources.pages.forgotten", EVENT, "sources")
# The network is read, the table is not written.
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
SOURCES_PROVIDER_MANIFEST = Topic("sources.provider.manifest", EVENT, "sources")
SOURCES_PROVIDER_SET = Topic(
    "sources.provider.set", COMMAND, "sources", request=ProviderSettingsDraft, reply=ProviderSaved,
    fuse=after(SERVICE_S, CATALOG_SERIES_LIST, CONFORM_WORKSPACE, SETTINGS_VALUE_GET, SETTINGS_VALUES_SET_MANY))
SOURCES_PROVIDER_STATE = Topic("sources.provider.state", EVENT, "sources", request=ProviderState)
SOURCES_RELEASES_GET = Topic("sources.releases.get", QUERY, "sources", reply=PageReleases, fuse=RULES_S)
SOURCES_SETTINGS_CHANGED = Topic("sources.settings.changed", EVENT, "sources", request=SourcesSettingsView)
SOURCES_SETTINGS_GET = Topic("sources.settings.get", QUERY, "sources", reply=SourcesSettingsView, fuse=QUICK_S)
SOURCES_SITE_RESOLVE = Topic(
    "sources.site.resolve", QUERY, "sources", request=SiteResolveRequest, reply=SiteResolveReply,
    fuse=QUICK_S)
SOURCES_TABLE_CANCEL = Topic("sources.table.cancel", COMMAND, "sources", fuse=STANDARD_S)
SOURCES_TABLE_FAILED = Topic("sources.table.failed", EVENT, "sources", request=TableFailed)
SOURCES_TABLE_FRESH = Topic("sources.table.fresh", EVENT, "sources", request=TableReady)
# Mixed: with `refresh` it also begins a read of the page; split into a query and a command in the pass of sources.
SOURCES_TABLE_GET = Topic(
    "sources.table.get", QUERY, "sources", request=TableGetRequest, reply=TableGetReply,
    fuse=SERVICE_S)
SOURCES_TABLE_PROGRESS = Topic("sources.table.progress", EVENT, "sources", request=TableProgress)
SOURCES_TABLE_READ = Topic("sources.table.read", COMMAND, "sources", request=TableGetRequest, fuse=STANDARD_S)
SOURCES_TABLE_READY = Topic("sources.table.ready", EVENT, "sources", request=TableReady)
# What the read of a page does, for the dump of a run (kinds `begin` and `end`).
SOURCES_TABLE_TRACE = Topic("sources.table.trace", EVENT, "sources")
SOURCES_TABLES_FRESH = Topic("sources.tables.fresh", QUERY, "sources", fuse=STANDARD_S)
SOURCES_TERMS_GET = Topic("sources.terms.get", QUERY, "sources", fuse=STANDARD_S)
SOURCES_TERMS_SET = Topic("sources.terms.set", COMMAND, "sources", fuse=STANDARD_S)
# The search of the titles: the sources of the provider asked (a session opened, two requests, again after a refusal)
# and a page of the search.
SOURCES_TITLES_SEARCH = Topic(
    "sources.titles.search", QUERY, "sources", request=TitleSearchRequest, reply=TitleSearchReply,
    fuse=HEAVY_S)
SOURCES_TORRENT_FILE_DROP = Topic("sources.torrent_file.drop", COMMAND, "sources", fuse=STANDARD_S)
# A miss fetches the file and writes the cache of the module.
SOURCES_TORRENT_FILE_GET = Topic("sources.torrent_file.get", QUERY, "sources", fuse=FETCH_S)
SOURCES_TRACKER_SET_MIRRORS = Topic("sources.tracker.set_mirrors", COMMAND, "sources", fuse=QUICK_S)
SOURCES_TRACKERS_LIST = Topic("sources.trackers.list", QUERY, "sources", fuse=QUICK_S)
# A file a player downloads itself is as long as its episode: the asker watches its progress events under its fuse of
# silence.
SOURCES_VIDEO_FETCH = Topic("sources.video.fetch", COMMAND, "sources", fuse=None)
SOURCES_VIDEO_FETCH_STOP = Topic("sources.video.fetch.stop", COMMAND, "sources", fuse=QUICK_S)
SOURCES_VIDEO_PROGRESS = Topic("sources.video.progress", EVENT, "sources")
# A link of Vibix also writes the manifest file and the key.
SOURCES_VIDEO_STREAM = Topic("sources.video.stream", QUERY, "sources", fuse=HEAVY_S)

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
# Mixed as sources.table.get (`refresh`); with `lines` a pure query. A preview reads the table, applies the profile to
# its lines and asks for the groups of the names, one after another.
COMPOSITION_PREVIEW = Topic(
    "composition.preview", QUERY, "composition", request=CompositionPreviewRequest, reply=CompositionPreviewReply,
    fuse=after(QUICK_S, SOURCES_TABLE_GET, RULES_APPLY, VOICES_GROUPS_RESOLVE, VOICES_SETTINGS_GET))
COMPOSITION_QUEUES_GET = Topic(
    "composition.queues.get", QUERY, "composition", request=QueuesGet, reply=QueuesReply,
    fuse=after(QUICK_S, VOICES_SETTINGS_GET))
# A recount asks the rules once for each source of the series: the fuse of the table is for one source.
COMPOSITION_RECOMPUTE = Topic("composition.recompute", COMMAND, "composition", fuse=RULES_S)
COMPOSITION_SERIES_READING = Topic("composition.series.reading", EVENT, "composition", request=SeriesReadProgress)
COMPOSITION_SETTINGS_RESET = Topic(
    "composition.settings.reset", COMMAND, "composition", request=SettingsReset,
    fuse=STANDARD_S)
# A lever is checked against the series and the groups of the voice-overs before it is written.
COMPOSITION_SETTINGS_SET = Topic(
    "composition.settings.set", COMMAND, "composition", request=SettingsSet,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
COMPOSITION_SETTINGS_SET_MANY = Topic(
    "composition.settings.set_many", COMMAND, "composition", request=SettingsSetMany,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
COMPOSITION_VOICE_ORDER_SET = Topic(
    "composition.voice_order.set", COMMAND, "composition", request=VoiceOrderSet,
    fuse=STANDARD_S)
COMPOSITION_VOICE_TITLE_SET = Topic(
    "composition.voice_title.set", COMMAND, "composition", request=VoiceTitleSet,
    fuse=STANDARD_S)

# --- downloads ---------------------------------------------------------------------------------------------------
DOWNLOADS_CANCEL = Topic("downloads.cancel", COMMAND, "downloads", fuse=STANDARD_S)
DOWNLOADS_CARD_REMOVE = Topic("downloads.card.remove", COMMAND, "downloads", fuse=STANDARD_S)
DOWNLOADS_FETCH = Topic("downloads.fetch", COMMAND, "downloads", fuse=STANDARD_S)
DOWNLOADS_FETCH_FINISHED = Topic("downloads.fetch.finished", EVENT, "downloads")
DOWNLOADS_FETCH_STARTED = Topic("downloads.fetch.started", EVENT, "downloads")
DOWNLOADS_LIMITS_GET = Topic("downloads.limits.get", QUERY, "downloads", fuse=STANDARD_S)
DOWNLOADS_LIMITS_SET = Topic("downloads.limits.set", COMMAND, "downloads", fuse=STANDARD_S)
DOWNLOADS_QUEUE_CHANGED = Topic("downloads.queue.changed", EVENT, "downloads")
DOWNLOADS_QUEUE_CLEAR = Topic("downloads.queue.clear", COMMAND, "downloads", fuse=STANDARD_S)
DOWNLOADS_QUEUE_GET = Topic("downloads.queue.get", QUERY, "downloads", fuse=STANDARD_S)
DOWNLOADS_WITHDRAW = Topic("downloads.withdraw", COMMAND, "downloads", fuse=STANDARD_S)

# --- gateway -----------------------------------------------------------------------------------------------------
# The count of the connected windows (protocol: gateway publishes, others read).
GATEWAY_SSE_CLIENTS = Topic("gateway.sse.clients", EVENT, "gateway")

# --- library -----------------------------------------------------------------------------------------------------
LIBRARY_DIRECTORIES_LIST = Topic("library.directories.list", QUERY, "library", fuse=STANDARD_S)
# One move for each file, each with its own (core.storage.MOVE_TIMEOUT_S).
LIBRARY_RELOCATION_APPLY = Topic("library.relocation.apply", COMMAND, "library", fuse=None)
LIBRARY_RELOCATION_FINISHED = Topic("library.relocation.finished", EVENT, "library")
LIBRARY_RELOCATION_GET = Topic("library.relocation.get", QUERY, "library", fuse=STANDARD_S)
LIBRARY_RELOCATION_PLAN = Topic("library.relocation.plan", COMMAND, "library", fuse=STANDARD_S)
LIBRARY_RELOCATION_PLANNED = Topic("library.relocation.planned", EVENT, "library")
LIBRARY_RELOCATION_STARTED = Topic("library.relocation.started", EVENT, "library")

# --- metadata ----------------------------------------------------------------------------------------------------
METADATA_DETAILS = Topic("metadata.details", QUERY, "metadata", fuse=STANDARD_S)
METADATA_MAP_GET = Topic("metadata.map.get", QUERY, "metadata", fuse=QUICK_S)
METADATA_MAP_LIST = Topic("metadata.map.list", QUERY, "metadata", fuse=QUICK_S)
METADATA_MAP_SET = Topic("metadata.map.set", COMMAND, "metadata", fuse=QUICK_S)
METADATA_SEARCH = Topic("metadata.search", QUERY, "metadata", fuse=STANDARD_S)
METADATA_SEASONS_RECOMPUTE = Topic("metadata.seasons.recompute", COMMAND, "metadata", fuse=STANDARD_S)

# --- planner -----------------------------------------------------------------------------------------------------
# A deletion and a forgetting are written once the plan of the series is counted again.
PLANNER_DELETE = Topic(
    "planner.delete", COMMAND, "planner", request=PlanDelete,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
PLANNER_DELETE_CANCEL = Topic(
    "planner.delete.cancel", COMMAND, "planner", request=PlanDelete,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
PLANNER_DOWNLOADED_COUNTS = Topic("planner.downloaded_counts", QUERY, "planner", fuse=QUICK_S)
PLANNER_FAILURES_FORGET = Topic(
    "planner.failures.forget", COMMAND, "planner", request=PlanForget,
    fuse=after(QUICK_S, CATALOG_SERIES_GET, VOICES_SETTINGS_GET, CATALOG_SERIES_GET, VOICES_SETTINGS_GET))
# The files of a series also look for a journal in its folder.
PLANNER_FILES_GET = Topic("planner.files.get", QUERY, "planner", fuse=after(QUICK_S + STANDARD_S, CATALOG_SERIES_GET))
PLANNER_JOURNAL_CHANGED = Topic("planner.journal.changed", EVENT, "planner")
PLANNER_PACK_CHANGED = Topic("planner.pack.changed", EVENT, "planner")
PLANNER_PACK_GET = Topic("planner.pack.get", QUERY, "planner", fuse=STANDARD_S)
PLANNER_PACK_REMOVE = Topic("planner.pack.remove", COMMAND, "planner", fuse=STANDARD_S)
# As planner.series.get. The references are counted from the layout as the view is: the layout, the settings of the
# voice-overs, the names. The answer is a tuple of `Reference` (a row of models has no model of its own).
PLANNER_REFERENCES_GET = Topic(
    "planner.references.get", QUERY, "planner",
    fuse=after(STANDARD_S, CATALOG_SERIES_GET, COMPOSITION_GET, VOICES_SETTINGS_GET))
PLANNER_SEASONS = Topic("planner.seasons", QUERY, "planner", fuse=QUICK_S)
PLANNER_SERIES_CHANGED = Topic("planner.series.changed", EVENT, "planner", request=PlannerView)
# A projection counted on demand (and published) when it is not there yet: a query. The view of the plan reads the table
# of the series. Answered as the model itself (immutable): the gateway writes it for the window.
PLANNER_SERIES_GET = Topic(
    "planner.series.get", QUERY, "planner", reply=PlannerView,
    fuse=after(STANDARD_S, CATALOG_SERIES_GET, COMPOSITION_GET, VOICES_SETTINGS_GET))
# The sync of the plan of a series is written down and started: its work is told by work.series.state.
PLANNER_SYNC = Topic("planner.sync", COMMAND, "planner", fuse=QUICK_S)
PLANNER_SYNC_CANCEL = Topic("planner.sync.cancel", COMMAND, "planner", fuse=STANDARD_S)
PLANNER_SYNC_TRACE = Topic("planner.sync.trace", EVENT, "planner")
# Takes the next number of the state (the order of the states): a read.
PLANNER_WORK_GET = Topic("planner.work.get", QUERY, "planner", reply=WorkState, fuse=QUICK_S)
# Published by planner and by the torrent pipeline; the meaning is the plan's.
SERIES_DOWNLOADED_CHANGED = Topic("series.downloaded.changed", EVENT, "planner")
# The owner of an object tells that its work is not needed (WORK_WITHDRAWN of core.contracts.work).
WORK_WITHDRAWN = Topic("work.withdrawn", EVENT, "planner", request=WorkWithdrawn)

# --- renaming ----------------------------------------------------------------------------------------------------
RENAMING_PROCESS_TORRENT = Topic("renaming.process_torrent", COMMAND, "renaming", fuse=RULES_S)

# --- run ---------------------------------------------------------------------------------------------------------
# Told by run.py when every module is up; the owner is the assembly of the process, not a module.
SYSTEM_STARTED = Topic("system.started", EVENT, "run")

# --- scan --------------------------------------------------------------------------------------------------------
SCAN_ALL_START = Topic("scan.all.start", COMMAND, "scan", fuse=STANDARD_S)
SCAN_EDIT_BEGIN = Topic("scan.edit.begin", COMMAND, "scan", fuse=after(QUICK_S, CATALOG_SERIES_GET))
SCAN_EDIT_END = Topic("scan.edit.end", COMMAND, "scan", fuse=QUICK_S)
SCAN_OUTPUT_SWITCH = Topic(
    "scan.output.switch", COMMAND, "scan", request=OutputSwitch,
    fuse=after(STANDARD_S, *each(4, CATALOG_SERIES_GET), CATALOG_SERIES_UPDATE, DOWNLOADS_QUEUE_GET, COMPOSITION_GET,
               VOICES_SETTINGS_GET))
# A stop of a sync: the run of the series, the work of its plan and the reads of its pages, one after another.
SCAN_RUN_CANCEL = Topic(
    "scan.run.cancel", COMMAND, "scan",
    fuse=after(QUICK_S, CATALOG_SERIES_GET, PLANNER_SYNC_CANCEL, SOURCES_TABLE_CANCEL))
# The run of a sync of a series (RUN_CHANGED of core.contracts.work).
SCAN_RUN_CHANGED = Topic("scan.run.changed", EVENT, "scan", request=RunChanged)
SCAN_RUNS_GET = Topic("scan.runs.get", QUERY, "scan", fuse=QUICK_S)
SCAN_SCHEDULE_SET = Topic("scan.schedule.set", COMMAND, "scan", fuse=STANDARD_S)
# No fuse of its own: a sync is as long as the steps it is made of, each of which has one.
SCAN_SERIES_RUN = Topic("scan.series.run", COMMAND, "scan", fuse=None)
SCAN_STATUS_CHANGED = Topic("scan.status.changed", EVENT, "scan")
SCAN_STATUS_GET = Topic("scan.status.get", QUERY, "scan", fuse=after(QUICK_S, *each(3, SETTINGS_VALUE_GET)))
# What an executor tells the run about its work of a series (WORK_STATE of core.contracts.work); the run is scan's.
WORK_SERIES_STATE = Topic("work.series.state", EVENT, "scan", request=WorkState)

# --- torrents ----------------------------------------------------------------------------------------------------
TORRENTS_ADD = Topic("torrents.add", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_COMPOSITION = Topic("torrents.composition", QUERY, "torrents", fuse=900.0)
TORRENTS_DB_ACTIVE = Topic("torrents.db.active", QUERY, "torrents", fuse=SERVICE_S)
TORRENTS_DB_ADD = Topic("torrents.db.add", COMMAND, "torrents", fuse=QUICK_S)
TORRENTS_DB_DEACTIVATE_ALL = Topic("torrents.db.deactivate_all", COMMAND, "torrents", fuse=HEAVY_S)
TORRENTS_DB_DOWNLOADED_COUNTS = Topic("torrents.db.downloaded_counts", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_DB_FILES_FOR_SERIES = Topic("torrents.db.files.for_series", QUERY, "torrents", fuse=QUICK_S)
TORRENTS_DB_FILES_LIST = Topic("torrents.db.files.list", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_DB_FILES_UPSERT = Topic("torrents.db.files.upsert", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_DB_HISTORY = Topic("torrents.db.history", QUERY, "torrents", fuse=QUICK_S)
# The monitor names the series of the old pipeline and the series whose torrent source is the page of a release.
TORRENTS_DB_PROGRESS_LIST = Topic(
    "torrents.db.progress.list", QUERY, "torrents",
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
TORRENTS_PROGRESS_CHANGED = Topic("torrents.progress.changed", EVENT, "torrents")
TORRENTS_QUEUE_CHANGED = Topic("torrents.queue.changed", EVENT, "torrents")
TORRENTS_QUEUE_GET = Topic("torrents.queue.get", QUERY, "torrents", fuse=QUICK_S)
TORRENTS_RECHECK = Topic("torrents.recheck", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_REGISTER = Topic("torrents.register", COMMAND, "torrents", fuse=BATCH_S)
TORRENTS_RELEASE_CHANGED = Topic("torrents.release.changed", EVENT, "torrents", request=ReleaseView)
# The releases of a tracker's page: the page read on the tracker, a new version's file fetched, the client driven.
TORRENTS_RELEASE_CHECK = Topic(
    "torrents.release.check", COMMAND, "torrents",
    fuse=after(HEAVY_S, SOURCES_SITE_RESOLVE, SOURCES_RELEASES_GET, SOURCES_TORRENT_FILE_GET))
TORRENTS_RELEASE_DOWNLOAD = Topic(
    "torrents.release.download", COMMAND, "torrents", reply=ReleaseView,
    fuse=after(HEAVY_S, SOURCES_SITE_RESOLVE, SOURCES_RELEASES_GET, SOURCES_TORRENT_FILE_GET))
TORRENTS_RELEASE_DROP_FILES = Topic("torrents.release.drop_files", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_RELEASE_WATCH = Topic("torrents.release.watch", COMMAND, "torrents", reply=ReleaseView, fuse=STANDARD_S)
TORRENTS_RELEASES_GET = Topic("torrents.releases.get", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_RENAME_FILE = Topic("torrents.rename_file", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_RESUME = Topic("torrents.resume", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_SEASONS = Topic("torrents.seasons", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_SET_LOCATION = Topic("torrents.set_location", COMMAND, "torrents", fuse=STANDARD_S)
TORRENTS_STAGING_CHANGED = Topic("torrents.staging.changed", EVENT, "torrents")
TORRENTS_STAGING_GET = Topic("torrents.staging.get", QUERY, "torrents", fuse=STANDARD_S)
TORRENTS_STAGING_SET = Topic("torrents.staging.set", COMMAND, "torrents", fuse=STANDARD_S)

# --- trackerauth -------------------------------------------------------------------------------------------------
TRACKERAUTH_CREDENTIALS_CHANGED = Topic("trackerauth.credentials.changed", EVENT, "trackerauth")
TRACKERAUTH_CREDENTIALS_GET = Topic("trackerauth.credentials.get", QUERY, "trackerauth", fuse=QUICK_S)
TRACKERAUTH_CREDENTIALS_SET = Topic("trackerauth.credentials.set", COMMAND, "trackerauth", fuse=QUICK_S)
# The fetch of a page of a service with credentials; it covers an anti-bot challenge solved by a browser (up to 45 s).
TRACKERAUTH_FETCH = Topic("trackerauth.fetch", COMMAND, "trackerauth", fuse=150.0)
TRACKERAUTH_SECRET_GET = Topic("trackerauth.secret.get", QUERY, "trackerauth", fuse=QUICK_S)

# --- unpack ------------------------------------------------------------------------------------------------------
UNPACK_ASSIGN = Topic("unpack.assign", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_AUTO = Topic("unpack.auto", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_EDITOR_CHANGED = Topic("unpack.editor.changed", EVENT, "unpack")
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
UNPACK_READ_PROGRESS = Topic("unpack.read.progress", EVENT, "unpack")
UNPACK_REMOVE = Topic("unpack.remove", COMMAND, "unpack", fuse=STANDARD_S)
UNPACK_TABLE_CHANGED = Topic("unpack.table.changed", EVENT, "unpack")
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
