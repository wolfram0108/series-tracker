"""Module torrents: the only interlocutor of qBittorrent (Р-1), the releases of the torrent sources, and the old torrent pipeline.

The client is made at the start (the credentials are read over the bus: the address and the login from `trackerauth.credentials.get`,
the password from `trackerauth.secret.get`, the one answer that carries it; env wins when it has an address, Р-22) and makes its first
login. Until that is done a request that needs the client waits for it (its fuse is the topic's) instead of answering «not
configured»; every way to the client goes through that wait (`_known`, `_client`).

The releases of the torrent sources (stage 15, modules/torrents/releases.py; tables torrent_releases, torrent_release_files,
torrent_release_versions), keyed by the page and the id of the release on it, never by a series. The answers are the models of
core/contracts/torrent.py, handed over as they are; the count (the folder, the view, the word of the client, whether a page lists a
newer version) is pure and lives in calc/torrents/releases.py:
  torrents.releases.get PageUrl -> ReleasesReply — the releases kept for the page
  torrents.release.download ReleaseRef -> ReleaseView — «Скачать»: the release as the page lists it now (sources.releases.get) goes to
      the client started, category st-source, into <torrents.staging_dir>/<site>-<key>; refused (NotAllowed) without the staging
      folder, for a release the page does not list or that has no .torrent
  torrents.release.check PageUrl -> ReleasesChecked — the page is read now: a watched release it lists in another version (another
      infohash, else another date) begins its update — the old version leaves the client without its files, the new one comes started
      into the same folder, the client checks the files there and downloads the rest; while a sync of a series with the page runs
      (scan.run.changed) the update waits for its end. The check sets `checked_at` of every watched release of the page. The pages of
      the series in the automatic mode are also checked at the times of the schedule (core/schedule.py) while the automatic sync is
      on, and a time missed while the service was down at the start; «Перечитать» of the source checks its page whatever the mode
      (trackers.py)
  torrents.release.watch ReleaseWatch -> ReleaseView — the automatic update on or off
  torrents.release.drop_files ReleaseRef (command) — a release no longer watched gives up its files (archived)
  torrents.staging.get -> StagingFolder and torrents.staging.set StagingSet -> StagingSaved — the folder the releases download into
      (the setting torrents.staging_dir); a value that is no absolute path is refused (`staging_dir_not_absolute`); the releases move
      there on settings.changed; the event torrents.staging.changed StagingFolder tells every window the folder held now
  Event torrents.release.changed ReleaseView — state, version or files changed; `ready` once every wanted file is there (an update
  ends by removing the files of the old version the new one does not list). `client` of the view is what the client says of the
  torrent (qBittorrent's state as is, e.g. `checkingDL`): the client is asked while a release moves or is checked, and once in
  `monitor_idle` otherwise; a change of it is told as the event above. The releases of the pages of the torrent sources of a series
  give the flags of its status (checking, downloading, queued, idle, ready, error, metadata) by the same words as its own torrents
  (calc/torrents/states.py torrent_flag). A page no source lists any more (series.source.removed, series.deleted) leaves the client
  with its files and its folder. settings.changed {key: torrents.staging_dir} moves every release to the new folder (setLocation);
  {key: schedule} sets the times of the checks and tells every release again (its `fresh_until` moved); {key:
  scanner_agent_enabled} wakes the checks.

What the monitor of «Агенты» shows (the table torrent_progress, one row per infohash; the table download_tasks belongs to downloads
and holds no torrents): torrents.db.progress.list -> TorrentTasks and the event torrents.progress.changed TorrentTasks, {tasks: [...]}.
A task is a torrent of the old pipeline ({id, qb_hash, series_id, series_name, status, progress, dlspeed, eta, error_message,
created_at, updated_at}) or a release of a torrent source, whatever its state (downloading, ready, being updated, failed, archived;
a release is there until its source goes), one task per series with the page: {kind: "release", qb_hash, url, release, title,
series_id, series_name, status (the state of the release), progress (by the size of the wanted files), files_done, files_total,
error_message}. Both pictures (the queue and the progress) go again when a window connects (gateway.sse.clients).

Control of the qBittorrent client (the public contract of the module; nothing inside the system calls most of it now, the interface and
the other modules are what it waits for):
  torrents.info.get {hashes?}           -> the torrents of the client (all, when no hashes are given)
  torrents.pause / .resume / .recheck {hashes} — commands to the client
  torrents.delete {hashes, delete_files?} — remove torrents from the client (their files by the flag)
  torrents.add {save_path, paused?, magnet | content_b64} -> {hash, link_type, existed}: the infohash is counted locally (Р-2)
      and «already exists» is told apart

The old torrent pipeline (Р-14) and what belongs to it: torrents.register, torrents.db.*, torrents.preview.*, torrents.composition,
torrents.seasons, torrents.queue.*, torrents.fs.verify, torrents.drive_incomplete, the agent and the monitor, and renaming.process_torrent
of the module `renaming`. It leaves with step 11 of stage 15 of the mission voice-alignment and is not rebuilt (its topics are the
records of the registry and its answers are dictionaries until then). The stage machine pipeline.py over agent_tasks is resumable:
the tasks are restored from the base and checked against qBit. KERNEL INVARIANT — the torrent is paused until the renaming ends, see
calc/torrents/pipeline.py.
Queries/commands of the pipeline (contracts of Р-12, scan waited for them):
  torrents.db.active {series_id}        -> the active torrents, checked against qBit
  torrents.db.deactivate_all {series_id} -> for force_replace
  torrents.register {series_id, torrent, qb_hash, link_type, replaces, start_pipeline?}
      — the torrent is fixed by the IDENTITY of its infohash (qb_hash), not by the label from the tracker. The same infohash already
        in the series -> nothing is downloaded, only the label is moved (POINT 3). A new infohash -> the old torrent leaves qBit, its
        row is marked «replaced» (history), the new one goes into the pipeline (POINT 4). replaces.db_id is the exact row to
        deactivate (by id, not by the label: or the wrong one would be). start_pipeline=false prepares the content: the metadata,
        the rules' parse into torrent_files and the priorities of the files, WITHOUT a task of the pipeline. The torrent stays paused
        while the user picks the episodes; then torrents.drive_incomplete drives it on
  torrents.queue.get {series_id?}       -> {count, tasks}
  torrents.fs.verify {series_id}        -> {missing, recheck_started}
      — the files on disk are checked, and recheck tasks are made for the missing ones (scan calls it, and the window of a series on
        opening: the decision of Р-13/Р-14). A series with no file of the pipeline answers {0, 0} at once.
  torrents.preview.fetch {items: [{torrent_id, magnet?, link?}]}
      — command: the file lists of the releases of the add form, for the preview of the naming. Each release ends with the event
        torrents.preview.ready or .failed, both published by this module only; there is no reply. The releases go in order: a magnet
        gets its metadata through qBit (added STARTED in the category st-naming-preview, files read, torrent deleted; ephemeral, not
        resumed; zombie-safe: cleanup by infohash and a sweep of the category in on_start); a link gets its .torrent from sources
        (sources.torrent_file.get, with its cache) and the file list is read locally (bencode), without qBit. A release with neither
        a magnet nor a link, or whose .torrent was not got or not parsed, ends with preview.failed and the reason.
Events of the pipeline: torrents.queue.changed {count, tasks} (the contract of the SSE agent_queue_update; count=0 tells scan to
assign the next pass), series.status.contribution {source: torrents, ...} (the stages to flags by the map of Р-11, and downloading/
ready from the progress: the semantics of sync_torrent_statuses), torrents.preview.ready {torrent_id, infohash, name, files} /
torrents.preview.failed {torrent_id, infohash, reason} (-> SSE: the preview of the naming on adding).

Selective download. The memory of the exclusions lives in the series (series.ignored_seasons + series.ignored_episodes, owned by
catalog) and so outlives the replacement of a torrent; torrent_files.is_excluded is its projection on the files of the current release,
and the qBit priorities are counted from it (0 — do not download). Only what is recognized and not excluded is downloaded; the one
source of truth is calc/torrents/selection.py. The projection is rebuilt on a change of the memory (the event series.updated) and on a
change of the content (torrents.db.files.upsert).

The files are renamed inside qBittorrent by the query renaming.process_torrent (the zone of renaming); the progress is followed by an
adaptive poll of qBit (often while something moves, rarely at rest), events only on changes.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone

from calc.torrents import pipeline, selection
from calc.torrents.infohash import TorrentParseError, infohash_candidates, infohash_from_magnet, torrent_file_list
from calc.torrents.releases import release_tasks, status_flag
from calc.torrents.selection import memory_of
from calc.torrents.states import torrent_flag
from core import storage
from core.module import BaseModule, BusRequestError
from core.source_types import torrent_profile_id
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_LIST, CATALOG_SERIES_TOUCH_SCAN_TIME, GATEWAY_SSE_CLIENTS,
                         METADATA_SEASONS_RECOMPUTE, RENAMING_PROCESS_TORRENT, RULES_FORMAT_TORRENT_FILE, SCAN_RUN_CHANGED,
                         SCAN_RUNS_GET, SERIES_DELETED, SERIES_DOWNLOADED_CHANGED, SERIES_PURGED, SERIES_SOURCE_REMOVED,
                         SERIES_STATUS_CONTRIBUTION, SERIES_UPDATED, SETTINGS_CHANGED, SETTINGS_VALUE_GET, SETTINGS_VALUE_SET,
                         SOURCES_SITE_RESOLVE, SOURCES_TORRENT_FILE_DROP, SOURCES_TORRENT_FILE_GET, TORRENTS_ADD,
                         TORRENTS_COMPOSITION, TORRENTS_DB_ACTIVE, TORRENTS_DB_ADD, TORRENTS_DB_DEACTIVATE_ALL,
                         TORRENTS_DB_DOWNLOADED_COUNTS, TORRENTS_DB_FILES_FOR_SERIES, TORRENTS_DB_FILES_LIST,
                         TORRENTS_DB_FILES_UPSERT, TORRENTS_DB_HISTORY, TORRENTS_DB_PROGRESS_LIST, TORRENTS_DELETE,
                         TORRENTS_DRIVE_INCOMPLETE, TORRENTS_FILES_GET, TORRENTS_FS_VERIFY, TORRENTS_INFO_GET, TORRENTS_PAUSE,
                         TORRENTS_PREVIEW_FAILED, TORRENTS_PREVIEW_FETCH, TORRENTS_PREVIEW_READY, TORRENTS_PROGRESS_CHANGED,
                         TORRENTS_QUEUE_CHANGED, TORRENTS_QUEUE_GET, TORRENTS_RECHECK, TORRENTS_REGISTER,
                         TORRENTS_RELEASE_CHANGED, TORRENTS_RELEASE_CHECK, TORRENTS_RELEASE_DOWNLOAD,
                         TORRENTS_RELEASE_DROP_FILES, TORRENTS_RELEASE_WATCH, TORRENTS_RELEASES_GET, TORRENTS_RENAME_FILE,
                         TORRENTS_RESUME, TORRENTS_SEASONS, TORRENTS_SET_LOCATION, TORRENTS_STAGING_CHANGED,
                         TORRENTS_STAGING_GET, TORRENTS_STAGING_SET, TRACKERAUTH_CREDENTIALS_CHANGED,
                         TRACKERAUTH_CREDENTIALS_GET, TRACKERAUTH_SECRET_GET)
from core.contracts import (CredentialsChanged, PageUrl, RunChanged, SeriesDeleted, SeriesPurged, SeriesUpdated,
                            SettingValue, SourceType, StatusContribution)
from core.contracts.torrent import (STAGING_DIR_KEY, ReleaseRef, ReleasesChecked, ReleasesReply, ReleaseTask, ReleaseView,
                                    ReleaseWatch, StagingFolder, StagingSaved, StagingSet, TorrentProgress, TorrentTasks)
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason, relayed
from core.retry import UNTIMED, Budget, retrying
from core.schedule import SCHEDULE_KEY, last_at, next_at, parse

from .qbt_client import QbtClient, QbtError
from .releases import Releases
from .repository import ReleaseStore, TorrentsRepository

# qBittorrent error states: internal torrent error and unavailable/missing files (finding 43).
# The old system (and our port) counted them as downloading, so the card lied; now they are the `error` flag. Texts are for the status modal.
_ERROR_STATES = {
    "error": reason("qbt_error"),
    "missingFiles": reason("qbt_files_missing"),
}

# qBit category of transient preview torrents (metadata fetch for a magnet when a series is added).
# Startup reconcile sweeps the whole category: a backstop against orphans left by a crash between add and delete (zombie safety).
PREVIEW_CATEGORY = "st-naming-preview"


# The torrent of a preview is deleted until qBittorrent confirms it gone: the add is asynchronous and can be cancelled.
PREVIEW_CLEANUP_BUDGET = Budget(attempts=20, pauses=(0.6,), limit_s=UNTIMED)

class TorrentsModule(BaseModule):
    name = "torrents"

    def __init__(self, bus, db: Database, *, qbt_url: str = "",
                 qbt_username: str = "", qbt_password: str = "",
                 qbt: QbtClient | None = None,
                 pipeline_poll: float = 1.5,
                 monitor_active: float = 5.0,
                 monitor_idle: float = 60.0) -> None:
        self.repo = TorrentsRepository(db)
        self._qbt_config = dict(base_url=qbt_url, username=qbt_username,
                                password=qbt_password)
        self.qbt: QbtClient | None = qbt
        self._qbt_injected = qbt is not None
        # Set when the client of qBittorrent is made and its first login tried (or it was given): until then there is no client
        # to ask, and a request that needs it waits for it instead of answering that none is configured.
        self._looked = asyncio.Event()
        if self._qbt_injected:
            self._looked.set()
        self._pipe: dict[str, dict] = {}  # hash -> pipeline task
        self._wake = asyncio.Event()
        self._flags_cache: dict[int, dict] = {}
        self._names_cache: dict[int, str] = {}  # series names for the progress
        # Naming preview on add: file-list cache by infohash, single-flight (one fetch per infohash), cache of qBit's default path.
        self._preview_cache: dict[str, dict] = {}
        self._preview_inflight: set[str] = set()
        self._preview_tasks: set[asyncio.Task] = set()
        self._preview_save_path: str | None = None
        self._progress_snapshot: str | None = None
        self._selection_locks: dict[str, asyncio.Lock] = {}
        self._has_incomplete = False
        self._pipeline_poll = pipeline_poll
        self._monitor_active = monitor_active
        self._monitor_idle = monitor_idle
        # The releases of the torrent sources (stage 15), beside the pipeline of the torrent series until those are gone.
        self._release_wake = asyncio.Event()
        # A change of the schedule wakes the loop of the checks of the releases (_watch_loop).
        self._schedule_wake = asyncio.Event()
        self.releases = Releases(
            ReleaseStore(db), client=self._client, reply=self.reply_of, publish=self._release_event,
            wake=self._release_wake.set)
        super().__init__(bus)

    def register(self) -> None:
        self.handle(TORRENTS_ADD, self.on_add)
        self.handle(TORRENTS_INFO_GET, self.on_info)
        self.handle(TORRENTS_FILES_GET, self.on_files)
        self.handle(TORRENTS_PREVIEW_FETCH, self.on_preview_fetch,
                    concurrent=True)
        self.handle(TORRENTS_PAUSE, self.on_pause)
        self.handle(TORRENTS_RESUME, self.on_resume)
        self.handle(TORRENTS_RECHECK, self.on_recheck)
        self.handle(TORRENTS_DELETE, self.on_delete)
        self.handle(TORRENTS_RENAME_FILE, self.on_rename_file)
        self.handle(TORRENTS_SET_LOCATION, self.on_set_location)
        self.handle(TORRENTS_DB_ACTIVE, self.on_db_active)
        self.handle(TORRENTS_DB_DEACTIVATE_ALL, self.on_db_deactivate_all)
        self.handle(TORRENTS_DB_FILES_LIST, self.on_db_files_list)
        self.handle(TORRENTS_DB_FILES_UPSERT, self.on_db_files_upsert)
        self.handle(TORRENTS_REGISTER, self.on_register)
        self.handle(TORRENTS_QUEUE_GET, self.on_queue_get)
        self.handle(TORRENTS_FS_VERIFY, self.on_fs_verify, concurrent=True)
        self.handle(TORRENTS_DRIVE_INCOMPLETE, self.on_drive_incomplete)
        self.handle(TORRENTS_DB_HISTORY, self.on_db_history)
        self.handle(TORRENTS_DB_ADD, self.on_db_add)
        self.handle(TORRENTS_DB_DOWNLOADED_COUNTS,
                    self.on_downloaded_counts)
        self.handle(TORRENTS_SEASONS, self.on_seasons)
        self.handle(TORRENTS_DB_FILES_FOR_SERIES, self.on_files_for_series)
        self.handle(TORRENTS_DB_PROGRESS_LIST, self.on_progress_list)
        self.handle(TORRENTS_COMPOSITION, self.on_composition,
                    concurrent=True)
        self.handle(SERIES_DELETED, self.on_series_deleted)
        self.handle(SERIES_UPDATED, self.on_series_updated)
        self.handle(TORRENTS_RELEASES_GET, self.on_releases_get)
        self.handle(GATEWAY_SSE_CLIENTS, self.on_sse_clients)
        self.handle(TORRENTS_RELEASE_DOWNLOAD, self.on_release_download)
        self.handle(TORRENTS_STAGING_GET, self.on_staging_get)
        self.handle(TORRENTS_STAGING_SET, self.on_staging_set)
        self.handle(TORRENTS_RELEASE_CHECK, self.on_release_check)
        self.handle(TORRENTS_RELEASE_WATCH, self.on_release_watch)
        self.handle(TORRENTS_RELEASE_DROP_FILES, self.on_release_drop_files)
        self.handle(SCAN_RUN_CHANGED, self.on_run_changed)
        self.handle(SERIES_SOURCE_REMOVED, self.on_source_removed)
        self.handle(SETTINGS_CHANGED, self.on_settings_changed)
        self.handle(TRACKERAUTH_CREDENTIALS_CHANGED, self.on_credentials_changed)

    async def _resolve_qbt_config(self) -> dict:
        """Env wins (the stand); without env the qbittorrent credentials come
        from the auth table over the bus (the production scenario, Р-22)."""
        cfg = dict(self._qbt_config)
        if cfg.get("base_url"):
            return cfg
        try:
            row = (await self.reply_of(TRACKERAUTH_CREDENTIALS_GET, {"service": "qbittorrent"})).credentials
            # The credentials never carry the password (docs/security.md): it comes in the one answer that does.
            secret = (await self.reply_of(TRACKERAUTH_SECRET_GET, {"service": "qbittorrent"})).secret if row else ""
        except BusRequestError as exc:
            self.log.warning("креды qBittorrent не получены: %s", exc)
            return cfg
        return dict(base_url=(row.url if row else "") or "",
                    username=(row.username if row else "") or "",
                    password=secret)

    async def _connect_qbt(self) -> None:
        """Makes the client and logs in; any error (including an empty URL, the credentials
        are not set yet) does not kill the process: the pipeline waits (finding 7: the module neither freezes nor brings the system down)."""
        try:
            cfg = await self._resolve_qbt_config()
            old, self.qbt = self.qbt, QbtClient(**cfg)
            if old:
                await old.close()
            if not cfg.get("base_url"):
                self.log.warning("креды qBittorrent не настроены (ни env, ни "
                                 "таблица auth) — конвейер ждёт настроек")
                return
            try:
                await self.qbt.login()
            except Exception as exc:  # noqa: BLE001 — unavailability is not fatal
                self.log.error("qBittorrent недоступен: %s — конвейер будет "
                               "ждать", exc)
        finally:
            self._looked.set()

    async def _known(self) -> None:
        """The client of qBittorrent is looked at before anything is said of it: it is made at the start, after the credentials
        are read over the bus, so until then there is none to ask, which is not «not configured». A request that needs it waits
        for that (its fuse is that of its topic)."""
        if not self._looked.is_set():
            self.log.info("qBittorrent: запрос ждёт, пока будет создан клиент и сделан первый вход")
            await self._looked.wait()

    async def _client(self) -> QbtClient:
        """The one way to the client of the releases: it is looked at first (`_known`), so what comes back is there."""
        await self._known()
        return self.qbt

    async def on_credentials_changed(self, env: Envelope) -> None:
        if CredentialsChanged.model_validate(env.payload).service != "qbittorrent" or self._qbt_injected:
            return
        self.log.info("креды qBittorrent изменились — пересоздаём клиент")
        await self._connect_qbt()

    async def on_start(self) -> None:
        if not self._qbt_injected:
            await self._connect_qbt()
        await self._reconcile_with_qbt()
        await self._sweep_preview_orphans()
        await self._recover_tasks()
        self.spawn(self._pipeline_loop(), lasting=True)
        self.spawn(self._monitor_loop(), lasting=True)
        await self._start_releases()

    async def _reconcile_with_qbt(self) -> None:
        """Reconciles the DB with the real qBit at start (crash tolerance): a torrent
        in the DB but missing from qBit (removed by hand or lost) gets its row reset and the series goes back to
        'no torrent'; the next scan adds the release again. qBit unavailable: skip (no data is lost on a false conclusion)."""
        try:
            live = await self.qbt.torrents_info()
        except Exception as exc:  # noqa: BLE001 — qBit may not be up yet
            self.log.warning("reconcile с qBit пропущен — qBit недоступен: "
                             "%s", exc)
            return
        live_hashes = {(t.get("hash") or "").lower() for t in live}
        affected = await self.repo.deactivate_orphans(live_hashes)
        for series_id in set(affected):
            self.log.warning("серия %d: торрент отсутствует в qBit — запись "
                             "сброшена (reconcile при старте)", series_id)
            await self._contribute(series_id)
        if affected:
            self._broadcast_queue()

    async def on_stop(self) -> None:
        self._looked.set()  # nobody waits for a client that will not be made
        if self.qbt and not self._qbt_injected:
            await self.qbt.close()

    async def _recover_tasks(self) -> None:
        """Reconcile (Р-11): tasks from the DB go to memory (stage error is a carrier and
        does not return to the pipeline); rollups over all series."""
        series_ids = set()
        for task in await self.repo.all_tasks():
            series_ids.add(task["series_id"])
            if task["stage"] == pipeline.ERROR:
                continue
            self._pipe[task["torrent_hash"]] = {**task,
                                                "recheck_initiated": False}
        for row in await self.repo.all_active():
            series_ids.add(row["series_id"])
        for series_id in series_ids:
            await self._contribute(series_id)
        if self._pipe:
            self.log.info("восстановлено задач конвейера: %d",
                          len(self._pipe))
            self._wake.set()
        self._broadcast_queue()

    # --- queries -----------------------------------------------------------------

    async def on_add(self, env: Envelope) -> dict:
        """payload: {save_path, paused?, magnet | content_b64}
        reply:   {hash, link_type, existed}"""
        p = env.payload
        save_path = p["save_path"]
        paused = bool(p.get("paused", True))
        await self._known()

        if "magnet" in p:
            candidates = [infohash_from_magnet(p["magnet"])]
            link_type = "magnet"
            verdict = await self.qbt.add_magnet(p["magnet"], save_path,
                                                paused=paused)
        elif "content_b64" in p:
            content = base64.b64decode(p["content_b64"])
            candidates = infohash_candidates(content)
            link_type = "file"
            verdict = await self.qbt.add_torrent_file(content, save_path,
                                                      paused=paused)
        else:
            raise TorrentParseError(reason("torrent_need_link"))

        torrent_hash = await self.qbt.find_hash(candidates)
        if torrent_hash is None:
            raise RuntimeError(reason("qbt_torrent_not_appeared", verdict=verdict,
                                      candidates=candidates))
        existed = verdict == "fails"  # "Fails." and found by hash means it already existed
        if existed:
            self.log.info("торрент %s уже существовал в qBittorrent",
                          torrent_hash[:8])
        return {"hash": torrent_hash, "link_type": link_type,
                "existed": existed}

    async def on_info(self, env: Envelope) -> list[dict]:
        await self._known()
        return await self.qbt.torrents_info(
            (env.payload or {}).get("hashes"))

    async def on_files(self, env: Envelope) -> list[dict] | None:
        await self._known()
        return await self.qbt.torrent_files(env.payload["hash"])

    # --- naming preview on add (background fetch of the file list) --------

    async def on_preview_fetch(self, env: Envelope) -> None:
        """The releases of the add form to file lists, by preview.ready/failed. The handler is concurrent:
        waiting for a .torrent from sources does not hold the queue."""
        for item in (env.payload or {}).get("items") or []:
            tid = item.get("torrent_id")
            if item.get("magnet"):
                await self._preview_fetch_magnet(tid, item["magnet"])
            elif item.get("link"):
                await self._preview_fetch_file(tid, item["link"])
            else:
                self._emit_preview_failed(tid, None, reason("preview_no_link"))

    async def _preview_fetch_file(self, tid, link: str) -> None:
        """A .torrent by its link: sources gives the file (cache, polite pauses to the tracker), the file
        list is read locally."""
        try:
            reply = await self.reply_of(SOURCES_TORRENT_FILE_GET, {"url": link, "torrent_id": tid})
        except (BusRequestError, asyncio.TimeoutError) as exc:
            detail = relayed(str(exc))
            failed = (reason("preview_torrent_download_failed", detail=detail) if detail
                      else reason("preview_torrent_no_answer"))
            self._emit_preview_failed(tid, None, failed)
            return
        self._preview_parse_torrent(tid, reply.content_b64)

    def _preview_parse_torrent(self, tid, content_b64: str) -> None:
        """A .torrent to its file list locally (bencode), without qBit; the result is torrents.preview.ready,
        the one channel with the magnet branch."""
        try:
            content = base64.b64decode(content_b64)
            name, files = torrent_file_list(content)
            ih = infohash_candidates(content)[0]
            data = {"name": name, "files": files}
            self._preview_cache[ih] = data
            self._emit_preview_ready(tid, ih, data)
        except Exception as exc:  # noqa: BLE001 — preview must not bring the system down
            self.log.warning("preview .torrent %s: %s", tid, exc)
            self._emit_preview_failed(
                tid, None, reason("preview_torrent_unreadable", detail=str(exc)))

    async def _preview_fetch_magnet(self, tid, magnet: str) -> None:
        """A magnet gets its metadata from the swarm through qBit. A paused torrent does not fetch it (checked),
        so the torrent is added STARTED in a category of its own, its files are read and it is deleted (about 0
        bytes of content). A background task keeps the bus free; one fetch per infohash; the cache outlives
        reopened windows."""
        try:
            ih = infohash_from_magnet(magnet)
        except TorrentParseError as exc:
            self._emit_preview_failed(tid, None, str(exc))
            return
        cached = self._preview_cache.get(ih)
        if cached is not None:
            self._emit_preview_ready(tid, ih, cached)
            return
        if ih in self._preview_inflight:
            return  # a fetch is already going: its outcome is the event
        self._preview_inflight.add(ih)
        task = self.spawn(self._preview_magnet_task(tid, magnet, ih))
        self._preview_tasks.add(task)
        task.add_done_callback(self._preview_tasks.discard)

    async def _preview_magnet_task(self, tid, magnet: str, ih: str) -> None:
        added = False
        try:
            await self._known()
            save_path = await self._preview_save_path_get()
            await self.qbt.add_magnet(magnet, save_path, paused=False,
                                      category=PREVIEW_CATEGORY)
            added = True
            files = await self._preview_poll_files(ih, timeout=25.0)
            if files is None:
                self._emit_preview_failed(tid, ih, reason("preview_no_metadata"))
                return
            data = {"name": None, "files": files}
            self._preview_cache[ih] = data
            self._emit_preview_ready(tid, ih, data)
        except Exception as exc:  # noqa: BLE001 — preview must not bring the system down
            self.log.warning("preview magnet %s: %s", ih[:8], exc)
            self._emit_preview_failed(tid, ih, reason("preview_fetch_failed", detail=str(exc)))
        finally:
            if added:
                await self._preview_cleanup(ih)
            self._preview_inflight.discard(ih)

    async def _preview_poll_files(self, ih: str, *, timeout: float,
                                  poll: float = 0.5) -> list[dict] | None:
        """Polls /torrents/files until the metadata appears. None means timeout."""
        async def look() -> list[dict] | None:
            files = await self.qbt.torrent_files(ih)
            return [{"path": f.get("name"), "size": f.get("size")} for f in files] if files else None

        found, _attempts, _exhausted = await retrying(
            look, final=lambda got: got is not None,
            budget=Budget(attempts=int(timeout / poll) + 1, pauses=(poll,), limit_s=timeout))
        return found

    async def _preview_cleanup(self, ih: str) -> None:
        """Cleans up a preview torrent by its KNOWN infohash, with confirmation: delete
        is repeated until qBit confirms it is gone (this survives the asynchrony of add and a cancel).
        deleteFiles=true removes any partial files (content is about 0: metadata comes before peers, but this is a safeguard)."""
        async def attempt() -> bool | None:
            try:
                await self.qbt.delete([ih], delete_files=True)
                return not await self.qbt.torrents_info([ih])
            except QbtError:
                return None  # qBit unavailable — the start sweep takes it by category

        gone, _attempts, _exhausted = await retrying(attempt, final=lambda got: got is not False,
                                                     budget=PREVIEW_CLEANUP_BUDGET)
        if gone is False:
            self.log.warning("preview: раздача %s не убралась за отведённое время",
                             ih[:8])

    async def _preview_save_path_get(self) -> str:
        if self._preview_save_path is None:
            self._preview_save_path = await self.qbt.default_save_path()
        return self._preview_save_path or ""

    async def _sweep_preview_orphans(self) -> None:
        """BACKSTOP of zombie safety: sweep all torrents of the preview category at start
        (orphaned by a crash between add and delete). qBit unavailable: skip (as reconcile does, no data is lost)."""
        if self.qbt is None:
            return
        try:
            await self.qbt.create_category(PREVIEW_CATEGORY)
            rows = await self.qbt.torrents_info(category=PREVIEW_CATEGORY)
            hashes = [t["hash"] for t in rows if t.get("hash")]
            if hashes:
                await self.qbt.delete(hashes, delete_files=True)
                self.log.warning("preview: подметено осиротевших раздач: %d",
                                 len(hashes))
        except Exception as exc:  # noqa: BLE001 — module start must not fail
            self.log.warning("preview: sweep категории пропущен: %s", exc)

    def _emit_preview_ready(self, tid, ih, data: dict) -> None:
        self.publish_event(TORRENTS_PREVIEW_READY, {
            "torrent_id": tid, "infohash": ih,
            "name": data.get("name"), "files": data.get("files") or []})

    def _emit_preview_failed(self, tid, ih, failure: str) -> None:
        self.publish_event(TORRENTS_PREVIEW_FAILED, {
            "torrent_id": tid, "infohash": ih, "reason": failure})

    # --- commands ------------------------------------------------------------------

    async def on_pause(self, env: Envelope) -> None:
        await self._known()
        await self.qbt.pause(env.payload["hashes"])

    async def on_resume(self, env: Envelope) -> None:
        await self._known()
        await self.qbt.resume(env.payload["hashes"])

    async def on_recheck(self, env: Envelope) -> None:
        await self._known()
        await self.qbt.recheck(env.payload["hashes"])

    async def on_delete(self, env: Envelope) -> None:
        await self._known()
        await self.qbt.delete(env.payload["hashes"],
                              delete_files=bool(
                                  env.payload.get("delete_files", False)))

    async def on_rename_file(self, env: Envelope) -> None:
        p = env.payload
        await self._known()
        await self.qbt.rename_file(p["hash"], p["old_path"], p["new_path"])

    async def on_set_location(self, env: Envelope) -> None:
        await self._known()
        await self.qbt.set_location(env.payload["hash"],
                                    env.payload["location"])

    # --- selective download -----------------------------------------------------------

    async def _project_and_apply(self, series_id: int, *,
                                 allow_resume: bool) -> int:
        """Series memory -> is_excluded column -> qBit priorities."""
        try:
            series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        except BusRequestError as exc:
            self.log.warning("серия %d: память исключений не получена: %s",
                             series_id, exc)
            return 0
        seasons, episodes = memory_of(series)
        changed = await self.repo.project_selection(series_id, seasons,
                                                    episodes)
        for qb_hash in await self.repo.active_hashes(series_id):
            await self._apply_selection(qb_hash, allow_resume=allow_resume)
        return changed

    async def _apply_selection(self, qb_hash: str, *,
                               allow_resume: bool) -> None:
        """The only place where the selection becomes qBit priorities.

        The DB stays the truth: a qBit failure is only logged; the next file check applies the priorities again."""
        lock = self._selection_locks.setdefault(qb_hash, asyncio.Lock())
        async with lock:
            rows = await self.repo.files_by_hash(qb_hash)
            try:
                qb_files = await self.qbt.torrent_files(qb_hash) or []
            except QbtError as exc:
                self.log.warning("[%s] состав раздачи не получен: %s",
                                 qb_hash[:8], exc)
                return
            plan = selection.resolve(rows, qb_files)
            if plan.unmatched:
                self.log.warning("[%s] файлы БД не найдены в раздаче: %s",
                                 qb_hash[:8], ", ".join(plan.unmatched[:5]))
            if not plan.to_disable and not plan.to_enable:
                return
            try:
                await self.qbt.set_file_priority(qb_hash, plan.to_disable,
                                                 selection.PRIORITY_SKIP)
                await self.qbt.set_file_priority(qb_hash, plan.to_enable,
                                                 selection.PRIORITY_NORMAL)
            except QbtError as exc:
                self.log.warning("[%s] приоритеты файлов не применены: %s",
                                 qb_hash[:8], exc)
                return
            self.log.info("[%s] выбор применён: выключено %d, включено %d",
                          qb_hash[:8], len(plan.to_disable),
                          len(plan.to_enable))
            if plan.to_enable and allow_resume:
                await self._resume_for_new_files(qb_hash)

    async def _resume_for_new_files(self, qb_hash: str) -> None:
        """The series is back in the plan: qBit downloads it only while running.

        The torrent in the pipeline is not touched: the kernel invariant requires a pause until the renaming ends."""
        if qb_hash in self._pipe or await self.repo.task_by_hash(qb_hash):
            return
        try:
            infos = await self.qbt.torrents_info([qb_hash])
        except QbtError:
            return
        info = next((i for i in infos if i.get("hash") == qb_hash), None)
        if info and info.get("state") in pipeline.PAUSED:
            await self.qbt.resume([qb_hash])
            self.log.info("[%s] раздача возобновлена: состав расширен",
                          qb_hash[:8])

    async def _prepare_composition(self, series_id: int,
                                   qb_hash: str) -> None:
        """The torrent's composition in the DB before download: metadata -> rules ->
        priorities. The torrent stays paused meanwhile."""
        if not await self._await_metadata(qb_hash):
            self.log.warning("[%s] метаданные не получены — состав не "
                             "построен", qb_hash[:8])
            return
        await self.request(RENAMING_PROCESS_TORRENT, {
            "series_id": series_id, "qb_hash": qb_hash, "dry_run": True})
        await self._project_and_apply(series_id, allow_resume=False)

    async def _await_metadata(self, qb_hash: str,
                              attempts: int = 60) -> bool:
        """Waits for the file list. A magnet gives its metadata only while running,
        so it is started for this time and paused at once: the same exception to the invariant as in the pipeline."""
        started = False

        async def look() -> bool | None:
            nonlocal started
            infos = await self.qbt.torrents_info([qb_hash])
            info = next((i for i in infos if i.get("hash") == qb_hash), None)
            if info is None:
                return False
            if info.get("total_size", 0) > 0:
                return True
            if not started:
                await self.qbt.resume([qb_hash])
                started = True
            return None

        try:
            ready, _attempts, _exhausted = await retrying(
                look, final=lambda got: got is not None,
                budget=Budget(attempts=attempts, pauses=(self._pipeline_poll,), limit_s=UNTIMED))
            return bool(ready)
        except QbtError as exc:
            self.log.warning("[%s] ожидание метаданных прервано: %s",
                             qb_hash[:8], exc)
            return False
        finally:
            if started:
                try:
                    await self.qbt.pause([qb_hash])
                except QbtError:
                    self.log.warning("[%s] пауза после метаданных не "
                                     "поставлена", qb_hash[:8])

    async def on_series_updated(self, env: Envelope) -> None:
        """The exclusion memory changed: rebuild the download plan."""
        event = SeriesUpdated.model_validate(env.payload)
        if not ({"ignored_seasons", "ignored_episodes"} & event.model_fields_set):
            return
        await self._project_and_apply(event.series_id, allow_resume=True)
        await self._broadcast_downloaded(event.series_id)
        self.send_command(METADATA_SEASONS_RECOMPUTE, {"series_id": event.series_id})

    # --- torrent registry (contracts of Р-12) ----------------------------------------------

    async def on_db_active(self, env: Envelope) -> list[dict]:
        """The series' active torrents, checked against qBittorrent."""
        rows = await self.repo.active_torrents(env.payload["series_id"])
        hashes = [r["qb_hash"] for r in rows if r.get("qb_hash")]
        if not hashes:
            return []
        live = {i["hash"] for i in await self.qbt.torrents_info(hashes)}
        return [r for r in rows if r.get("qb_hash") in live]

    async def on_db_deactivate_all(self, env: Envelope) -> dict:
        hashes = await self.repo.deactivate_all(env.payload["series_id"])
        if hashes:
            await self.qbt.delete(hashes, delete_files=False)
        return {"deactivated": len(hashes)}

    async def on_db_files_list(self, env: Envelope) -> list[dict]:
        row = await self.repo.torrent_by_hash(env.payload["qb_hash"])
        if not row:
            return []
        return await self.repo.files_for_torrent(row["id"])

    async def on_db_files_upsert(self, env: Envelope) -> dict:
        """Records of a torrent's files (renaming writes them after the rename;
        we own the torrent_files table)."""
        row = await self.repo.torrent_by_hash(env.payload["qb_hash"])
        if not row:
            raise LookupError(reason("torrent_not_in_db", qb_hash=env.payload["qb_hash"][:8]))
        await self.repo.upsert_files(row["id"], env.payload["files"])
        # The torrent's composition changed (new release, rules re-applied): the memory projection
        # restores the user's choice on the new rows before qBit starts downloading the extra.
        await self._project_and_apply(row["series_id"], allow_resume=False)
        await self._broadcast_downloaded(row["series_id"])  # Д1
        return {"count": len(env.payload["files"])}

    async def on_register(self, env: Envelope) -> dict:
        """A torrent's identity is its infohash (qb_hash), not the tracker's label.

        POINT 3: if the series already knows this infohash and it is healthy, nothing is downloaded or duplicated;
        only the label and date are re-glued (the date detector, POINT 1, must converge). POINT 4: a new infohash means
        the old torrent must leave qBit, its previous row is marked 'replaced' (history), and the new one runs through the pipeline."""
        p = env.payload
        qb_hash, series_id = p["qb_hash"], p["series_id"]
        torrent, replaces = p["torrent"], p.get("replaces")

        # a task for this infohash is already in flight: do nothing (idempotent)
        if qb_hash in self._pipe:
            return {"existed": True}

        link_type = p.get("link_type", "file")
        start_pipeline = bool(p.get("start_pipeline", True))
        task_row = await self.repo.task_by_hash(qb_hash)
        is_error_retry = bool(task_row and task_row["stage"] == pipeline.ERROR)
        known = await self.repo.row_by_hash(series_id, qb_hash)

        if not start_pipeline:
            # Preparing the composition: the torrent stays paused and no pipeline task is created,
            # so the download does not start until the user picks the episodes and runs the scan.
            if known:
                await self.repo.refresh_listing(known["id"], torrent, qb_hash)
            else:
                await self.repo.upsert_registered(series_id, torrent, qb_hash)
                await self._retire_replaced(torrent, replaces, qb_hash)
            await self._prepare_composition(series_id, qb_hash)
            return {"existed": bool(known), "prepared": True}

        if known and not is_error_retry:
            # POINT 3: "do nothing" applies ONLY when the torrent is really complete and healthy in qB,
            # not merely known to the DB. Removed from qB and re-added / incomplete / in error -> reconcile:
            # update the same row (no duplicate) and (re)run the pipeline so it downloads and renames.
            await self.repo.refresh_listing(known["id"], torrent, qb_hash)
            if await self._is_complete_in_qb(qb_hash):
                return {"existed": True}
            await self._enter_pipeline(qb_hash, series_id, torrent,
                                       link_type, "None")
            return {"existed": False}

        # POINT 4 (or a restart after an error): a new infohash becomes active.
        await self.repo.upsert_registered(series_id, torrent, qb_hash)

        old_torrent_id = await self._retire_replaced(torrent, replaces,
                                                     qb_hash)
        await self._enter_pipeline(qb_hash, series_id, torrent, link_type,
                                   old_torrent_id)
        return {"existed": False}

    async def _retire_replaced(self, torrent: dict, replaces: dict | None,
                               qb_hash: str) -> str:
        """POINT 4: the previous torrent goes to history and is removed from qBit."""
        if not replaces or replaces.get("torrent_id") == torrent["torrent_id"]:
            return "None"
        old_torrent_id = replaces.get("torrent_id") or "None"
        old_db_id = replaces.get("db_id")
        if old_db_id is not None:
            # the exact row by db_id (history is kept, files are cleared)
            await self.repo.deactivate_and_clear_files(old_db_id)
        old_hash = replaces.get("qb_hash")
        if old_hash and old_hash != qb_hash:
            # a new infohash: the old torrent must be removed from qBit (mandatory)
            try:
                await self.qbt.delete([old_hash], delete_files=False)
            except QbtError as exc:
                self.log.warning("замена: старый торрент %s не удалён "
                                 "из qBit: %s", old_hash[:8], exc)
        return old_torrent_id

    async def _is_complete_in_qb(self, qb_hash: str) -> bool:
        """The torrent is really complete and healthy in qBit (not merely known to the DB):
        only then a repeated registration is a no-op (POINT 3). qBit unavailable / no torrent / progress <100 / error: not complete, the pipeline must reconcile it."""
        try:
            infos = await self.qbt.torrents_info([qb_hash])
        except QbtError:
            return False
        info = next((i for i in infos if i.get("hash") == qb_hash), None)
        if info is None:
            return False
        return (info.get("progress", 0) >= 1.0
                and info.get("state") not in _ERROR_STATES)

    async def _is_stuck_in_qb(self, qb_hash: str) -> bool:
        """The torrent is PAUSED in qB and not complete: it is stuck (the pipeline paused it and did not resume).
        Downloading (running) and peer-waiting (stalledDL) torrents are not stuck, so they are left alone."""
        try:
            infos = await self.qbt.torrents_info([qb_hash])
        except QbtError:
            return False
        info = next((i for i in infos if i.get("hash") == qb_hash), None)
        if info is None:
            return False
        return (info.get("progress", 0) < 1.0
                and info.get("state") in pipeline.PAUSED)

    async def _enter_pipeline(self, qb_hash: str, series_id: int,
                              torrent: dict, link_type: str,
                              old_torrent_id: str) -> None:
        """Queues a pipeline task for the infohash and wakes the pipeline."""
        task = {"torrent_hash": qb_hash, "series_id": series_id,
                "torrent_id": torrent["torrent_id"],
                "old_torrent_id": old_torrent_id,
                "stage": pipeline.INITIAL_STAGE[link_type]}
        await self.repo.upsert_task(task)
        self._pipe[qb_hash] = {**task, "recheck_initiated": False}
        self._wake.set()
        self._broadcast_queue()
        await self._contribute(series_id)

    async def on_db_history(self, env: Envelope) -> list[dict]:
        """The whole torrent history of the series (contract GET torrents/history)."""
        return await self.repo.history(env.payload["series_id"])

    async def on_files_for_series(self, env: Envelope) -> list[dict]:
        """Files of all the series' torrents (the sourcefile list, composition)."""
        return await self.repo.files_for_series(env.payload["series_id"])

    async def on_composition(self, env: Envelope) -> list[dict]:
        """Contract GET composition for torrent series (Р-21): fresh rules,
        name preview, and whether the file is present on disk."""
        series_id = env.payload["series_id"]
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        files = await self.repo.files_for_series(series_id)
        plan: list[dict] = []
        profile_id = torrent_profile_id(series)
        for f in files:
            current = f.get("renamed_path") or f["original_path"]
            present = await storage.call(
                os.path.exists,
                os.path.join(series.save_path, current))
            excluded = {"is_excluded": bool(f.get("is_excluded")),
                        "exclude_reason": f.get("exclude_reason")}
            meta = f.get("extracted_metadata")
            meta_dict = json.loads(meta) if meta else {}
            is_non_video = (meta_dict.get("is_extra")
                            or meta_dict.get("media_type") in ("audio", "subtitles", "extra"))
            if profile_id is None or is_non_video:
                # as in the original: without a profile or for non-video, the saved metadata
                preview = f.get("renamed_path") or meta_dict.get("target_path") or current
                plan.append({
                    "id": f["id"], "original_path": f["original_path"],
                    "renamed_path_preview": preview,
                    "status": f["status"],
                    "extracted_metadata": meta_dict,
                    "is_file_present": present,
                    "qb_hash": f.get("qb_hash"),
                    "is_mismatch": False, **excluded})
                continue
            reply = await self.request(RULES_FORMAT_TORRENT_FILE, {
                "series": series, "profile_id": profile_id,
                "file_basename": os.path.basename(f["original_path"]),
                "original_path": f["original_path"]})
            preview = reply["filename"] or current
            plan.append({
                "id": f["id"], "original_path": f["original_path"],
                "renamed_path_preview": preview,
                "status": f["status"],
                "extracted_metadata": reply.get("extracted") or {},
                "is_file_present": present,
                "qb_hash": f.get("qb_hash"),
                "is_mismatch": current != preview, **excluded})
        plan.extend(await self._unregistered_files(series_id, files))
        return plan

    async def _unregistered_files(self, series_id: int,
                                  known: list[dict]) -> list[dict]:
        """Torrent files missing from the registry: the rules did not parse them and they will not download.
        The user must see this rather than guess where half of the torrent went."""
        seen = {f.get("renamed_path") or f["original_path"] for f in known}
        seen |= {f["original_path"] for f in known}
        extra: list[dict] = []
        for qb_hash in await self.repo.active_hashes(series_id):
            try:
                qb_files = await self.qbt.torrent_files(qb_hash) or []
            except QbtError as exc:
                self.log.warning("[%s] состав для композиции не получен: %s",
                                 qb_hash[:8], exc)
                continue
            for position, qb_file in enumerate(qb_files):
                name = qb_file.get("name") or ""
                if name in seen:
                    continue
                extra.append({
                    "id": f"qb-{qb_hash[:8]}-{qb_file.get('index', position)}",
                    "original_path": name,
                    "renamed_path_preview": name,
                    "status": "unregistered",
                    "extracted_metadata": {},
                    "is_file_present": False,
                    "qb_hash": qb_hash,
                    "is_mismatch": False,
                    "is_excluded": True,
                    "exclude_reason": selection.REASON_UNRECOGNIZED})
        return extra

    async def on_db_add(self, env: Envelope) -> dict:
        """Torrents of the series add form: the DB registry only, no qBit
        (the semantics of the old add_series: the pipeline starts the scan)."""
        added = await self.repo.add_registered_rows(
            env.payload["series_id"], env.payload["torrents"])
        return {"added": added}

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}} — renamed files (Р-19)."""
        return {"counts": await self.repo.downloaded_counts()}

    async def on_seasons(self, env: Envelope) -> dict:
        """{seasons: [..]} — the real seasons of the series from the naming (for the TMDB aggregate over several seasons)."""
        return {"seasons": await self.repo.seasons_for_series(
            env.payload["series_id"])}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Cascade R-19: the registry, the files and the tasks of the pipeline go; the torrents leave qBittorrent with
        their files left in place (they lie in the library), as a release of a source leaves it with its series; the
        cache of the .torrent files is dropped by sources on our commands."""
        series_id = SeriesDeleted.model_validate(env.payload).series_id
        rows = await self.repo.history(series_id)
        hashes = [r["qb_hash"] for r in rows if r.get("qb_hash")]
        if hashes:
            try:
                await self.qbt.delete(hashes, delete_files=False)
            except QbtError as exc:
                self.log.warning("удаление серии %d: записи в qBit не "
                                 "удалены: %s", series_id, exc)
        for r in rows:
            if r.get("torrent_id"):
                self.send_command(SOURCES_TORRENT_FILE_DROP, {"torrent_id": r["torrent_id"]})
        await self.repo.delete_for_series(series_id)
        dropped = [h for h, t in self._pipe.items()
                   if t["series_id"] == series_id]
        for h in dropped:
            self._pipe.pop(h, None)
        self._flags_cache.pop(series_id, None)
        self._names_cache.pop(series_id, None)
        if dropped:
            self._broadcast_queue()
        await self._forget_unlisted_pages()
        self.publish_event(SERIES_PURGED, SeriesPurged(series_id=series_id, owner=self.name).model_dump(mode="json"))

    async def on_queue_get(self, env: Envelope) -> dict:
        series_id = (env.payload or {}).get("series_id")
        tasks = [t for t in self._pipe.values()
                 if series_id is None or t["series_id"] == series_id]
        return {"count": len(tasks), "tasks": tasks}

    async def on_fs_verify(self, env: Envelope) -> dict:
        """Files on disk: a 100%-downloaded renamed file with no file on disk -> missing -> a recheck task (qBit restores it).
        Called when the series window opens, instead of a background check every 60 s."""
        series_id = env.payload["series_id"]
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        files = await self.repo.files_for_series(series_id)
        # A series with no file of the old pipeline has nothing to check or to warn about here.
        if not files:
            return {"missing": 0, "recheck_started": 0}
        # F7: path/NAS unavailable -> do NOT mark everything as missing falsely and do not start a mass recheck.
        # The check is skipped until it is restored.
        if not await storage.call(os.path.exists, series.save_path):
            self.log.warning("серия %d: путь %s недоступен — сверка файлов "
                             "пропущена", series_id, series.save_path)
            return {"missing": 0, "recheck_started": 0}
        missing_hashes: set[str] = set()
        missing = 0
        for f in files:
            # An excluded file is absent from disk by design: without this branch
            # it would go to missing on every scan and trigger a recheck.
            if f.get("is_excluded"):
                continue
            if f.get("progress", 0) < 100 or not f.get("renamed_path"):
                continue
            path = os.path.join(series.save_path, f["renamed_path"])
            exists = await storage.call(os.path.exists, path)
            if f["status"] == "renamed" and not exists:
                await self.repo.set_file_status(f["id"], "missing")
                self.log.warning("файл торрента пропал: %s", path)
                missing += 1
                missing_hashes.add(f["qb_hash"])
            elif f["status"] == "missing":
                if exists:
                    await self.repo.set_file_status(f["id"], "renamed")
                else:
                    missing_hashes.add(f["qb_hash"])

        started = 0
        for qb_hash in missing_hashes:
            if not qb_hash or qb_hash in self._pipe:
                continue
            row = await self.repo.torrent_by_hash(qb_hash)
            if not row:
                continue
            task = {"torrent_hash": qb_hash, "series_id": series_id,
                    "torrent_id": row["torrent_id"],
                    "old_torrent_id": "None",
                    "stage": pipeline.RECHECKING}
            await self.repo.upsert_task(task)
            await self.repo.set_files_status_by_hash(qb_hash, "missing",
                                                     "rechecking")
            self._pipe[qb_hash] = {**task, "recheck_initiated": False}
            started += 1
        if started:
            self._wake.set()
            self._broadcast_queue()
            await self._contribute(series_id)
        # Self-healing point: qBit priorities may have diverged from the DB
        # (the client was unavailable, or the torrent was edited by hand).
        await self._project_and_apply(series_id, allow_resume=False)
        await self._broadcast_downloaded(series_id)  # Д1: missing changes the count
        return {"missing": missing, "recheck_started": started}

    async def on_drive_incomplete(self, env: Envelope) -> dict:
        """Reconciles stuck torrents (situation P10): an active torrent that IS in qB but is not complete
        and has NO pipeline task (the task was lost or not finished) goes back into the pipeline.
        Healthy (complete) torrents and those already driven or in error (a task exists) are left alone.
        The scan calls it on every pass: it restores the legacy self-healing, point by point."""
        series_id = env.payload["series_id"]
        driven = 0
        for row in await self.repo.active_torrents(series_id):
            qb_hash = row.get("qb_hash")
            if not qb_hash or qb_hash in self._pipe:
                continue
            if await self.repo.task_by_hash(qb_hash):
                continue  # a task already exists (including a carrier in error): leave it alone
            if not await self._is_stuck_in_qb(qb_hash):
                continue  # downloading / waiting for peers / complete: not stuck
            torrent = {"torrent_id": row["torrent_id"], "link": row.get("link"),
                       "magnet": None, "date_time": row.get("date_time"),
                       "quality": row.get("quality"),
                       "episodes": row.get("episodes")}
            # the torrent is already in qB with metadata: the entry stage is the same as for a file
            await self._enter_pipeline(qb_hash, series_id, torrent, "file",
                                       "None")
            driven += 1
        if driven:
            self.log.info("серия %d: загнано в конвейер застрявших раздач: "
                          "%d", series_id, driven)
        return {"driven": driven}

    # --- pipeline ---------------------------------------------------------------------

    async def _pipeline_loop(self) -> None:
        while True:
            if not self._pipe:
                self._wake.clear()
                await self._wake.wait()
            try:
                infos = await self.qbt.torrents_info(list(self._pipe))
            except QbtError as exc:
                self.log.warning("конвейер: qBittorrent недоступен: %s", exc)
                await asyncio.sleep(self._monitor_active)
                continue
            info_map = {i["hash"]: i for i in infos}
            for qb_hash in list(self._pipe):
                task = self._pipe[qb_hash]
                info = info_map.get(qb_hash)
                if info is None:
                    self.log.warning("задача %s: торрент исчез из "
                                     "qBittorrent — задача снята",
                                     qb_hash[:8])
                    await self._drop_task(qb_hash, task["series_id"])
                    continue
                await self._advance(qb_hash, task, info)
            await asyncio.sleep(self._pipeline_poll)

    async def _advance(self, qb_hash: str, task: dict, info: dict) -> None:
        try:
            action, next_stage = pipeline.decide(
                task["stage"], info, task["recheck_initiated"])
            if action == "resume":
                await self.qbt.resume([qb_hash])
            elif action in ("pause", "force_pause"):
                if action == "force_pause":
                    self.log.warning("[%s] неожиданно активен на стадии "
                                     "'%s' — немедленная пауза (инвариант "
                                     "ядра)", qb_hash[:8], task["stage"])
                await self.qbt.pause([qb_hash])
            elif action == "rename":
                await self.request(RENAMING_PROCESS_TORRENT, {
                    "series_id": task["series_id"], "qb_hash": qb_hash})
            elif action == "recheck":
                await self.qbt.recheck([qb_hash])
                task["recheck_initiated"] = True
            elif action in ("complete", "resume_and_complete"):
                if action == "resume_and_complete":
                    await self.qbt.resume([qb_hash])
                await self._complete(qb_hash, task)
                return
            if next_stage:
                task["stage"] = next_stage
                await self.repo.set_stage(qb_hash, next_stage)
                self.log.info("[%s] стадия: %s", qb_hash[:8], next_stage)
                self._broadcast_queue()
                await self._contribute(task["series_id"])
        except Exception as exc:  # noqa: BLE001 — an error does not bring the pipeline down
            self.log.exception("[%s] ошибка на стадии '%s'", qb_hash[:8],
                               task["stage"])
            await self.repo.set_stage(qb_hash, pipeline.ERROR)
            del self._pipe[qb_hash]
            self._broadcast_queue()
            await self._contribute(task["series_id"])

    async def _complete(self, qb_hash: str, task: dict) -> None:
        await self.repo.delete_task(qb_hash)
        del self._pipe[qb_hash]
        self.send_command(CATALOG_SERIES_TOUCH_SCAN_TIME, {"series_id": task["series_id"]})
        # The rename sorted files into Season NN folders, so the real seasons are known:
        # recompute the multi-season TMDB aggregate.
        self.send_command(METADATA_SEASONS_RECOMPUTE, {"series_id": task["series_id"]})
        self.log.info("[%s] задача конвейера выполнена", qb_hash[:8])
        self._broadcast_queue()
        await self._contribute(task["series_id"])

    async def _drop_task(self, qb_hash: str, series_id: int) -> None:
        # The torrent vanished from qB: deactivate the row and drop its file records (symmetry with the startup reconcile, R3);
        # the series goes back to 'no torrent' and the next scan adds it again. Otherwise a phantom is_active=1 would remain (false counters).
        row = await self.repo.torrent_by_hash(qb_hash)
        was_active = bool(row and row["is_active"])
        await self.repo.drop_task_and_row(qb_hash, row["id"] if was_active else None)
        self._pipe.pop(qb_hash, None)
        if was_active:
            await self._broadcast_downloaded(series_id)  # Д1: the count dropped
        self._broadcast_queue()
        await self._contribute(series_id)

    def _broadcast_queue(self) -> None:
        tasks = list(self._pipe.values())
        self.publish_event(TORRENTS_QUEUE_CHANGED, {"count": len(tasks), "tasks": tasks})

    async def _broadcast_downloaded(self, series_id: int) -> None:
        """The downloaded files of the series changed -> push the counter to SSE
        (Д1): the card updates «скачано» at once, without a reload."""
        count = await self.repo.downloaded_count_for_series(series_id)
        self.publish_event(SERIES_DOWNLOADED_CHANGED, {"series_id": series_id, "count": count})

    # --- progress monitoring -----------------------------------------------------------

    async def _monitor_loop(self) -> None:
        while True:
            active = bool(self._pipe) or self._has_incomplete
            await asyncio.sleep(self._monitor_active if active
                                else self._monitor_idle)
            try:
                await self._monitor_tick()
            except QbtError as exc:
                self.log.warning("мониторинг: qBittorrent недоступен: %s",
                                 exc)
            except Exception:  # noqa: BLE001
                self.log.exception("ошибка такта мониторинга")

    async def _monitor_tick(self) -> None:
        rows = await self.repo.all_active()
        if not rows:
            self._has_incomplete = False
            await self._broadcast_progress()
            return
        by_series: dict[int, list[str]] = {}
        for r in rows:
            by_series.setdefault(r["series_id"], []).append(r["qb_hash"])
        all_hashes = [h for hs in by_series.values() for h in hs]
        info_map = {i["hash"]: i
                    for i in await self.qbt.torrents_info(all_hashes)}
        self._has_incomplete = any(
            i.get("progress", 0) < 1 for i in info_map.values())
        for series_id, hashes in by_series.items():
            for qb_hash in hashes:
                if qb_hash in info_map:
                    info = info_map[qb_hash]
                    err = _ERROR_STATES.get(info.get("state"))
                    await self.repo.upsert_progress(series_id, qb_hash,
                                                    info, err)
            await self.repo.remove_stale_progress(
                series_id, [h for h in hashes if h in info_map])
            await self._contribute(series_id)
        await self._broadcast_progress()

    # --- torrent progress for the UI (Р-23: instead of polling the tab) ---------------------

    async def _progress_tasks(self) -> TorrentTasks:
        rows = await self.repo.all_torrent_progress()
        old = tuple([TorrentProgress(**row, series_name=await self._series_name(row["series_id"])) for row in rows])
        return TorrentTasks(tasks=old + await self._release_tasks())

    async def _release_tasks(self) -> tuple[ReleaseTask, ...]:
        """Every release the module keeps, one task per series whose torrent source is the page: it is there until its source
        goes, whatever its state."""
        rows = await self.releases.store.all()
        if not rows:
            return ()
        series_of: dict[str, list[tuple[int, str]]] = {}
        for series in (await self.reply_of(CATALOG_SERIES_LIST, {})).series:
            for source in series.sources:
                if source.source_type == SourceType.TORRENT:
                    series_of.setdefault(await self._canonical(source.url), []).append((series.id, series.name))
        return release_tasks([await self.releases.view(row) for row in rows], series_of)

    async def _series_name(self, series_id: int) -> str:
        if series_id not in self._names_cache:
            try:
                series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
                self._names_cache[series_id] = series.name or ""
            except BusRequestError:
                return ""
        return self._names_cache[series_id]

    async def on_progress_list(self, env: Envelope) -> TorrentTasks:
        """Contract of the old GET /api/series/active_torrents."""
        return await self._progress_tasks()

    async def on_sse_clients(self, env: Envelope) -> None:
        """A window that connects gets the pictures of the torrents at once, as those of the other queues: it does not wait
        for the next change or tick to see what the client holds."""
        if not env.payload.get("count"):
            return
        self._broadcast_queue()
        self._progress_snapshot = None
        await self._broadcast_progress()

    async def _broadcast_progress(self) -> None:
        """torrents.progress.changed — only on real changes
        (SSE torrent_progress_update; replaces the 5-second polling of the «Агенты» tab, finding 40)."""
        picture = (await self._progress_tasks()).model_dump(mode="json")
        snapshot = json.dumps(picture, sort_keys=True)
        if snapshot == self._progress_snapshot:
            return
        self._progress_snapshot = snapshot
        self.publish_event(TORRENTS_PROGRESS_CHANGED, picture)

    # --- status rollup (Р-11) --------------------------------------------------------

    async def _contribute(self, series_id: int) -> None:
        flags = dict.fromkeys(
            ("metadata", "renaming", "checking", "activating", "error",
             "downloading", "idle", "queued", "ready"), False)
        for task in await self.repo.tasks_for_series(series_id):
            flag = pipeline.STAGE_FLAGS.get(task["stage"])
            if flag:
                flags[flag] = True
        # A torrent where no file is wanted reports progress=100 (nothing to download):
        # 'ready' by it would be a lie.
        fully_excluded = await self.repo.fully_excluded_hashes(series_id)
        for row in await self.repo.torrent_progress(series_id):
            if row["status"] in _ERROR_STATES:
                flags["error"] = True
            elif row["qb_hash"] in fully_excluded:
                continue
            elif flag := torrent_flag(row["status"], row["progress"] >= 100):
                flags[flag] = True
        # The releases of the torrent sources of the series speak by the same words as its own torrents.
        for flag in await self._release_flags(series_id):
            flags[flag] = True
        if self._flags_cache.get(series_id) == flags:
            return
        self._flags_cache[series_id] = flags
        self.publish_event(SERIES_STATUS_CONTRIBUTION, StatusContribution(
            source="torrents", series_id=series_id, flags=flags).model_dump(mode="json"))


    # --- the releases of the torrent sources (stage 15, modules/torrents/releases.py) ------------------------------

    def _release_event(self, view: ReleaseView) -> None:
        """A release told of: the window is told, and the series whose source is its page recount their status."""
        self.publish_event(TORRENTS_RELEASE_CHANGED, view.model_dump(mode="json"))
        self.spawn(self._contribute_page(view.url), name=f"torrents.release.contribute:{view.url}")

    async def _contribute_page(self, url: str) -> None:
        try:
            for series in (await self.reply_of(CATALOG_SERIES_LIST, {})).series:
                for source in series.sources:
                    if source.source_type == SourceType.TORRENT and await self._canonical(source.url) == url:
                        await self._contribute(series.id)
                        break
        except Exception:  # noqa: BLE001 - the next word of a release or the next tick of the monitor counts again
            self.log.exception("статус сериалов страницы раздач %s не пересчитан", url)

    async def _release_flags(self, series_id: int) -> set[str]:
        """The flags of the status that the releases of the pages of the torrent sources of the series give."""
        if not await self.releases.store.all():
            return set()
        flags: set[str] = set()
        for page in await self._pages_of(series_id):
            for view in await self.releases.of_page(page):
                if flag := status_flag(view):
                    flags.add(flag)
        return flags

    async def _start_releases(self) -> None:
        try:
            stored = (await self.reply_of(SETTINGS_VALUE_GET, {"key": STAGING_DIR_KEY})).value
        except Exception:  # noqa: BLE001 - without the setting no download begins; the moving releases are still followed
            self.log.exception("the staging folder of the torrent sources was not read")
            stored = None
        self.releases.staging = stored or ""
        try:
            runs = (await self.request(SCAN_RUNS_GET, {})).get("runs") or []
            for run in runs:
                self.releases.runs[run["series_id"]] = await self._pages_of(run["series_id"])
        except Exception:  # noqa: BLE001 - an update that begins in a run is the worse loss: none begins until the next run news
            self.log.exception("the open syncs were not read; the updates wait for the news of a run")
        try:
            self.releases.checks = parse((await self.reply_of(SETTINGS_VALUE_GET, {"key": SCHEDULE_KEY})).value).checks()
        except Exception:  # noqa: BLE001 - without the schedule no page is checked until it changes
            self.log.exception("the schedule of the checks of the releases was not read")
        self.spawn(self._release_loop(), lasting=True)
        self.spawn(self._watch_loop(), lasting=True)

    async def _canonical(self, url: str) -> str:
        resolved = await self.reply_of(SOURCES_SITE_RESOLVE, {"url": url})
        return resolved.canonical_url or url

    async def _scheduled_pages(self) -> set[str]:
        """The pages the schedule checks: of the series in the automatic mode, while the automatic sync is on."""
        try:
            if (await self.reply_of(SETTINGS_VALUE_GET, {"key": "scanner_agent_enabled"})).value != "true":
                return set()
            series = (await self.reply_of(CATALOG_SERIES_LIST, {})).series
            return {await self._canonical(source.url) for one in series if one.auto_scan_enabled for source in one.sources}
        except Exception:  # noqa: BLE001 - what cannot be counted is not checked; the next time of the schedule counts again
            self.log.exception("страницы для проверки раздач не посчитаны")
            return set()

    async def _pages_of(self, series_id: int) -> set[str]:
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        return {await self._canonical(source.url) for source in series.sources}

    async def on_releases_get(self, env: Envelope) -> ReleasesReply:
        """{url} → the releases kept for the page."""
        url = await self._canonical(PageUrl.model_validate(env.payload).url)
        return ReleasesReply(releases=tuple(await self.releases.of_page(url)))

    async def on_release_download(self, env: Envelope) -> ReleaseView:
        """«Скачать» — the release goes to the client; a refusal is NotAllowed with its reason."""
        ref = ReleaseRef.model_validate(env.payload)
        return await self.releases.download(await self._canonical(ref.url), ref.release)

    async def on_release_check(self, env: Envelope) -> ReleasesChecked:
        """The page is read now; the watched releases it lists newer begin their update or wait."""
        url = await self._canonical(PageUrl.model_validate(env.payload).url)
        return ReleasesChecked(releases=tuple(await self.releases.check(url)))

    async def on_release_watch(self, env: Envelope) -> ReleaseView:
        command = ReleaseWatch.model_validate(env.payload)
        return await self.releases.watch(await self._canonical(command.url), command.release, command.watched)

    async def on_release_drop_files(self, env: Envelope) -> None:
        ref = ReleaseRef.model_validate(env.payload)
        await self.releases.drop_files(await self._canonical(ref.url), ref.release)

    async def on_run_changed(self, env: Envelope) -> None:
        run = RunChanged.model_validate(env.payload)
        pages = await self._pages_of(run.series_id) if run.active else set()
        await self.releases.run_changed(run.series_id, run.active, pages)

    async def on_source_removed(self, env: Envelope) -> None:
        await self._forget_unlisted_pages()

    async def _forget_unlisted_pages(self) -> None:
        """The releases of a page no source of any series lists any more leave the client with their files."""
        try:
            series = (await self.reply_of(CATALOG_SERIES_LIST, {})).series
            listed = {await self._canonical(source.url) for one in series for source in one.sources}
        except Exception:  # noqa: BLE001 - nothing is removed on a list that could not be read
            self.log.exception("the pages of the sources were not counted; no release is forgotten")
            return
        for url in {row["url"] for row in await self.releases.store.all()} - listed:
            await self.releases.forget(url)

    async def on_staging_get(self, env: Envelope) -> StagingFolder:
        return StagingFolder(value=(await self.reply_of(SETTINGS_VALUE_GET, {"key": STAGING_DIR_KEY})).value or "")

    async def on_staging_set(self, env: Envelope) -> StagingSaved:
        """The folder of the releases, as the settings of the sources set it: an absolute path, the same for the client."""
        value = str(StagingSet.model_validate(env.payload).value or "").strip()
        if not os.path.isabs(value):
            return StagingSaved(ok=False, value=value, reason=reason("staging_dir_not_absolute"))
        await self.request(SETTINGS_VALUE_SET, {"key": STAGING_DIR_KEY, "value": value})
        return StagingSaved(ok=True, value=value)

    async def on_settings_changed(self, env: Envelope) -> None:
        setting = SettingValue.model_validate(env.payload)
        if setting.key == STAGING_DIR_KEY:
            # Every open window of the settings shows the folder the core holds now, whichever window set it.
            self.publish_event(TORRENTS_STAGING_CHANGED, StagingFolder(value=setting.value or "").model_dump(mode="json"))
            await self.releases.relocate(setting.value or "")
        elif setting.key == "scanner_agent_enabled":
            self._schedule_wake.set()  # switched on: a time missed while it was off is made up now
        elif setting.key == SCHEDULE_KEY:
            self.releases.checks = parse(setting.value).checks()
            self._schedule_wake.set()
            await self.releases.told_all()  # the next check of every release moved

    async def _release_loop(self) -> None:
        """Woken by a word (a release added, its priorities changed). The client pushes nothing: it is asked again at the
        pace of the monitor while a release moves or is checked, and at the idle pace otherwise, so that a check the client
        began by itself is told too."""
        while True:
            try:
                busy = await self.releases.tick()
                await self._broadcast_progress()
            except QbtError as exc:
                self.log.warning("раздачи источников: qBittorrent недоступен: %s", exc)
                busy = True
            except Exception:  # noqa: BLE001 - one bad tick does not end the following of the releases
                self.log.exception("ошибка такта раздач источников")
                busy = True
            try:
                await asyncio.wait_for(self._release_wake.wait(), self._monitor_active if busy else self._monitor_idle)
            except TimeoutError:
                pass
            self._release_wake.clear()

    async def _watch_loop(self) -> None:
        """The watched pages are checked at the times of the schedule (core/schedule.py; the time of day is the subject of
        the wait): a page not checked since the last of them is checked now, so a time missed while the service was down
        is made up at the start. Only the pages of the series in the automatic mode are checked, and only while the
        automatic sync is on: a series switched off is not asked of the tracker by the schedule («Перечитать» still asks).
        A change of the schedule or of the switch wakes the loop to count again."""
        while True:
            self._schedule_wake.clear()
            now = datetime.now(timezone.utc)
            for url in await self.releases.due(last_at(self.releases.checks, now), await self._scheduled_pages()):
                try:
                    await self.releases.check(url)
                except Exception:  # noqa: BLE001 - one page that cannot be read leaves the others checked
                    self.log.exception("проверка обновления %s не удалась", url)
            following = next_at(self.releases.checks, datetime.now(timezone.utc))
            delay = None if following is None else max(0.0, (following - datetime.now(timezone.utc)).total_seconds())
            try:
                await asyncio.wait_for(self._schedule_wake.wait(), delay)
            except TimeoutError:
                pass
