"""Модуль downloads — загрузка VK-видео (yt-dlp). Решение Р-13.

Владение: download_tasks (vk-часть) + колонки media_items.status и
final_filename.

События (входящие):
  scan.plan.updated {series_id} — план пересчитан: ТОЛЬКО усыновление
      готовых на диске файлов + чистка задач без строки плана (задача без
      строки media_items останавливается в любом состоянии вместе с
      недокачанным файлом; ждущая или упавшая, чья строка вышла из плана,
      снимается; идущая, чья строка осталась, доканчивается) + снос файлов,
      вытесненных победителем слота. Загрузку НЕ запускает (инвариант:
      усыновление ≠ загрузка; открытие композиции и сохранение свойств не
      должны качать).
  settings.changed — подписка на max_parallel_downloads (вместо
      перечитывания каждые 5 с).

Queries / commands:
  downloads.dispatch {series_id} — ПРИКАЗ скана докачать недостающее:
      усыновление + создание задач на планово-ожидаемое, чего НЕТ на
      диске, + пинок диспетчеру. ЕДИНСТВЕННАЯ точка старта загрузки
      (шлёт только scan._run_series по явному скану/автоскану).
  downloads.queue.get {} → {tasks, count} — активные задачи (загрузка UI)
  downloads.episodes.get {series_id} → {items: [{unique_id, season, episode_start,
      source_url, status, final_filename, superseded_by}]} — the reference episodes of the plan (read
      by voices); superseded_by of a displaced row names the winner of the slot
  downloads.queue.clear {} → {deleted} — всё, кроме идущих загрузок
  downloads.fs.sync {series_id} → {adopted, lost} — синхронизация с
      диском (вызывается сканом и при открытии модалки статуса). A series
      of kind `torrent` answers {0, 0} at once: nothing is read, published
      or logged for it.

События (исходящие):
  downloads.item.renamed {series_id, unique_id, filename} — the file name of an episode changed
      (renaming reports it by downloads.item.set_filename); modules that keep files next to the
      video (voices) follow.
  downloads.queue.changed {tasks, count} — при реальных изменениях
      (троттлинг на прогрессе) — контракт SSE download_queue_update.
  series.status.contribution {source: downloads, ...} — свёртка Р-11
      (downloading/error/ready/waiting — семантика sync_vk_statuses).

Один файл на слот эпизода: строка с media_items.superseded_by (его пишет
scan вместе с планом) теряет файл, как только файл победителя скачан и лежит
на диске. Строка остаётся (final_filename=NULL) — память о скачанном
разрешении; без победителя на диске не удаляется ничего. Сносит
_retire_superseded: после загрузки, усыновления, диспетчеризации и сверки.

Диспетчер живёт по событиям (создание задачи, завершение загрузки,
смена лимита) — никаких тиков. Параллелизм — счётчик активных задач
против лимита из настроек.

A general "stream to file" download (voice-over mission):
  downloads.fetch {key, url, path, max_height?, series_id?} — download the stream behind
      the link to an absolute path without a record in media_items or download_tasks
      (the voice-overs of a series go to a temporary directory). The executor keeps no state:
      the owner of the queue (voices) sends the command again after a restart.
  downloads.fetch.cancel {series_id?, key?} — drop the queue and the running downloads of a series, or one job by its key
events downloads.fetch.started {key, series_id, path} (the download left the queue and began) and
downloads.fetch.finished {key, ok, error, transient, path, series_id}.
It has a limit of its own, the setting max_parallel_dub_downloads (2 by default),
independent of the queue of the library.

The stream of a candidate is resolved by sources.video.stream only; this module knows no site. When that
request fails, yt-dlp may take the link over only if the candidate belongs to a vk_video source of its
series (media_items.source_id -> catalog.series.get sources); a web_video candidate fails with the error,
since yt-dlp would scrape a foreign player. A row without source_id (earlier versions) takes the type of
the only source of the series, and is a web_video one when the series has several.
"""
from __future__ import annotations

import asyncio
import os
import time

from core import BaseModule, BusRequestError, storage
from core.contracts import SeriesKind, SourceType
from core.db import Database
from core.envelope import Envelope
from core.retry import UNTIMED, Budget, retrying

from . import ytdlp
from .repository import DownloadsRepository

PROGRESS_DB_THROTTLE = 2.0   # секунд между записями прогресса в БД
PROGRESS_EVENT_THROTTLE = 1.0  # секунд между событиями очереди при прогрессе
MAX_DOWNLOAD_ATTEMPTS = 3
RETRY_DELAY_S = 5.0
STREAM_RESOLVE_TIMEOUT_S = 120  # a Vibix playlist may need a browser capture first
FETCH_LIMIT_KEY = "max_parallel_dub_downloads"
_TRANSIENT_MARKERS = ("timed out", "timeout", "connection", "network", "обрыв", "input/output error",
                      "temporarily", "server returned 5", "server returned 429",
                      "server returned 403", "http error 403", "http error 408",
                      "http error 429", "http error 5")


def _inside(base: str, path: str) -> bool:
    root = os.path.realpath(base)
    return os.path.commonpath([root, os.path.realpath(path)]) == root


async def _never() -> bool:
    """The answer of a job that has no candidate behind it: yt-dlp is never the way out of a failed request."""
    return False


def source_of_candidate(sources: list[dict], source_id: int | None) -> dict | None:
    """The source a candidate was found by; None when it is not known and not the only one of the series.

    A row of an earlier version has no source_id: the only source of its series is its source. A source_id that
    no source of the series has any more belongs to a removed source, so it is not guessed."""
    if source_id is not None:
        return next((source for source in sources if source["id"] == source_id), None)
    return sources[0] if len(sources) == 1 else None


def _transient_download_error(error: str) -> bool:
    lowered = (error or "").lower()
    return any(marker in lowered for marker in _TRANSIENT_MARKERS)


def _download_budget() -> Budget:
    """The one budget of a whole download, whether its attempts live in a loop or in the queue."""
    return Budget(attempts=MAX_DOWNLOAD_ATTEMPTS, pauses=(RETRY_DELAY_S,), limit_s=UNTIMED)


class DownloadsModule(BaseModule):
    name = "downloads"

    def __init__(self, bus, db: Database, *, downloader=ytdlp.download) -> None:
        self.repo = DownloadsRepository(db)
        self._download = downloader  # подменяется в тестах
        self._limit = 2               # параллельных файлов (max_parallel_downloads)
        self._fetch_limit = 2         # parallel general downloads (downloads.fetch)
        self._fetch_queue: list[dict] = []
        self._fetch_active: dict[str, tuple[asyncio.Task, int | None]] = {}
        self._threads = 6             # фрагментов на файл (yt-dlp -N)
        self._active: dict[int, asyncio.Task] = {}
        self._last_db_write: dict[int, float] = {}
        self._last_event = 0.0
        super().__init__(bus)

    def register(self) -> None:
        self.handle("scan.plan.updated", self.on_plan_updated)
        self.handle("downloads.dispatch", self.on_dispatch)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("downloads.queue.get", self.on_queue_get)
        self.handle("downloads.queue.clear", self.on_queue_clear)
        self.handle("downloads.cancel", self.on_cancel)
        self.handle("downloads.episodes.get", self.on_episodes_get)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle("downloads.fetch.cancel", self.on_fetch_cancel)
        self.handle("downloads.fs.sync", self.on_fs_sync)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        self._limit = await self._read_limit()
        self._threads = await self._read_threads()
        self._fetch_limit = await self._read_fetch_limit()
        requeued = await self.repo.requeue_interrupted()
        if requeued:
            self.log.info("оборванных загрузок возвращено в очередь: %d",
                          requeued)
        # Свёртки при старте (обязательство поставщика, Р-11).
        try:
            all_series = await self.request("catalog.series.list", {},
                                            timeout=30)
            for s in all_series:
                if s.get("kind") == SeriesKind.VIDEO:
                    await self._contribute(s["id"])
        except BusRequestError as exc:
            self.log.warning("каталог недоступен при старте: %s", exc)
        await self._pump()

    async def _read_limit(self) -> int:
        try:
            reply = await self.request("settings.value.get",
                                       {"key": "max_parallel_downloads"},
                                       timeout=10)
            return max(1, int(reply.get("value") or 2))
        except (BusRequestError, ValueError, TypeError):
            return 2

    async def _read_fetch_limit(self) -> int:
        try:
            reply = await self.request("settings.value.get", {"key": FETCH_LIMIT_KEY}, timeout=10)
            return max(1, int(reply.get("value") or 2))
        except (BusRequestError, ValueError, TypeError):
            return 2

    async def _read_threads(self) -> int:
        """Число параллельных фрагментов yt-dlp (-N). Прирост скорости
        идёт только в связке hls+-N (замер на стенде)."""
        try:
            reply = await self.request("settings.value.get",
                                       {"key": "yt_dlp_concurrent_fragments"},
                                       timeout=10)
            return max(1, int(reply.get("value") or 6))
        except (BusRequestError, ValueError, TypeError):
            return 6

    # --- реакции на события -------------------------------------------------------

    async def on_settings_changed(self, env: Envelope) -> None:
        key = env.payload.get("key")
        if key == "yt_dlp_concurrent_fragments":
            try:
                self._threads = max(1, int(env.payload.get("value")))
                self.log.info("параллельных фрагментов (-N): %d",
                              self._threads)
            except (ValueError, TypeError):
                pass
            return
        if key == FETCH_LIMIT_KEY:
            try:
                self._fetch_limit = max(1, int(env.payload.get("value")))
            except (ValueError, TypeError):
                return
            self._fetch_pump()
            return
        if key != "max_parallel_downloads":
            return
        try:
            self._limit = max(1, int(env.payload.get("value")))
        except (ValueError, TypeError):
            return
        self.log.info("лимит параллельных загрузок: %d", self._limit)
        await self._pump()

    async def on_plan_updated(self, env: Envelope) -> None:
        """План изменился (скан-скрейп / открытие композиции / смена
        игнора): УСЫНОВЛЕНИЕ существующих на диске файлов + чистка pending
        вне плана. Загрузку НЕ запускает — её стартует ТОЛЬКО downloads.
        dispatch по явному скану (инвариант: усыновление ≠ загрузка)."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        adopted = await self._adopt_existing_files(series)
        dropped = await self._drop_tasks_without_plan_row(series_id)
        retired = await self._retire_superseded(series)
        self.log.info("план %d: усыновлено %d, вычищено вне плана %d, "
                      "вытесненных файлов снесено %d",
                      series_id, adopted, dropped, retired)
        if adopted:
            await self._contribute(series_id)
        if dropped:
            await self._broadcast_queue()

    async def on_dispatch(self, env: Envelope) -> None:
        """Явная диспетчеризация загрузок (приказ скана, downloads.dispatch):
        сперва усыновить уже лежащее на диске, затем создать задачи только
        на планово-ожидаемое, чего на диске НЕТ, и пнуть пул. ЕДИНСТВЕННАЯ
        точка, где стартует загрузка VK-видео."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        base_path = series.get("save_path") or ""
        if not os.path.isabs(base_path):
            self.log.error("серия %d: путь сохранения не абсолютный (%r) — "
                           "загрузка не запущена", series_id, base_path)
            return
        adopted = await self._adopt_existing_files(series)
        await self._retire_superseded(series)
        created = 0
        for item in await self.repo.planned_pending_items(series_id):
            filename = await self._format_filename(series, item)
            save_path = os.path.join(series["save_path"], filename)
            if await self.repo.replace_or_create_task(
                    item["unique_id"], series_id,
                    item.get("download_url") or item["source_url"],
                    save_path):
                created += 1
                if item["status"] == "error":  # ретрай сканом снимает ошибку
                    await self.repo.set_item_status(item["unique_id"],
                                                    "pending")
        self.log.info("диспетч %d: усыновлено %d, задач создано %d",
                      series_id, adopted, created)
        if adopted or created:
            await self._contribute(series_id)
        if created:
            await self._broadcast_queue()
        await self._pump()

    async def _format_filename(self, series: dict, item: dict) -> str:
        reply = await self.request("rules.format_filename", {
            "series": series, "media_item": item}, timeout=30)
        return reply["filename"]

    # --- синхронизация с диском ------------------------------------------------------

    async def _adopt_existing_files(self, series: dict) -> int:
        """pending-элемент плана, чей файл уже лежит с ожидаемым именем,
        регистрируется скачанным без загрузки."""
        adopted = 0
        for item in await self.repo.planned_pending_items(series["id"]):
            filename = await self._format_filename(series, item)
            path = os.path.join(series["save_path"], filename)
            if await storage.call(os.path.exists, path):
                await self.repo.register_downloaded(item["unique_id"],
                                                    filename)
                self.log.info("усыновлён существующий файл: %s", filename)
                adopted += 1
        return adopted

    async def _retire_superseded(self, series: dict) -> int:
        """Delete files of rows whose episode slot is held by another downloaded
        row; the winner's file must exist on disk, so a lone copy is never lost."""
        base = series["save_path"]
        removed = 0
        for row in await self.repo.superseded_files(series["id"]):
            path = os.path.join(base, row["filename"])
            winner_path = os.path.join(base, row["winner_filename"])
            if os.path.realpath(path) == os.path.realpath(winner_path):
                continue
            if not _inside(base, path):
                self.log.warning("файл вне каталога серии не тронут: %s", path)
                continue
            if not await storage.call(os.path.isfile, winner_path):
                continue
            try:
                await storage.call(os.remove, path, owns=path)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self.log.warning("вытесненный файл %s не удалён: %s", path, exc)
                continue
            await self.repo.clear_item_filename(row["unique_id"])
            self.log.info("вытеснен победителем слота, файл удалён: %s",
                          row["filename"])
            removed += 1
        if removed:
            await self._contribute(series["id"])
        return removed

    async def on_fs_sync(self, env: Envelope) -> dict:
        """Проверка диска по запросу (скан, открытие модалки статуса):
        пропавшие файлы — снова pending, найденные — completed.
        Заменяет фоновую 60-секундную проверку старой системы."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        # The files of a torrent series are the torrents module's: nothing to check, publish or warn about here.
        if series["kind"] == SeriesKind.TORRENT:
            return {"adopted": 0, "lost": 0}
        # F7/VF7: путь/NAS недоступен → НЕ сбрасывать всё ложно в pending.
        if not await storage.call(os.path.exists, series["save_path"]):
            self.log.warning("серия %d: путь %s недоступен — сверка диска "
                             "пропущена", series_id, series["save_path"])
            await self._contribute(series_id)
            return {"adopted": 0, "lost": 0}
        lost = 0
        for item in await self.repo.completed_items(series_id):
            # нарезанные компиляции живут отдельными файлами — их
            # верификация принадлежит slicing (читаем чужую колонку)
            if (item.get("slicing_status") or "none") in (
                    "completed", "completed_with_errors"):
                continue
            rel = item.get("final_filename")
            if not rel:
                continue
            path = os.path.join(series["save_path"], rel)
            if not await storage.call(os.path.exists, path):
                await self.repo.reset_download_state(item["unique_id"])
                self.log.warning("файл пропал: %s — статус сброшен", path)
                lost += 1
        adopted = await self._adopt_existing_files(series)
        await self._retire_superseded(series)
        # Обход диска — авторитетная точка: публикуем готовность всегда,
        # даже если ничего не изменилось (иначе после рестарта вклад не
        # переотправится до первого реального изменения). _pump имеет
        # смысл только когда что-то вернулось в очередь.
        await self._contribute(series_id)
        if lost or adopted:
            await self._pump()
        return {"adopted": adopted, "lost": lost}

    # --- очередь ------------------------------------------------------------------

    async def on_set_filename(self, env: Envelope) -> None:
        """final_filename — наша колонка; renaming сообщает новое имя
        после переобработки (Р-15)."""
        await self.repo.set_item_filename(env.payload["unique_id"],
                                          env.payload["filename"])
        series_id = await self.repo.series_of_item(env.payload["unique_id"])
        if series_id is not None:
            self.publish_event("downloads.item.renamed", {
                "series_id": series_id, "unique_id": env.payload["unique_id"],
                "filename": env.payload["filename"]})

    async def on_set_status(self, env: Envelope) -> None:
        """status — наша колонка; slicing помечает усыновлённую
        компиляцию скачанной (deep-adoption)."""
        await self.repo.set_item_status(env.payload["unique_id"],
                                        env.payload["status"])

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19 + Д2: прервать активные загрузки серии и снести
        НЕДОДЕЛАННЫЕ полуфайлы (законченные не трогаем — файлы остаются по
        принципу «удаление не удаляет файлы»), затем вычистить vk-задачи."""
        series_id = env.payload["series_id"]
        for task in await self.repo.tasks_for_series(series_id):
            await self._stop_task(task)
        await self.repo.delete_for_series(series_id)
        await self._broadcast_queue()
        self.publish_event("series.purged",
                           {"series_id": series_id, "owner": self.name})

    async def _cancel_runner(self, task_id: int) -> None:
        """Stops the running download of a task and waits for it; ytdlp kills its processes in a finally."""
        runner = self._active.get(task_id)
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except BaseException:  # noqa: BLE001 - wait for the cancellation to finish
                pass

    async def _stop_task(self, task: dict) -> None:
        """Stops a task and removes what its download left half-written; a finished file stays."""
        await self._cancel_runner(task["id"])
        path = task.get("save_path")
        # No final file means the download did not finish: remove the half-written files;
        # a final file in place is a finished one and stays.
        if path and not await storage.call(os.path.exists, path):
            await storage.call(self._remove_partial, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)

    async def _drop_tasks_without_plan_row(self, series_id: int) -> int:
        """A task lives while its row does: one without a row is stopped whatever its state (its file would
        land on disk with nobody to register it); a waiting or failed one whose row left the plan just goes."""
        gone = await self.repo.tasks_without_row(series_id)
        for task in gone:
            await self._stop_task(task)
            await self.repo.delete_task(task["id"])
        return len(gone) + await self.repo.drop_pending_outside_plan(series_id)

    async def on_cancel(self, env: Envelope) -> dict:
        """Точечная отмена загрузки (приказ пользователя): убить активный
        yt-dlp, удалить недокачанный файл, снять задачу из очереди. НЕ
        трогает план/игноры — штатная автоматизация (скан) может позже
        снова поставить серию в загрузку (находка 45)."""
        task_id = env.payload["task_id"]
        task = await self.repo.get_task(task_id)
        if task is None:
            raise LookupError(f"задача загрузки {task_id} не найдена")
        uid, series_id = task["task_key"], task["series_id"]

        # 1) остановить активную загрузку — cancel прерывает _run_download,
        # ytdlp.download в finally убивает процесс
        await self._cancel_runner(task_id)

        # 2) удалить недокачанный файл и временные артефакты yt-dlp
        if task.get("save_path"):
            await storage.call(self._remove_partial, task["save_path"], timeout=storage.REMOVE_TIMEOUT_S,
                               owns=task["save_path"])

        # 3) снять задачу, вернуть элемент в pending (был downloading)
        await self.repo.delete_task(task_id)
        await self.repo.set_item_status(uid, "pending")
        self.log.info("загрузка %s отменена пользователем (задача %d)",
                      uid, task_id)
        await self._contribute(series_id)
        await self._broadcast_queue()
        return {"cancelled": True}

    @staticmethod
    def _remove_partial(save_path: str) -> None:
        """Целевой файл + недокачанные артефакты того же видео — точечно по
        имени (привязка к base сохраняемого файла), чужого не трогаем.

        VF6: основной мусор этого кода — собственные шаблоны ytdlp.py:
        `-o <save>.download.<ext>` и tmp `<save>.remux.mp4`. Их подчищает
        finally в ytdlp.download(), но он обёрнут ТОЛЬКО вокруг фазы remux —
        при обрыве/отмене в фазе download (cancel, Д2-удаление) finally не
        отрабатывает, и эти файлы оставались. Чистим их здесь явно — очистка
        перестаёт зависеть от того, в какой фазе оборвались.

        Подбор артефактов — строковым сопоставлением (ytdlp.download_artifacts),
        НЕ glob: каталоги медиатеки именуются с `[tmdbid-NNNN]`, а glob трактует
        `[...]` как класс символов и не находит реальные файлы."""
        for path in (save_path, *ytdlp.download_artifacts(save_path)):
            if os.path.isfile(path):
                storage.remove_file(path)

    async def on_queue_get(self, env: Envelope) -> dict:
        tasks = await self.repo.active_tasks()
        return {"tasks": tasks, "count": len(tasks)}

    async def on_episodes_get(self, env: Envelope) -> dict:
        """Reference episodes of the series with their download state (for voices)."""
        return {"items": await self.repo.reference_items(env.payload["series_id"])}

    async def on_queue_clear(self, env: Envelope) -> dict:
        deleted = await self.repo.clear_queue()
        await self._broadcast_queue()
        return {"deleted": deleted}

    async def _broadcast_queue(self) -> None:
        tasks = await self.repo.active_tasks()
        self.publish_event("downloads.queue.changed",
                           {"tasks": tasks, "count": len(tasks)})

    # --- диспетчер и загрузка ---------------------------------------------------------

    async def _pump(self) -> None:
        free = self._limit - len(self._active)
        if free <= 0:
            return
        for task in await self.repo.next_pending(free):
            if task["id"] in self._active:
                continue
            # слот резервируется ДО первого await — конкурентный _pump
            # (завершение другой загрузки) не возьмёт ту же задачу
            placeholder: asyncio.Future = asyncio.get_running_loop(
                ).create_future()
            self._active[task["id"]] = placeholder
            await self.repo.mark_downloading(task["id"])
            await self.repo.set_item_status(task["task_key"], "downloading")
            runner = asyncio.create_task(self._run_download(task))
            self._active[task["id"]] = runner
            placeholder.cancel()
            self._tasks.append(runner)
            self.log.info("задача %d (%s) отправлена на загрузку",
                          task["id"], task["task_key"])
        await self._broadcast_queue()

    async def _run_download(self, task: dict) -> None:
        task_id, uid = task["id"], task["task_key"]
        series_id = task["series_id"]
        try:
            await self._contribute(series_id)

            async def on_progress(data: dict) -> None:
                now = time.monotonic()
                if (now - self._last_db_write.get(task_id, 0)
                        > PROGRESS_DB_THROTTLE) or data.get("progress") == 100:
                    await self.repo.update_progress(task_id, data)
                    self._last_db_write[task_id] = now
                if now - self._last_event > PROGRESS_EVENT_THROTTLE:
                    self._last_event = now
                    await self._broadcast_queue()

            stream_url, stream_headers, stream_error, progressive = await self._resolve_stream(
                task["video_url"], ytdlp_fallback=lambda: self._candidate_may_use_ytdlp(series_id, uid))

            if stream_error is not None:
                ok, (error, transient) = False, stream_error
            else:
                kw = {"threads": self._threads}
                if stream_headers:
                    kw["headers"] = stream_headers
                if progressive:
                    kw["progressive"] = True
                ok, error = await self._download(stream_url,
                                                 task["save_path"], on_progress,
                                                 **kw)
                transient = not ok and _transient_download_error(error)
            if ok:
                series = await self.request("catalog.series.get",
                                            {"series_id": series_id})
                rel = os.path.relpath(task["save_path"],
                                      series["save_path"]).replace("\\", "/")
                await self.repo.register_downloaded(uid, rel)
                await self.repo.delete_task(task_id)
                await self._retire_superseded(series)
                self.send_command("catalog.series.touch_scan_time",
                                  {"series_id": series_id})
                self.log.info("загрузка завершена: %s", rel)
            else:
                attempt = int(task.get("attempts") or 0) + 1
                budget = _download_budget()
                if budget.allows(attempt) and transient:
                    pause = budget.pause(attempt)
                    self.log.warning(
                        "загрузка %s: %s — повтор %d/%d через %.0f с",
                        uid, error, attempt, budget.attempts, pause)
                    await asyncio.sleep(pause)
                    await self.repo.requeue_task(task_id)
                else:
                    await self.repo.mark_error(task_id, error)
                    await self.repo.set_item_status(uid, "error")
                    self.log.error("загрузка %s не удалась: %s", uid, error)
        except Exception as exc:  # noqa: BLE001 — ошибка не валит модуль
            self.log.exception("сбой воркера загрузки %d", task_id)
            await self.repo.mark_error(task_id, f"внутренняя ошибка: {exc}")
            await self.repo.set_item_status(uid, "error")
        finally:
            self._active.pop(task_id, None)
            self._last_db_write.pop(task_id, None)
            await self._contribute(series_id)
            await self._broadcast_queue()
            await self._pump()

    async def _resolve_stream(self, url: str, *, max_height: int | None = None,
                              ytdlp_fallback) -> tuple[str, dict | None, tuple[str, bool] | None, bool]:
        """(stream url, headers, (error, transient), progressive) of a link, via the provider that owns it.

        `ytdlp_fallback` is an async callable asked only when the request fails: True hands the link to
        the downloader as it is, False makes the failure the error of the job, since a page of a web
        player is no stream and yt-dlp would scrape a foreign player."""
        payload = {"url": url, **({"max_height": max_height} if max_height else {})}
        try:
            reply = await self.request("sources.video.stream", payload,
                                       timeout=STREAM_RESOLVE_TIMEOUT_S)
            if reply.get("error"):
                return url, None, (reply["error"], bool(reply.get("transient"))), False
            if reply.get("handled"):
                return reply["stream_url"], reply.get("headers"), None, bool(reply.get("progressive"))
        except Exception as exc:
            if not await ytdlp_fallback():
                return url, None, (f"поток не получен: {exc}", True), False
            self.log.debug("sources.video.stream не обработан для %s: %s", url, exc)
        return url, None, None, False

    async def on_fetch(self, env: Envelope) -> None:
        job = {k: env.payload.get(k) for k in ("key", "url", "path", "max_height", "series_id")}
        if not os.path.isabs(job["path"] or ""):
            self.publish_event("downloads.fetch.finished", {
                **job, "ok": False, "transient": False,
                "error": "путь сохранения должен быть абсолютным"})
            return
        if job["key"] in self._fetch_active or any(
                queued["key"] == job["key"] for queued in self._fetch_queue):
            return
        self._fetch_queue.append(job)
        self._fetch_pump()

    async def on_fetch_cancel(self, env: Envelope) -> None:
        """payload: {series_id?, key?}: the jobs of a series, one job by its key, or the job of that key in that series."""
        series_id, key = env.payload.get("series_id"), env.payload.get("key")
        if series_id is None and key is None:
            raise ValueError("отмена загрузки: нужен series_id или key")

        def wanted(job_key: str, owner: int) -> bool:
            return (key is None or job_key == key) and (series_id is None or owner == series_id)

        self._fetch_queue = [job for job in self._fetch_queue if not wanted(job["key"], job["series_id"])]
        for job_key, (runner, owner) in list(self._fetch_active.items()):
            if wanted(job_key, owner):
                runner.cancel()

    def _fetch_pump(self) -> None:
        while self._fetch_queue and len(self._fetch_active) < self._fetch_limit:
            job = self._fetch_queue.pop(0)
            runner = asyncio.create_task(self._fetch_one(job))
            self._fetch_active[job["key"]] = (runner, job["series_id"])
            self._tasks.append(runner)
            self.publish_event("downloads.fetch.started", {
                "key": job["key"], "series_id": job["series_id"], "path": job["path"]})

    async def _fetch_one(self, job: dict) -> None:
        key, path = job["key"], job["path"]
        ok, error, transient = False, "", False

        async def ignore_progress(_data: dict) -> None:
            return None

        async def attempt_once() -> tuple[bool, str, bool]:
            stream_url, headers, stream_error, progressive = await self._resolve_stream(
                job["url"], max_height=job.get("max_height"), ytdlp_fallback=_never)
            if stream_error is not None:
                return (False, *stream_error)
            kw: dict = {"threads": self._threads}
            if headers:
                kw["headers"] = headers
            if progressive:
                kw["progressive"] = True
            done, failure = await self._download(stream_url, path, ignore_progress, **kw)
            return done, failure, not done and _transient_download_error(failure)

        budget = _download_budget()

        def report(result: tuple[bool, str, bool], attempt: int, pause: float) -> None:
            self.log.warning("загрузка %s: %s — повтор %d/%d через %.0f с", key, result[1], attempt, budget.attempts, pause)

        try:
            (ok, error, transient), _attempts, _exhausted = await retrying(
                attempt_once, final=lambda result: result[0] or not result[2], budget=budget, on_retry=report)
        except asyncio.CancelledError:
            await storage.call(self._remove_partial, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
            self._fetch_active.pop(key, None)
            self._fetch_pump()
            raise
        except Exception as exc:  # noqa: BLE001 - one failed fetch must not take the module down
            self.log.exception("сбой общей загрузки %s", key)
            ok, error, transient = False, f"внутренняя ошибка: {exc}", False
        if not ok:
            await storage.call(self._remove_partial, path, timeout=storage.REMOVE_TIMEOUT_S, owns=path)
        self._fetch_active.pop(key, None)
        self.publish_event("downloads.fetch.finished", {
            "key": key, "ok": ok, "error": "" if ok else error, "transient": transient,
            "path": path, "series_id": job.get("series_id")})
        self._fetch_pump()

    async def _candidate_may_use_ytdlp(self, series_id: int, unique_id: str) -> bool:
        """Whether the downloader may take over a candidate whose stream request failed: only a VK one."""
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id}, timeout=10)
        except BusRequestError:
            return False  # the source is unknown: an error that is retried, not a scrape of a foreign player
        source = source_of_candidate(series.get("sources") or [],
                                     await self.repo.source_of_item(unique_id))
        return source is not None and source["source_type"] == SourceType.VK_VIDEO

    # --- свёртка статусов (Р-11) -----------------------------------------------------

    async def _contribute(self, series_id: int) -> None:
        flags = await self.repo.series_flags(series_id)
        self.publish_event("series.status.contribution", {
            "source": "downloads", "series_id": series_id, "flags": flags})
        # Д1: состав скачанного мог измениться (усыновление/докачка/сброс) —
        # толкаем счётчик в SSE, карточка обновляет «скачано» без перезагрузки.
        count = await self.repo.downloaded_count(series_id)
        self.publish_event("series.downloaded.changed",
                           {"series_id": series_id, "count": count})
