"""Модуль downloads — загрузка VK-видео (yt-dlp). Решение Р-13.

Владение: download_tasks (vk-часть) + колонки media_items.status и
final_filename.

События (входящие):
  scan.plan.updated {series_id} — план пересчитан: ТОЛЬКО усыновление
      готовых на диске файлов + чистка pending вне плана. Загрузку НЕ
      запускает (инвариант: усыновление ≠ загрузка; открытие композиции
      и сохранение свойств не должны качать).
  settings.changed — подписка на max_parallel_downloads (вместо
      перечитывания каждые 5 с).

Queries / commands:
  downloads.dispatch {series_id} — ПРИКАЗ скана докачать недостающее:
      усыновление + создание задач на планово-ожидаемое, чего НЕТ на
      диске, + пинок диспетчеру. ЕДИНСТВЕННАЯ точка старта загрузки
      (шлёт только scan._run_series по явному скану/автоскану).
  downloads.queue.get {} → {tasks, count} — активные задачи (загрузка UI)
  downloads.queue.clear {} → {deleted} — всё, кроме идущих загрузок
  downloads.fs.sync {series_id} → {adopted, lost} — синхронизация с
      диском (вызывается сканом и при открытии модалки статуса)

События (исходящие):
  downloads.queue.changed {tasks, count} — при реальных изменениях
      (троттлинг на прогрессе) — контракт SSE download_queue_update.
  series.status.contribution {source: downloads, ...} — свёртка Р-11
      (downloading/error/ready/waiting — семантика sync_vk_statuses).

Диспетчер живёт по событиям (создание задачи, завершение загрузки,
смена лимита) — никаких тиков. Параллелизм — счётчик активных задач
против лимита из настроек.
"""
from __future__ import annotations

import asyncio
import os
import time

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from . import ytdlp
from .repository import DownloadsRepository

PROGRESS_DB_THROTTLE = 2.0   # секунд между записями прогресса в БД
PROGRESS_EVENT_THROTTLE = 1.0  # секунд между событиями очереди при прогрессе


class DownloadsModule(BaseModule):
    name = "downloads"

    def __init__(self, bus, db: Database, *, downloader=ytdlp.download) -> None:
        self.repo = DownloadsRepository(db)
        self._download = downloader  # подменяется в тестах
        self._limit = 2               # параллельных файлов (max_parallel_downloads)
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
        self.handle("downloads.fs.sync", self.on_fs_sync)
        self.handle("downloads.item.set_filename", self.on_set_filename)
        self.handle("downloads.item.set_status", self.on_set_status)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        self._limit = await self._read_limit()
        self._threads = await self._read_threads()
        requeued = await self.repo.requeue_interrupted()
        if requeued:
            self.log.info("оборванных загрузок возвращено в очередь: %d",
                          requeued)
        # Свёртки при старте (обязательство поставщика, Р-11).
        try:
            all_series = await self.request("catalog.series.list", {},
                                            timeout=30)
            for s in all_series:
                if s.get("source_type") == "vk_video":
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
        dropped = await self.repo.drop_pending_outside_plan(series_id)
        self.log.info("план %d: усыновлено %d, вычищено вне плана %d",
                      series_id, adopted, dropped)
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
        adopted = await self._adopt_existing_files(series)
        created = 0
        for item in await self.repo.planned_pending_items(series_id):
            filename = await self._format_filename(series, item)
            save_path = os.path.join(series["save_path"], filename)
            if await self.repo.replace_or_create_task(
                    item["unique_id"], series_id, item["source_url"],
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
            if await asyncio.to_thread(os.path.exists, path):
                await self.repo.register_downloaded(item["unique_id"],
                                                    filename)
                self.log.info("усыновлён существующий файл: %s", filename)
                adopted += 1
        return adopted

    async def on_fs_sync(self, env: Envelope) -> dict:
        """Проверка диска по запросу (скан, открытие модалки статуса):
        пропавшие файлы — снова pending, найденные — completed.
        Заменяет фоновую 60-секундную проверку старой системы."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        # F7/VF7: путь/NAS недоступен → НЕ сбрасывать всё ложно в pending.
        if not await asyncio.to_thread(os.path.exists, series["save_path"]):
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
            if not await asyncio.to_thread(os.path.exists, path):
                await self.repo.reset_download_state(item["unique_id"])
                self.log.warning("файл пропал: %s — статус сброшен", path)
                lost += 1
        adopted = await self._adopt_existing_files(series)
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
            runner = self._active.get(task["id"])
            if runner is not None and not runner.done():
                runner.cancel()  # ytdlp в finally убьёт процесс и .download/.remux
                try:
                    await runner
                except BaseException:  # noqa: BLE001 — ждём отмены
                    pass
            sp = task.get("save_path")
            # final отсутствует → загрузка не закончена → снести полуфайлы;
            # если final на месте — это законченный файл, не трогаем.
            if sp and not await asyncio.to_thread(os.path.exists, sp):
                await asyncio.to_thread(self._remove_partial, sp)
        await self.repo.delete_for_series(series_id)
        await self._broadcast_queue()

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
        runner = self._active.get(task_id)
        if runner is not None and not runner.done():
            runner.cancel()
            try:
                await runner
            except BaseException:  # noqa: BLE001 — ждём завершения отмены
                pass

        # 2) удалить недокачанный файл и временные артефакты yt-dlp
        if task.get("save_path"):
            await asyncio.to_thread(self._remove_partial, task["save_path"])

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
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    async def on_queue_get(self, env: Envelope) -> dict:
        tasks = await self.repo.active_tasks()
        return {"tasks": tasks, "count": len(tasks)}

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

            ok, error = await self._download(task["video_url"],
                                             task["save_path"], on_progress,
                                             threads=self._threads)
            if ok:
                series = await self.request("catalog.series.get",
                                            {"series_id": series_id})
                rel = os.path.relpath(task["save_path"],
                                      series["save_path"]).replace("\\", "/")
                await self.repo.register_downloaded(uid, rel)
                await self.repo.delete_task(task_id)
                self.send_command("catalog.series.touch_scan_time",
                                  {"series_id": series_id})
                self.log.info("загрузка завершена: %s", rel)
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
