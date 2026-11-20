"""Модуль torrents: единственный собеседник qBittorrent (Р-1) и
торрент-конвейер (Р-14).

Клиентская часть (этап 2): queries/commands поверх QbtClient;
добавление с локальным infohash (Р-2) и различением «уже существует».

Конвейер (этап 4): стадийная машина pipeline.py поверх agent_tasks
(ресьюмабельно: задачи восстанавливаются из БД и сверяются с qBit).
ИНВАРИАНТ ЯДРА — пауза до завершения переименования, см. pipeline.py.

Управление клиентом qBittorrent (публичный контракт модуля; вызывающих внутри системы сейчас нет — их ждёт
интерфейс и другие модули):
  torrents.info.get {hashes?}           → список раздач клиента (все, если hashes не заданы)
  torrents.pause / .resume / .recheck {hashes} — команды клиенту
  torrents.delete {hashes, delete_files?} — убрать раздачи из клиента (файлы — по флагу)
  Клиент создаётся при старте (креды читаются по шине) и делает первый вход: пока этого нет, запрос, которому нужен клиент,
  ждёт его (предохранитель — у топика), а не отвечает «не настроен».

Queries/commands конвейера (контракты Р-12, их ждёт scan):
  torrents.db.active {series_id}        → активные раздачи, сверенные с qBit
  torrents.db.deactivate_all {series_id} → для force_replace
  torrents.register {series_id, torrent, qb_hash, link_type, replaces,
      start_pipeline?}
      — фиксация раздачи по ИДЕНТИЧНОСТИ infohash (qb_hash), не по ярлыку
        с трекера. Тот же infohash уже у серии → ничего не качаем, лишь
        переклеиваем ярлык (ПУНКТ 3). Новый infohash → старую раздачу
        убрать из qBit, прежнюю строку пометить «заменена» (история),
        новую — в конвейер (ПУНКТ 4). replaces.db_id — точная строка для
        деактивации (по id, не по ярлыку: иначе деактивируется не та).
        start_pipeline=false — подготовка состава: метаданные, разбор
        правилами в torrent_files и приоритеты файлов, БЕЗ задачи
        конвейера. Раздача стоит на паузе, пока пользователь отбирает
        серии; дальше её доводит запрос torrents.drive_incomplete
  torrents.queue.get {series_id?}       → {count, tasks}
  torrents.fs.verify {series_id}        → {missing, recheck_started}
      — проверка файлов на диске + recheck-задачи для пропавших
      (вызывается сканом и при открытии модалки — решение Р-13/Р-14)
      A series with no file of the pipeline answers {0, 0} at once.
  torrents.preview.fetch {items: [{torrent_id, magnet?, link?}]}
      — command: the file lists of the releases of the add form, for the
        preview of the naming. Each release ends with the event
        torrents.preview.ready or .failed, both published by this module only;
        there is no reply. The releases go in order: a magnet gets its
        metadata through qBit (added STARTED in the category
        st-naming-preview, files read, torrent deleted; ephemeral, not
        resumed; zombie-safe: cleanup by infohash and a sweep of the category
        in on_start); a link gets its .torrent from sources
        (sources.torrent_file.get, with its cache) and the file list is read
        locally (bencode), without qBit. A release with neither a magnet nor a
        link, or whose .torrent was not got or not parsed, ends with
        preview.failed and the reason.

События: torrents.queue.changed {count, tasks} (контракт SSE
agent_queue_update; count=0 — сигнал scan'у назначить следующий проход),
series.status.contribution {source: torrents, ...} (стадии → флаги по
карте Р-11 + downloading/ready из прогресса — семантика
sync_torrent_statuses), torrents.preview.ready {torrent_id, infohash, name,
files} / torrents.preview.failed {torrent_id, infohash, reason} (→ SSE:
предпросмотр нейминга при добавлении).

Выборочная загрузка. Память исключений живёт в сериале
(series.ignored_seasons + series.ignored_episodes, владелец catalog) и
потому переживает замену раздачи; torrent_files.is_excluded — её
проекция на файлы текущего релиза, из неё считаются приоритеты qBit
(0 — не качать). Качается только распознанное и не исключённое,
единственная точка правды — selection.py. Проекция пересобирается на
изменение памяти (событие series.updated) и на изменение состава
(torrents.db.files.upsert).

Прогресс раздач для интерфейса — таблица torrent_progress (одна строка на infohash; таблица
download_tasks принадлежит downloads, торрентов в ней нет): query torrents.db.progress.list и
событие torrents.progress.changed отдают {tasks: [{id, qb_hash, series_id, series_name, status,
progress, dlspeed, eta, error_message, created_at, updated_at}]}. With them stand the releases of the torrent sources the
keeps, whatever their state (downloading, ready, being updated, failed, archived) — a release is there until its source
goes — one task per series with the page: {kind: "release", qb_hash, url, release, title,
series_id, series_name, status (the state of the release), progress (by the size of the wanted files), files_done,
files_total, error_message} — the monitor of «Агенты» shows every torrent of the client. Both pictures (the queue and the
progress) go again when a window connects (gateway.sse.clients).

The releases of the torrent sources (stage 15, modules/torrents/releases.py; tables torrent_releases,
torrent_release_files, torrent_release_versions), keyed by the page and the id of the release on it, never by a series:
  torrents.releases.get {url} → {releases: [ReleaseView]} — the releases kept for the page (core/contracts/torrent.py)
  torrents.release.download {url, release} → ReleaseView — «Скачать»: the release as the page lists it now
      (sources.releases.get) goes to the client started, category st-source, into <torrents.staging_dir>/<site>-<key>;
      refused (NotAllowed) without the staging folder, for a release the page does not list or that has no .torrent
  torrents.release.check {url} → {releases} — the page is read now: a watched release it lists in another version (another
      infohash, else another date) begins its update — the old version leaves the client without its files, the new one
      comes started into the same folder, the client checks the files there and downloads the rest; while a sync of a
      series with the page runs (scan.run.changed) the update waits for its end. The check sets `checked_at` of every
      watched release of the page. The pages of the series in the automatic mode are also checked at the times of the
      schedule (core/schedule.py) while the automatic sync is on, and a time missed while the service was down at the
      start; «Перечитать» of the source checks its page whatever the mode (trackers.py).
  torrents.release.watch {url, release, watched} → ReleaseView — the automatic update on or off
  torrents.staging.get {} → {value} and torrents.staging.set {value} → {ok, value, reason?} — the folder the releases download
      into (the setting torrents.staging_dir); a value that is no absolute path is refused (`staging_dir_not_absolute`); the
      releases move there on settings.changed; event torrents.staging.changed {value} tells every window the folder held now
  torrents.release.drop_files {url, release} (command) — a release no longer watched gives up its files (archived)
  Event torrents.release.changed ReleaseView — state, version or files changed; `ready` once every wanted file is there
  (an update ends by removing the files of the old version the new one does not list). `client` of the view is what the
  client says of the torrent (qBittorrent's state as is, e.g. `checkingDL`): the client is asked while a release moves or
  is checked, and once in `monitor_idle` otherwise; a change of it is told as the event above. The releases of the pages
  of the torrent sources of a series give the flags of its status (checking, downloading, queued, idle, ready, error,
  metadata) by the same words as its own torrents (pipeline.torrent_flag). A page no source lists any more
  (series.source.removed, series.deleted) leaves the client with its files and its folder. settings.changed
  {key: torrents.staging_dir} moves every release to the new folder (setLocation); {key: schedule} sets the times of the
  checks and tells every release again (its `fresh_until` moved); {key: scanner_agent_enabled} wakes the checks.

Переименование файлов — query renaming.process_torrent (зона renaming);
мониторинг прогресса — адаптивный опрос qBit (часто при активности,
редко в покое), события только при изменениях.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timezone

from core import storage
from core.module import BaseModule, BusRequestError
from core.source_types import torrent_profile_id
from core.topics import (CATALOG_SERIES_GET, CATALOG_SERIES_LIST, SERIES_PURGED, SERIES_STATUS_CONTRIBUTION, SETTINGS_VALUE_GET,
                         SETTINGS_VALUE_SET)
from core.contracts import (RUN_CHANGED, RunChanged, SeriesCard, SeriesDeleted, SeriesPurged, SeriesUpdated, SettingValue,
                            SourceType, StatusContribution)
from core.contracts.torrent import RELEASE_CHANGED_TOPIC, STAGING_DIR_KEY
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason, relayed
from core.retry import UNTIMED, Budget, retrying
from core.schedule import SCHEDULE_KEY, last_at, next_at, parse

from . import pipeline, selection
from .infohash import (TorrentParseError, infohash_candidates,
                       infohash_from_magnet, torrent_file_list)
from .qbt_client import QbtClient, QbtError
from .releases import Releases, status_flag
from .repository import ReleaseStore, TorrentsRepository

# Состояния-ошибки qBittorrent: внутренняя ошибка раздачи и
# недоступные/пропавшие файлы (находка 43). Старая система (и наш
# перенос) считала их «загрузкой» — карточка врала; теперь это флаг
# error. Тексты — для модалки статуса.
_ERROR_STATES = {
    "error": reason("qbt_error"),
    "missingFiles": reason("qbt_files_missing"),
}

# Категория qBit для транзиентных preview-раздач (добор метаданных magnet при
# добавлении сериала). Reconcile при старте сметает всё в ней — бэкстоп от
# осиротевших раздач при краше между add и delete (зомби-безопасность).
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
        self._pipe: dict[str, dict] = {}  # hash -> задача конвейера
        self._wake = asyncio.Event()
        self._flags_cache: dict[int, dict] = {}
        self._names_cache: dict[int, str] = {}  # имена серий для прогресса
        # Предпросмотр нейминга при добавлении: кэш списков файлов по infohash,
        # single-flight (один добор на infohash), кэш дефолтного пути qBit.
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
            ReleaseStore(db), client=lambda: self.qbt, request=lambda topic, payload: self.request(topic, payload),
            publish=self._release_event, wake=self._release_wake.set)
        super().__init__(bus)

    def register(self) -> None:
        self.handle("torrents.add", self.on_add)
        self.handle("torrents.info.get", self.on_info)
        self.handle("torrents.files.get", self.on_files)
        self.handle("torrents.preview.fetch", self.on_preview_fetch,
                    concurrent=True)
        self.handle("torrents.pause", self.on_pause)
        self.handle("torrents.resume", self.on_resume)
        self.handle("torrents.recheck", self.on_recheck)
        self.handle("torrents.delete", self.on_delete)
        self.handle("torrents.rename_file", self.on_rename_file)
        self.handle("torrents.set_location", self.on_set_location)
        self.handle("torrents.db.active", self.on_db_active)
        self.handle("torrents.db.deactivate_all", self.on_db_deactivate_all)
        self.handle("torrents.db.files.list", self.on_db_files_list)
        self.handle("torrents.db.files.upsert", self.on_db_files_upsert)
        self.handle("torrents.register", self.on_register)
        self.handle("torrents.queue.get", self.on_queue_get)
        self.handle("torrents.fs.verify", self.on_fs_verify, concurrent=True)
        self.handle("torrents.drive_incomplete", self.on_drive_incomplete)
        self.handle("torrents.db.history", self.on_db_history)
        self.handle("torrents.db.add", self.on_db_add)
        self.handle("torrents.db.downloaded_counts",
                    self.on_downloaded_counts)
        self.handle("torrents.seasons", self.on_seasons)
        self.handle("torrents.db.files.for_series", self.on_files_for_series)
        self.handle("torrents.db.progress.list", self.on_progress_list)
        self.handle("torrents.composition", self.on_composition,
                    concurrent=True)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("series.updated", self.on_series_updated)
        self.handle("torrents.releases.get", self.on_releases_get)
        self.handle("gateway.sse.clients", self.on_sse_clients)
        self.handle("torrents.release.download", self.on_release_download)
        self.handle("torrents.staging.get", self.on_staging_get)
        self.handle("torrents.staging.set", self.on_staging_set)
        self.handle("torrents.release.check", self.on_release_check)
        self.handle("torrents.release.watch", self.on_release_watch)
        self.handle("torrents.release.drop_files", self.on_release_drop_files)
        self.handle(RUN_CHANGED, self.on_run_changed)
        self.handle("series.source.removed", self.on_source_removed)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("trackerauth.credentials.changed",
                    self.on_credentials_changed)

    async def _resolve_qbt_config(self) -> dict:
        """env имеет приоритет (стенд); без env — креды qbittorrent из
        таблицы auth через шину (прод-сценарий, Р-22)."""
        cfg = dict(self._qbt_config)
        if cfg.get("base_url"):
            return cfg
        try:
            reply = await self.request("trackerauth.credentials.get",
                                       {"service": "qbittorrent"})
        except BusRequestError as exc:
            self.log.warning("креды qBittorrent не получены: %s", exc)
            return cfg
        row = reply.get("credentials") or {}
        return dict(base_url=row.get("url") or "",
                    username=row.get("username") or "",
                    password=row.get("password") or "")

    async def _connect_qbt(self) -> None:
        """Создание клиента и вход; любая ошибка (включая пустой URL —
        креды ещё не настроены) не валит процесс: конвейер ждёт
        (находка 7 — модуль не замораживает и не роняет систему)."""
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
            except Exception as exc:  # noqa: BLE001 — недоступность не фатальна
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

    async def on_credentials_changed(self, env: Envelope) -> None:
        if env.payload.get("service") != "qbittorrent" or self._qbt_injected:
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
        """Сверка БД с реальным qBit при старте (crash-tolerance): если
        торрент есть в БД, но отсутствует в qBit (удалён вручную/пропал),
        запись сбрасывается и серия возвращается в состояние «торрента
        нет» — следующий скан добавит раздачу заново. qBit недоступен —
        пропускаем (не теряем данные на ложном выводе)."""
        try:
            live = await self.qbt.torrents_info()
        except Exception as exc:  # noqa: BLE001 — qBit мог не подняться
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
        """Reconcile (Р-11): задачи из БД — в память (стадия error —
        носитель, в конвейер не возвращается), свёртки по всем сериям."""
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
        existed = verdict == "fails"  # Fails. + найден по hash = уже был
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

    # --- предпросмотр нейминга при добавлении (фоновый добор списка файлов) --------

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
            reply = await self.request(
                "sources.torrent_file.get",
                {"url": link, "torrent_id": tid})
        except (BusRequestError, asyncio.TimeoutError) as exc:
            detail = relayed(str(exc))
            failed = (reason("preview_torrent_download_failed", detail=detail) if detail
                      else reason("preview_torrent_no_answer"))
            self._emit_preview_failed(tid, None, failed)
            return
        self._preview_parse_torrent(tid, reply["content_b64"])

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
        except Exception as exc:  # noqa: BLE001 — предпросмотр не валит систему
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
            if self.qbt is None:
                self._emit_preview_failed(tid, ih, reason("qbt_not_configured"))
                return
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
        except Exception as exc:  # noqa: BLE001 — предпросмотр не валит систему
            self.log.warning("preview magnet %s: %s", ih[:8], exc)
            self._emit_preview_failed(tid, ih, reason("preview_fetch_failed", detail=str(exc)))
        finally:
            if added:
                await self._preview_cleanup(ih)
            self._preview_inflight.discard(ih)

    async def _preview_poll_files(self, ih: str, *, timeout: float,
                                  poll: float = 0.5) -> list[dict] | None:
        """Поллинг /torrents/files до появления метаданных. None — таймаут."""
        async def look() -> list[dict] | None:
            files = await self.qbt.torrent_files(ih)
            return [{"path": f.get("name"), "size": f.get("size")} for f in files] if files else None

        found, _attempts, _exhausted = await retrying(
            look, final=lambda got: got is not None,
            budget=Budget(attempts=int(timeout / poll) + 1, pauses=(poll,), limit_s=timeout))
        return found

    async def _preview_cleanup(self, ih: str) -> None:
        """Уборка preview-раздачи по ИЗВЕСТНОМУ infohash с подтверждением:
        delete повторяется, пока qBit не подтвердит отсутствие (переживает
        асинхронность add и отмену). deleteFiles=true снимает любые частичные
        (контента ≈0 — метаданные приходят до пирсов, но подстраховка)."""
        async def attempt() -> bool | None:
            try:
                await self.qbt.delete([ih], delete_files=True)
                return not await self.qbt.torrents_info([ih])
            except QbtError:
                return None  # qBit недоступен — старт-sweep подметёт по категории

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
        """BACKSTOP зомби-безопасности: смести все раздачи preview-категории при
        старте (осиротевшие при краше между add и delete). qBit недоступен —
        пропускаем (как reconcile, данные не теряем)."""
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
        except Exception as exc:  # noqa: BLE001 — старт модуля не должен падать
            self.log.warning("preview: sweep категории пропущен: %s", exc)

    def _emit_preview_ready(self, tid, ih, data: dict) -> None:
        self.publish_event("torrents.preview.ready", {
            "torrent_id": tid, "infohash": ih,
            "name": data.get("name"), "files": data.get("files") or []})

    def _emit_preview_failed(self, tid, ih, failure: str) -> None:
        self.publish_event("torrents.preview.failed", {
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

    # --- выборочная загрузка -----------------------------------------------------------

    @staticmethod
    def _memory_of(series: SeriesCard) -> tuple[set[int], dict[int, set[int]]]:
        """Память исключений сериала: сезоны и номера серий."""
        try:
            seasons = {int(s) for s in
                       json.loads(series.ignored_seasons or "[]")}
        except (json.JSONDecodeError, TypeError, ValueError):
            seasons = set()
        try:
            episodes = selection.parse_memory(
                json.loads(series.ignored_episodes or "{}"))
        except (json.JSONDecodeError, TypeError):
            episodes = {}
        return seasons, episodes

    async def _project_and_apply(self, series_id: int, *,
                                 allow_resume: bool) -> int:
        """Память сериала -> колонка is_excluded -> приоритеты в qBit."""
        try:
            series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        except BusRequestError as exc:
            self.log.warning("серия %d: память исключений не получена: %s",
                             series_id, exc)
            return 0
        seasons, episodes = self._memory_of(series)
        changed = await self.repo.project_selection(series_id, seasons,
                                                    episodes)
        for qb_hash in await self.repo.active_hashes(series_id):
            await self._apply_selection(qb_hash, allow_resume=allow_resume)
        return changed

    async def _apply_selection(self, qb_hash: str, *,
                               allow_resume: bool) -> None:
        """Единственная точка, где выбор превращается в приоритеты qBit.

        БД остаётся истиной: сбой qBit только логируется — ближайшая
        сверка файлов применит приоритеты заново."""
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
        """Вернули серию в план — qBit докачает её только запущенным.

        Раздачу в конвейере не трогаем: инвариант ядра требует паузы до
        конца переименования."""
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
        """Состав раздачи в БД до закачки: метаданные → правила →
        приоритеты. Торрент всё это время стоит на паузе."""
        if not await self._await_metadata(qb_hash):
            self.log.warning("[%s] метаданные не получены — состав не "
                             "построен", qb_hash[:8])
            return
        await self.request("renaming.process_torrent", {
            "series_id": series_id, "qb_hash": qb_hash, "dry_run": True})
        await self._project_and_apply(series_id, allow_resume=False)

    async def _await_metadata(self, qb_hash: str,
                              attempts: int = 60) -> bool:
        """Дождаться списка файлов. magnet отдаёт метаданные только
        запущенным, поэтому его на это время запускаем и сразу возвращаем
        на паузу — то же исключение из инварианта, что и в конвейере."""
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
        """Память исключений изменилась — пересобрать план загрузки."""
        event = SeriesUpdated.model_validate(env.payload)
        if not ({"ignored_seasons", "ignored_episodes"} & event.model_fields_set):
            return
        await self._project_and_apply(event.series_id, allow_resume=True)
        await self._broadcast_downloaded(event.series_id)
        self.send_command("metadata.seasons.recompute", {"series_id": event.series_id})

    # --- реестр раздач (контракты Р-12) ----------------------------------------------

    async def on_db_active(self, env: Envelope) -> list[dict]:
        """Активные раздачи серии, сверенные с qBittorrent."""
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
        """Записи о файлах раздачи (пишет renaming после переименования —
        таблицей torrent_files владеем мы)."""
        row = await self.repo.torrent_by_hash(env.payload["qb_hash"])
        if not row:
            raise LookupError(reason("torrent_not_in_db", qb_hash=env.payload["qb_hash"][:8]))
        await self.repo.upsert_files(row["id"], env.payload["files"])
        # Состав раздачи сменился (новый релиз, переприменение правил) —
        # проекция памяти восстанавливает выбор пользователя на новых
        # строках до того, как qBit начнёт качать лишнее.
        await self._project_and_apply(row["series_id"], allow_resume=False)
        await self._broadcast_downloaded(row["series_id"])  # Д1
        return {"count": len(env.payload["files"])}

    async def on_register(self, env: Envelope) -> dict:
        """Идентичность раздачи — infohash (qb_hash), не ярлык с трекера.

        ПУНКТ 3: если этот infohash серия уже знает и он здоров — ничего не
        качаем и не дублируем; лишь переклеиваем ярлык/дату (детектор по
        дате, ПУНКТ 1, должен сойтись). ПУНКТ 4: новый infohash → старую
        раздачу обязательно убрать из qBit, прежнюю строку пометить
        «заменена» (история), новую прогнать пайплайном."""
        p = env.payload
        qb_hash, series_id = p["qb_hash"], p["series_id"]
        torrent, replaces = p["torrent"], p.get("replaces")

        # задача этого infohash уже в полёте — ничего не делаем (идемпотентно)
        if qb_hash in self._pipe:
            return {"existed": True}

        link_type = p.get("link_type", "file")
        start_pipeline = bool(p.get("start_pipeline", True))
        task_row = await self.repo.task_by_hash(qb_hash)
        is_error_retry = bool(task_row and task_row["stage"] == pipeline.ERROR)
        known = await self.repo.row_by_hash(series_id, qb_hash)

        if not start_pipeline:
            # Подготовка состава: раздача остаётся на паузе, задача
            # конвейера не создаётся — закачка не начнётся, пока
            # пользователь не отберёт серии и не запустит скан.
            if known:
                await self.repo.refresh_listing(known["id"], torrent, qb_hash)
            else:
                await self.repo.upsert_registered(series_id, torrent, qb_hash)
                await self._retire_replaced(torrent, replaces, qb_hash)
            await self._prepare_composition(series_id, qb_hash)
            return {"existed": bool(known), "prepared": True}

        if known and not is_error_retry:
            # ПУНКТ 3 — но «ничего не делать» ТОЛЬКО если раздача реально
            # докачана и здорова в qB, а не просто известна БД. Удалил из qB
            # и добавил заново / раздача неполна / в ошибке → реконсиляция:
            # обновляем ту же строку (без дубля) и (пере)гоняем конвейер,
            # чтобы раздача докачалась и переименовалась.
            await self.repo.refresh_listing(known["id"], torrent, qb_hash)
            if await self._is_complete_in_qb(qb_hash):
                return {"existed": True}
            await self._enter_pipeline(qb_hash, series_id, torrent,
                                       link_type, "None")
            return {"existed": False}

        # ПУНКТ 4 (или перезапуск после ошибки): новый infohash → активной.
        await self.repo.upsert_registered(series_id, torrent, qb_hash)

        old_torrent_id = await self._retire_replaced(torrent, replaces,
                                                     qb_hash)
        await self._enter_pipeline(qb_hash, series_id, torrent, link_type,
                                   old_torrent_id)
        return {"existed": False}

    async def _retire_replaced(self, torrent: dict, replaces: dict | None,
                               qb_hash: str) -> str:
        """ПУНКТ 4: прежнюю раздачу — в историю, из qBit убрать."""
        if not replaces or replaces.get("torrent_id") == torrent["torrent_id"]:
            return "None"
        old_torrent_id = replaces.get("torrent_id") or "None"
        old_db_id = replaces.get("db_id")
        if old_db_id is not None:
            # точная строка по db_id (история сохраняется, файлы снимаются)
            await self.repo.deactivate_and_clear_files(old_db_id)
        old_hash = replaces.get("qb_hash")
        if old_hash and old_hash != qb_hash:
            # новый infohash → старую раздачу убрать из qBit (обязательно)
            try:
                await self.qbt.delete([old_hash], delete_files=False)
            except QbtError as exc:
                self.log.warning("замена: старый торрент %s не удалён "
                                 "из qBit: %s", old_hash[:8], exc)
        return old_torrent_id

    async def _is_complete_in_qb(self, qb_hash: str) -> bool:
        """Раздача реально докачана и здорова в qBit (а не просто известна
        БД): только тогда повторная регистрация — no-op (ПУНКТ 3). qBit
        недоступен / раздачи нет / прогресс <100 / ошибка → не докачана,
        нужна реконсиляция конвейером."""
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
        """Раздача в qB НА ПАУЗЕ и не докачана — застряла (конвейер поставил
        паузу и не возобновил). Качающиеся (running) и ждущие пиров
        (stalledDL) не застряли — их не трогаем."""
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
        """Поставить задачу конвейера для infohash и разбудить его."""
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
        """Вся история раздач серии (контракт GET torrents/history)."""
        return await self.repo.history(env.payload["series_id"])

    async def on_files_for_series(self, env: Envelope) -> list[dict]:
        """Файлы всех раздач серии (sourcefile-список, композиция)."""
        return await self.repo.files_for_series(env.payload["series_id"])

    async def on_composition(self, env: Envelope) -> list[dict]:
        """Контракт GET composition для торрент-серий (Р-21): свежие
        правила + предпросмотр имени + наличие файла на диске."""
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
                # как в оригинале: без профиля или для не-видео — сохранённые метаданные
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
            reply = await self.request("rules.format_torrent_file", {
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
        """Файлы раздачи, которых нет в реестре: правила их не разобрали,
        качаться они не будут — пользователь должен это видеть, а не
        гадать, куда делась половина раздачи."""
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
        """Раздачи формы добавления серии — только реестр в БД, без qBit
        (семантика старого add_series: конвейер запустит скан)."""
        added = await self.repo.add_registered_rows(
            env.payload["series_id"], env.payload["torrents"])
        return {"added": added}

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}} — переименованные файлы (Р-19)."""
        return {"counts": await self.repo.downloaded_counts()}

    async def on_seasons(self, env: Envelope) -> dict:
        """{seasons: [..]} — реальные сезоны серии из нейминга (для агрегата
        TMDB по нескольким сезонам)."""
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
                self.send_command("sources.torrent_file.drop",
                                  {"torrent_id": r["torrent_id"]})
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
        """Файлы на диске: 100%-скачанный renamed без файла → missing →
        recheck-задача (восстановление силами qBit). Вызывается при
        открытии окна сериала (вместо фоновой проверки раз в 60 с)."""
        series_id = env.payload["series_id"]
        series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
        files = await self.repo.files_for_series(series_id)
        # A series with no file of the old pipeline has nothing to check or to warn about here.
        if not files:
            return {"missing": 0, "recheck_started": 0}
        # F7: путь/NAS недоступен → НЕ помечать всё ложно missing и не
        # запускать массовый recheck. Пропускаем сверку до восстановления.
        if not await storage.call(os.path.exists, series.save_path):
            self.log.warning("серия %d: путь %s недоступен — сверка файлов "
                             "пропущена", series_id, series.save_path)
            return {"missing": 0, "recheck_started": 0}
        missing_hashes: set[str] = set()
        missing = 0
        for f in files:
            # Исключённого файла на диске нет по замыслу: без этой ветки
            # он на каждом скане уезжал бы в missing и тянул recheck.
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
        # Точка самолечения: приоритеты в qBit могли разойтись с БД
        # (клиент был недоступен, раздачу правили руками).
        await self._project_and_apply(series_id, allow_resume=False)
        await self._broadcast_downloaded(series_id)  # Д1: missing меняет счёт
        return {"missing": missing, "recheck_started": started}

    async def on_drive_incomplete(self, env: Envelope) -> dict:
        """Реконсиляция застрявших раздач (ситуация P10): активная раздача,
        которая ЕСТЬ в qB, но не докачана и БЕЗ задачи конвейера (задача
        потеряна / не доведена), — загоняется в конвейер заново. Здоровые
        (докачанные) и уже ведомые/ошибочные (есть задача) не трогаем.
        Зовётся сканом на каждом проходе — восстанавливает самолечение
        легаси, но точечно."""
        series_id = env.payload["series_id"]
        driven = 0
        for row in await self.repo.active_torrents(series_id):
            qb_hash = row.get("qb_hash")
            if not qb_hash or qb_hash in self._pipe:
                continue
            if await self.repo.task_by_hash(qb_hash):
                continue  # уже есть задача (в т.ч. носитель error) — не трогаем
            if not await self._is_stuck_in_qb(qb_hash):
                continue  # качается / ждёт пиров / докачана — не застряла
            torrent = {"torrent_id": row["torrent_id"], "link": row.get("link"),
                       "magnet": None, "date_time": row.get("date_time"),
                       "quality": row.get("quality"),
                       "episodes": row.get("episodes")}
            # раздача уже в qB с метаданными → входная стадия как у file
            await self._enter_pipeline(qb_hash, series_id, torrent, "file",
                                       "None")
            driven += 1
        if driven:
            self.log.info("серия %d: загнано в конвейер застрявших раздач: "
                          "%d", series_id, driven)
        return {"driven": driven}

    # --- конвейер ---------------------------------------------------------------------

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
                await self.request("renaming.process_torrent", {
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
        except Exception as exc:  # noqa: BLE001 — ошибка не валит конвейер
            self.log.exception("[%s] ошибка на стадии '%s'", qb_hash[:8],
                               task["stage"])
            await self.repo.set_stage(qb_hash, pipeline.ERROR)
            del self._pipe[qb_hash]
            self._broadcast_queue()
            await self._contribute(task["series_id"])

    async def _complete(self, qb_hash: str, task: dict) -> None:
        await self.repo.delete_task(qb_hash)
        del self._pipe[qb_hash]
        self.send_command("catalog.series.touch_scan_time",
                          {"series_id": task["series_id"]})
        # Переименование разложило файлы по Season NN → реальные сезоны
        # известны: пересчитать многосезонный агрегат TMDB.
        self.send_command("metadata.seasons.recompute",
                          {"series_id": task["series_id"]})
        self.log.info("[%s] задача конвейера выполнена", qb_hash[:8])
        self._broadcast_queue()
        await self._contribute(task["series_id"])

    async def _drop_task(self, qb_hash: str, series_id: int) -> None:
        # раздача исчезла из qB — деактивируем строку и снимаем файло-записи
        # (симметрия с reconcile при старте, R3): серия возвращается к
        # «торрента нет», следующий скан добавит заново. Иначе осталась бы
        # фантомная is_active=1 без живой раздачи (ложные счётчики).
        row = await self.repo.torrent_by_hash(qb_hash)
        was_active = bool(row and row["is_active"])
        await self.repo.drop_task_and_row(qb_hash, row["id"] if was_active else None)
        self._pipe.pop(qb_hash, None)
        if was_active:
            await self._broadcast_downloaded(series_id)  # Д1: счёт упал
        self._broadcast_queue()
        await self._contribute(series_id)

    def _broadcast_queue(self) -> None:
        tasks = list(self._pipe.values())
        self.publish_event("torrents.queue.changed",
                           {"count": len(tasks), "tasks": tasks})

    async def _broadcast_downloaded(self, series_id: int) -> None:
        """Состав скачанных файлов серии изменился → толкнуть счётчик в SSE
        (Д1): карточка обновляет «скачано» сразу, без перезагрузки."""
        count = await self.repo.downloaded_count_for_series(series_id)
        self.publish_event("series.downloaded.changed",
                           {"series_id": series_id, "count": count})

    # --- мониторинг прогресса -----------------------------------------------------------

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

    # --- прогресс торрентов для UI (Р-23: вместо поллинга вкладки) ---------------------

    async def _progress_tasks(self) -> list[dict]:
        tasks = await self.repo.all_torrent_progress()
        for t in tasks:
            t["series_name"] = await self._series_name(t["series_id"])
        return tasks + await self._release_tasks()

    async def _release_tasks(self) -> list[dict]:
        """Every release the module keeps, one task per series whose torrent source is the page: it is there until its source
        goes, whatever its state."""
        rows = await self.releases.store.all()
        if not rows:
            return []
        series_of: dict[str, list[SeriesCard]] = {}
        for series in (await self.reply_of(CATALOG_SERIES_LIST, {})).series:
            for source in series.sources:
                if source.source_type == SourceType.TORRENT:
                    series_of.setdefault(await self._canonical(source.url), []).append(series)
        tasks = []
        for row in rows:
            view = await self.releases.view(row)
            wanted = [f for f in view.files if f.priority > 0]
            size = sum(f.size for f in wanted)
            progress = round(100 * sum(f.size * f.progress for f in wanted) / size, 1) if size else 0.0
            for series_id, series_name in [(each.id, each.name) for each in series_of.get(view.url, ())] or [(None, "")]:
                tasks.append({"kind": "release", "qb_hash": view.infohash or "", "url": view.url, "release": view.release,
                              "title": view.title, "series_id": series_id, "series_name": series_name or "",
                              "status": view.state.value, "progress": progress,
                              "files_done": sum(1 for f in wanted if f.progress >= 1), "files_total": len(wanted),
                              "error_message": view.reason})
        return tasks

    async def _series_name(self, series_id: int) -> str:
        if series_id not in self._names_cache:
            try:
                series = await self.reply_of(CATALOG_SERIES_GET, {"series_id": series_id})
                self._names_cache[series_id] = series.name or ""
            except BusRequestError:
                return ""
        return self._names_cache[series_id]

    async def on_progress_list(self, env: Envelope) -> dict:
        """Контракт старого GET /api/series/active_torrents."""
        return {"tasks": await self._progress_tasks()}

    async def on_sse_clients(self, env: Envelope) -> None:
        """A window that connects gets the pictures of the torrents at once, as those of the other queues: it does not wait
        for the next change or tick to see what the client holds."""
        if not env.payload.get("count"):
            return
        self._broadcast_queue()
        self._progress_snapshot = None
        await self._broadcast_progress()

    async def _broadcast_progress(self) -> None:
        """torrents.progress.changed — только при реальных изменениях
        (SSE torrent_progress_update; заменяет 5-секундный поллинг
        вкладки «Агенты», находка 40)."""
        tasks = await self._progress_tasks()
        snapshot = json.dumps(tasks, sort_keys=True, default=str)
        if snapshot == self._progress_snapshot:
            return
        self._progress_snapshot = snapshot
        self.publish_event("torrents.progress.changed", {"tasks": tasks})

    # --- свёртка статусов (Р-11) --------------------------------------------------------

    async def _contribute(self, series_id: int) -> None:
        flags = dict.fromkeys(
            ("metadata", "renaming", "checking", "activating", "error",
             "downloading", "idle", "queued", "ready"), False)
        for task in await self.repo.tasks_for_series(series_id):
            flag = pipeline.STAGE_FLAGS.get(task["stage"])
            if flag:
                flags[flag] = True
        # Раздача, где не нужен ни один файл, отдаёт progress=100 (качать
        # нечего) — «готов» по ней был бы враньём.
        fully_excluded = await self.repo.fully_excluded_hashes(series_id)
        for row in await self.repo.torrent_progress(series_id):
            if row["status"] in _ERROR_STATES:
                flags["error"] = True
            elif row["qb_hash"] in fully_excluded:
                continue
            elif flag := pipeline.torrent_flag(row["status"], row["progress"] >= 100):
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

    def _release_event(self, topic: str, payload: dict) -> None:
        """A release told of: the window is told, and the series whose source is its page recount their status."""
        self.publish_event(topic, payload)
        if topic == RELEASE_CHANGED_TOPIC:
            self.spawn(self._contribute_page(payload["url"]), name=f"torrents.release.contribute:{payload['url']}")

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
            runs = (await self.request("scan.runs.get", {})).get("runs") or []
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
        resolved = await self.request("sources.site.resolve", {"url": url})
        return resolved.get("canonical_url") or url

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

    async def on_releases_get(self, env: Envelope) -> dict:
        """{url} → {releases: [ReleaseView]}: the releases kept for the page."""
        url = await self._canonical(env.payload["url"])
        return {"releases": [view.model_dump(mode="json") for view in await self.releases.of_page(url)]}

    async def on_release_download(self, env: Envelope) -> dict:
        """{url, release} → ReleaseView: «Скачать» — the release goes to the client; a refusal is NotAllowed with its reason."""
        await self._known()
        return (await self.releases.download(await self._canonical(env.payload["url"]), env.payload["release"])
                ).model_dump(mode="json")

    async def on_release_check(self, env: Envelope) -> dict:
        """{url} → {releases}: the page is read now; the watched releases it lists newer begin their update or wait."""
        await self._known()
        return {"releases": await self.releases.check(await self._canonical(env.payload["url"]))}

    async def on_release_watch(self, env: Envelope) -> dict:
        await self._known()
        url = await self._canonical(env.payload["url"])
        return (await self.releases.watch(url, env.payload["release"], bool(env.payload["watched"]))).model_dump(mode="json")

    async def on_release_drop_files(self, env: Envelope) -> None:
        await self._known()
        url = await self._canonical(env.payload["url"])
        await self.releases.drop_files(url, env.payload["release"])

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

    async def on_staging_get(self, env: Envelope) -> dict:
        return {"value": (await self.reply_of(SETTINGS_VALUE_GET, {"key": STAGING_DIR_KEY})).value or ""}

    async def on_staging_set(self, env: Envelope) -> dict:
        """The folder of the releases, as the settings of the sources set it: an absolute path, the same for the client."""
        value = str(env.payload.get("value") or "").strip()
        if not os.path.isabs(value):
            return {"ok": False, "value": value, "reason": reason("staging_dir_not_absolute")}
        await self.request(SETTINGS_VALUE_SET, {"key": STAGING_DIR_KEY, "value": value})
        return {"ok": True, "value": value}

    async def on_settings_changed(self, env: Envelope) -> None:
        setting = SettingValue.model_validate(env.payload)
        if setting.key == STAGING_DIR_KEY:
            # Every open window of the settings shows the folder the core holds now, whichever window set it.
            self.publish_event("torrents.staging.changed", {"value": setting.value or ""})
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
