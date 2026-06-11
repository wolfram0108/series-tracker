"""Модуль torrents: единственный собеседник qBittorrent (Р-1) и
торрент-конвейер (Р-14).

Клиентская часть (этап 2): queries/commands поверх QbtClient;
добавление с локальным infohash (Р-2) и различением «уже существует».

Конвейер (этап 4): стадийная машина pipeline.py поверх agent_tasks
(ресьюмабельно: задачи восстанавливаются из БД и сверяются с qBit).
ИНВАРИАНТ ЯДРА — пауза до завершения переименования, см. pipeline.py.

Queries/commands конвейера (контракты Р-12, их ждёт scan):
  torrents.db.active {series_id}        → активные раздачи, сверенные с qBit
  torrents.db.deactivate_all {series_id} → для force_replace
  torrents.register {series_id, torrent, qb_hash, link_type, replaces}
      — идемпотентная фиксация раздачи + замена старой + задача конвейера
  torrents.queue.get {series_id?}       → {count, tasks}
  torrents.fs.verify {series_id}        → {missing, recheck_started}
      — проверка файлов на диске + recheck-задачи для пропавших
      (вызывается сканом и при открытии модалки — решение Р-13/Р-14)

События: torrents.queue.changed {count, tasks} (контракт SSE
agent_queue_update; count=0 — сигнал scan'у назначить следующий проход),
series.status.contribution {source: torrents, ...} (стадии → флаги по
карте Р-11 + downloading/ready из прогресса — семантика
sync_torrent_statuses).

Переименование файлов — query renaming.process_torrent (зона renaming);
мониторинг прогресса — адаптивный опрос qBit (часто при активности,
редко в покое), события только при изменениях.
"""
from __future__ import annotations

import asyncio
import base64
import os

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from . import pipeline
from .infohash import (TorrentParseError, infohash_candidates,
                       infohash_from_magnet)
from .qbt_client import QbtClient, QbtError
from .repository import TorrentsRepository

# state-исключения для downloading (старый sync_torrent_statuses +
# stopped*-имена поколения qBit 5.x)
_NOT_DOWNLOADING = {"pausedUP", "pausedDL", "stoppedUP", "stoppedDL",
                    "uploading"}


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
        self._pipe: dict[str, dict] = {}  # hash -> задача конвейера
        self._wake = asyncio.Event()
        self._flags_cache: dict[int, dict] = {}
        self._has_incomplete = False
        self._pipeline_poll = pipeline_poll
        self._monitor_active = monitor_active
        self._monitor_idle = monitor_idle
        super().__init__(bus)

    def register(self) -> None:
        self.handle("torrents.add", self.on_add)
        self.handle("torrents.info.get", self.on_info)
        self.handle("torrents.files.get", self.on_files)
        self.handle("torrents.pause", self.on_pause)
        self.handle("torrents.resume", self.on_resume)
        self.handle("torrents.recheck", self.on_recheck)
        self.handle("torrents.delete", self.on_delete)
        self.handle("torrents.rename_file", self.on_rename_file)
        self.handle("torrents.set_location", self.on_set_location)
        self.handle("torrents.db.active", self.on_db_active)
        self.handle("torrents.db.deactivate_all", self.on_db_deactivate_all)
        self.handle("torrents.register", self.on_register)
        self.handle("torrents.queue.get", self.on_queue_get)
        self.handle("torrents.fs.verify", self.on_fs_verify, concurrent=True)

    async def on_start(self) -> None:
        if not self._qbt_injected:
            self.qbt = QbtClient(**self._qbt_config)
            try:
                await self.qbt.login()
            except QbtError as exc:
                self.log.error("qBittorrent недоступен при старте: %s — "
                               "конвейер будет ждать", exc)
        await self._recover_tasks()
        self._tasks.append(asyncio.create_task(self._pipeline_loop()))
        self._tasks.append(asyncio.create_task(self._monitor_loop()))

    async def on_stop(self) -> None:
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
            raise TorrentParseError("нужен magnet или content_b64")

        torrent_hash = await self.qbt.find_hash(candidates)
        if torrent_hash is None:
            raise RuntimeError(
                f"торрент не появился в qBittorrent после добавления "
                f"(ответ API: {verdict}, кандидаты: {candidates})")
        existed = verdict == "fails"  # Fails. + найден по hash = уже был
        if existed:
            self.log.info("торрент %s уже существовал в qBittorrent",
                          torrent_hash[:8])
        return {"hash": torrent_hash, "link_type": link_type,
                "existed": existed}

    async def on_info(self, env: Envelope) -> list[dict]:
        return await self.qbt.torrents_info(
            (env.payload or {}).get("hashes"))

    async def on_files(self, env: Envelope) -> list[dict] | None:
        return await self.qbt.torrent_files(env.payload["hash"])

    # --- commands ------------------------------------------------------------------

    async def on_pause(self, env: Envelope) -> None:
        await self.qbt.pause(env.payload["hashes"])

    async def on_resume(self, env: Envelope) -> None:
        await self.qbt.resume(env.payload["hashes"])

    async def on_recheck(self, env: Envelope) -> None:
        await self.qbt.recheck(env.payload["hashes"])

    async def on_delete(self, env: Envelope) -> None:
        await self.qbt.delete(env.payload["hashes"],
                              delete_files=bool(
                                  env.payload.get("delete_files", False)))

    async def on_rename_file(self, env: Envelope) -> None:
        p = env.payload
        await self.qbt.rename_file(p["hash"], p["old_path"], p["new_path"])

    async def on_set_location(self, env: Envelope) -> None:
        await self.qbt.set_location(env.payload["hash"],
                                    env.payload["location"])

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

    async def on_register(self, env: Envelope) -> dict:
        p = env.payload
        qb_hash, series_id = p["qb_hash"], p["series_id"]
        torrent, replaces = p["torrent"], p.get("replaces")

        if qb_hash in self._pipe:
            return {"existed": True}
        await self.repo.upsert_registered(series_id, torrent, qb_hash)

        old_torrent_id = "None"
        if replaces and replaces["torrent_id"] != torrent["torrent_id"]:
            old_torrent_id = replaces["torrent_id"]
            await self.repo.deactivate_and_clear_files(old_torrent_id)
            old_hash = replaces.get("qb_hash")
            if old_hash and old_hash != qb_hash:
                try:
                    await self.qbt.delete([old_hash], delete_files=False)
                except QbtError as exc:
                    self.log.warning("замена: старый торрент %s не удалён "
                                     "из qBit: %s", old_hash[:8], exc)

        task = {"torrent_hash": qb_hash, "series_id": series_id,
                "torrent_id": torrent["torrent_id"],
                "old_torrent_id": old_torrent_id,
                "stage": pipeline.INITIAL_STAGE[p.get("link_type", "file")]}
        await self.repo.upsert_task(task)
        self._pipe[qb_hash] = {**task, "recheck_initiated": False}
        self._wake.set()
        self._broadcast_queue()
        await self._contribute(series_id)
        return {"existed": False}

    async def on_queue_get(self, env: Envelope) -> dict:
        series_id = (env.payload or {}).get("series_id")
        tasks = [t for t in self._pipe.values()
                 if series_id is None or t["series_id"] == series_id]
        return {"count": len(tasks), "tasks": tasks}

    async def on_fs_verify(self, env: Envelope) -> dict:
        """Файлы на диске: 100%-скачанный renamed без файла → missing →
        recheck-задача (восстановление силами qBit). Вызывается сканом
        и при открытии модалки (вместо фоновой проверки раз в 60 с)."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        missing_hashes: set[str] = set()
        missing = 0
        for f in await self.repo.files_for_series(series_id):
            if f.get("progress", 0) < 100 or not f.get("renamed_path"):
                continue
            path = os.path.join(series["save_path"], f["renamed_path"])
            exists = await asyncio.to_thread(os.path.exists, path)
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
        return {"missing": missing, "recheck_started": started}

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
                    "series_id": task["series_id"], "qb_hash": qb_hash},
                    timeout=300)
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
        self.log.info("[%s] задача конвейера выполнена", qb_hash[:8])
        self._broadcast_queue()
        await self._contribute(task["series_id"])

    async def _drop_task(self, qb_hash: str, series_id: int) -> None:
        await self.repo.delete_task(qb_hash)
        self._pipe.pop(qb_hash, None)
        self._broadcast_queue()
        await self._contribute(series_id)

    def _broadcast_queue(self) -> None:
        tasks = list(self._pipe.values())
        self.publish_event("torrents.queue.changed",
                           {"count": len(tasks), "tasks": tasks})

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
                    await self.repo.upsert_progress(series_id, qb_hash,
                                                    info_map[qb_hash])
            await self.repo.remove_stale_progress(
                series_id, [h for h in hashes if h in info_map])
            await self._contribute(series_id)

    # --- свёртка статусов (Р-11) --------------------------------------------------------

    async def _contribute(self, series_id: int) -> None:
        flags = dict.fromkeys(
            ("metadata", "renaming", "checking", "activating", "error",
             "downloading", "ready"), False)
        for task in await self.repo.tasks_for_series(series_id):
            flag = pipeline.STAGE_FLAGS.get(task["stage"])
            if flag:
                flags[flag] = True
        for row in await self.repo.torrent_progress(series_id):
            if row["progress"] >= 100:
                flags["ready"] = True
            elif row["status"] not in _NOT_DOWNLOADING:
                flags["downloading"] = True
        if self._flags_cache.get(series_id) == flags:
            return
        self._flags_cache[series_id] = flags
        self.publish_event("series.status.contribution", {
            "source": "torrents", "series_id": series_id, "flags": flags})
