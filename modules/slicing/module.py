"""Модуль slicing — нарезка компиляций ffmpeg по главам (Р-16).

Владение: slicing_tasks, sliced_files, колонки media_items
slicing_status/chapters/chapters_filtered.

Queries:
  slicing.chapters.get {unique_id}            — главы yt-dlp (+ в БД)
  slicing.chapters.filtered {unique_id}       — главы + автофильтр мусора
  slicing.chapters.mark {unique_id, garbage_indices} — ручная разметка
  slicing.task.create {unique_id, garbage_indices?}  — задача на нарезку
      (контракт роутов: число хороших глав == диапазону эпизодов)
  slicing.verify {unique_id}                  — сверка детей с диском
  slicing.deep_adoption {series_id}           — усыновление уже нарезанных
  slicing.files.list {series_id}              — для renaming (Р-15)
  slicing.queue.get
Commands:
  slicing.file.set_path {id, path}            — для renaming (Р-15)

События: slicing.queue.changed {tasks} (контракт SSE
slicing_queue_update), series.status.contribution {source: slicing}.

Нарезка: одна задача за раз (ffmpeg грузит диск), '-c copy' без
перекодирования, granular-ресьюмабельность progress_chapters
(семантика оригинала — она хороша); ffmpeg с таймаутом (согласованное
отличие: зависший процесс — ошибка-носитель, не вечное зависание).
Нумерация эпизодов: episode_start + индекс главы в сохранённом списке
(1:1 с оригиналом; мусор в списке почти исключён фильтрацией при
создании задачи — см. разбор Р-16).
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from . import chapters as ch
from .repository import SlicingRepository

FFMPEG_TIMEOUT = 1800.0  # секунд на одну главу ('-c copy' — секунды)


async def run_ffmpeg(source: str, start: str, duration: str | None,
                     output: str) -> tuple[bool, str]:
    """Вырезание одной главы. Подменяется в тестах."""
    executable = shutil.which("ffmpeg")
    if not executable:
        return False, "ffmpeg не найден в PATH"
    command = [executable, "-y", "-ss", start, "-i", source]
    if duration:
        command += ["-t", duration]
    command += ["-c", "copy", output]
    proc = await asyncio.create_subprocess_exec(
        *command, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE)
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(),
                                           FFMPEG_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        return False, f"ffmpeg не уложился в {FFMPEG_TIMEOUT:.0f} с"
    if proc.returncode != 0:
        return False, stderr.decode("utf-8", "replace").strip()[-500:]
    return True, ""


class SlicingModule(BaseModule):
    name = "slicing"

    def __init__(self, bus, db: Database, *, ffmpeg=run_ffmpeg,
                 fetch_chapters=ch.fetch_chapters) -> None:
        self.repo = SlicingRepository(db)
        self._ffmpeg = ffmpeg
        self._fetch = fetch_chapters
        self._wake = asyncio.Event()
        super().__init__(bus)

    def register(self) -> None:
        self.handle("slicing.chapters.get", self.on_chapters_get,
                    concurrent=True)
        self.handle("slicing.chapters.filtered", self.on_chapters_filtered,
                    concurrent=True)
        self.handle("slicing.chapters.mark", self.on_chapters_mark)
        self.handle("slicing.task.create", self.on_task_create)
        self.handle("slicing.verify", self.on_verify)
        self.handle("slicing.deep_adoption", self.on_deep_adoption,
                    concurrent=True)
        self.handle("slicing.files.list", self.on_files_list)
        self.handle("slicing.files.drop_for_source",
                    self.on_files_drop_for_source)
        self.handle("slicing.file.set_path", self.on_file_set_path)
        self.handle("slicing.queue.get", self.on_queue_get)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        requeued = await self.repo.requeue_interrupted()
        if requeued:
            self.log.info("оборванных нарезок возвращено в очередь: %d",
                          requeued)
        for series_id in await self.repo.series_with_activity():
            await self._contribute(series_id)
        self._tasks.append(asyncio.create_task(self._worker_loop()))
        self._wake.set()

    # --- главы ---------------------------------------------------------------------

    async def _item_or_fail(self, unique_id: str) -> dict:
        item = await self.repo.get_item(unique_id)
        if not item:
            raise LookupError(f"медиа-элемент {unique_id} не найден")
        return item

    @staticmethod
    def _expected_count(item: dict) -> int:
        return (item.get("episode_end") or 0) - \
            (item.get("episode_start") or 0) + 1

    async def on_chapters_get(self, env: Envelope) -> list[dict]:
        # проверка глав НЕ меняет slicing_status (находка 53): 'pending'
        # означает запущенную задачу нарезки, а не «главы готовы» —
        # иначе проверка оглавления блокировала бы саму нарезку.
        item = await self._item_or_fail(env.payload["unique_id"])
        chapters = await self._fetch(item["source_url"])
        await self.repo.set_chapters(item["unique_id"], chapters=chapters)
        return chapters

    async def on_chapters_filtered(self, env: Envelope) -> dict:
        item = await self._item_or_fail(env.payload["unique_id"])
        chapters = await self._fetch(item["source_url"])
        if not chapters:
            return {"chapters": [], "filtered_chapters": [],
                    "garbage_chapters": []}
        filtered = ch.filter_chapters(chapters)
        garbage = ch.garbage_chapters(chapters)
        await self.repo.set_chapters(item["unique_id"], chapters=chapters,
                                     filtered=filtered)
        expected = self._expected_count(item)
        # slicing_status НЕ трогаем (находка 53) — см. on_chapters_get
        return {"chapters": chapters, "filtered_chapters": filtered,
                "garbage_chapters": garbage, "expected_count": expected}

    async def on_chapters_mark(self, env: Envelope) -> dict:
        item = await self._item_or_fail(env.payload["unique_id"])
        if not item.get("chapters"):
            raise ValueError("главы не найдены в БД — сначала получите их")
        chapters = json.loads(item["chapters"])
        marked = ch.mark_manually(chapters, env.payload["garbage_indices"])
        filtered = [c for c in marked if not c["is_garbage"]]
        await self.repo.set_chapters(item["unique_id"], filtered=filtered)
        expected = self._expected_count(item)
        # slicing_status НЕ трогаем (находка 53) — см. on_chapters_get
        return {"chapters": marked, "filtered_chapters": filtered,
                "garbage_chapters": [c for c in marked if c["is_garbage"]],
                "expected_count": expected}

    # --- создание задачи -----------------------------------------------------------------

    async def on_task_create(self, env: Envelope) -> dict:
        """Контракт старых /slice и /slice-with-filter."""
        item = await self._item_or_fail(env.payload["unique_id"])
        uid = item["unique_id"]
        if item.get("slicing_status") not in ("none",
                                              "completed_with_errors",
                                              "error"):
            raise RuntimeError("задача на нарезку уже в очереди или была "
                               "успешно завершена")
        if not item.get("chapters"):
            raise ValueError("главы не найдены — сначала получите их")
        chapters = json.loads(item["chapters"])
        garbage_indices = env.payload.get("garbage_indices") or []
        if garbage_indices:
            marked = ch.mark_manually(chapters, garbage_indices)
            filtered = [c for c in marked if not c["is_garbage"]]
        else:
            filtered = ch.filter_chapters(chapters)
        expected = self._expected_count(item)
        if len(filtered) != expected:
            raise ValueError(
                f"число отфильтрованных глав ({len(filtered)}) не "
                f"совпадает с ожидаемым ({expected})")
        # как slice-with-filter: оба поля получают отфильтрованный список
        await self.repo.set_chapters(uid, chapters=filtered,
                                     filtered=filtered)
        deleted = await self.repo.delete_sliced_for_source(uid)
        if deleted:
            self.log.info("удалено %d старых записей о нарезке для %s",
                          deleted, uid)
        await self.repo.delete_task_by_uid(uid)
        await self.repo.create_task(uid, item["series_id"])
        await self.repo.set_slicing_status(uid, "pending")
        await self._broadcast_queue()
        self._wake.set()
        return {"created": True, "chapters": len(filtered)}

    # --- воркер: одна нарезка за раз ----------------------------------------------------

    async def _worker_loop(self) -> None:
        while True:
            task = await self.repo.next_pending()
            if task is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            try:
                await self._process_task(task)
            except Exception:  # noqa: BLE001 — воркер не должен умирать
                self.log.exception("сбой обработки задачи нарезки %d",
                                   task["id"])

    async def _process_task(self, task: dict) -> None:
        uid, task_id = task["media_item_unique_id"], task["id"]
        series_id = task["series_id"]
        try:
            await self.repo.set_task_status(task_id, "slicing")
            await self.repo.set_slicing_status(uid, "slicing")
            await self._contribute(series_id)
            await self._broadcast_queue()

            item = await self._item_or_fail(uid)
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            if not item.get("final_filename"):
                raise FileNotFoundError(
                    f"у элемента {uid} нет пути к исходному файлу")
            source = os.path.join(series["save_path"],
                                  item["final_filename"])
            chapters = json.loads(item.get("chapters_filtered")
                                  or item["chapters"])
            if not await asyncio.to_thread(os.path.exists, source):
                raise FileNotFoundError(f"исходник не найден: {source}")

            progress = task["progress_chapters"]
            if isinstance(progress, str):
                progress = json.loads(progress or "{}")
            if not progress:  # первый запуск: усыновить уже нарезанное
                progress = await self._init_progress(series, item, chapters,
                                                     task_id)

            for i, chapter in enumerate(chapters):
                if chapter.get("is_garbage"):
                    self.log.info("мусорная глава %d пропущена: %s", i + 1,
                                  chapter.get("title"))
                    continue
                episode = item["episode_start"] + i
                if progress.get(str(episode)) == "completed":
                    continue
                out_rel = await self._child_name(series, item, episode)
                out_abs = os.path.join(series["save_path"], out_rel)
                duration = self._duration(chapter, chapters, i)
                self.log.info("ffmpeg: эпизод %d (%s, %s)", episode,
                              chapter["time"], duration or "до конца")
                ok, error = await self._ffmpeg(source, chapter["time"],
                                               duration, out_abs)
                if not ok:
                    raise RuntimeError(
                        f"ffmpeg, эпизод {episode}: {error}")
                await self.repo.add_sliced_file(series_id, uid, episode,
                                                out_rel)
                progress[str(episode)] = "completed"
                await self.repo.set_task_progress(task_id, progress)
                await self._broadcast_queue()

            await self.repo.set_slicing_status(uid, "completed")
            # нарезанная компиляция уходит из планов и статусов —
            # семантика оригинала; колонкой владеет scan
            self.send_command("scan.item.set_ignored",
                              {"unique_id": uid, "is_ignored": True})
            await self._maybe_delete_source(uid, source)
            await self.repo.delete_task(task_id)
            self.log.info("нарезка %s завершена", uid)
        except Exception as exc:  # noqa: BLE001 — ошибка остаётся носителем
            self.log.exception("ошибка нарезки %s", uid)
            await self.repo.set_task_status(task_id, "error", str(exc))
            await self.repo.set_slicing_status(uid, "error")
        finally:
            await self._contribute(series_id)
            await self._broadcast_queue()

    async def _init_progress(self, series: dict, item: dict,
                             chapters: list[dict], task_id: int) -> dict:
        progress = {}
        for i in range(len(chapters)):
            episode = item["episode_start"] + i
            progress[str(episode)] = "pending"
            rel = await self._child_name(series, item, episode)
            if await asyncio.to_thread(
                    os.path.exists, os.path.join(series["save_path"], rel)):
                self.log.info("эпизод %d уже на диске — усыновлён", episode)
                progress[str(episode)] = "completed"
                await self.repo.add_sliced_file(series["id"],
                                                item["unique_id"], episode,
                                                rel)
        await self.repo.set_task_progress(task_id, progress)
        return progress

    async def _child_name(self, series: dict, item: dict,
                          episode: int) -> str:
        reply = await self.request("rules.format_filename", {
            "series": series, "media_item": item,
            "episode_override": episode})
        return reply["filename"]

    @staticmethod
    def _duration(chapter: dict, chapters: list[dict], i: int) -> str | None:
        if i + 1 >= len(chapters):
            return None  # последняя глава — до конца файла
        # time_to_seconds (а не strptime '%H:%M:%S'): компиляции длиннее
        # суток дают время вида 24:02:29 — strptime часы>23 не парсит
        # (находка 55). ffmpeg -t принимает секунды.
        nxt = ch.time_to_seconds(chapters[i + 1]["time"])
        cur = ch.time_to_seconds(chapter["time"])
        if nxt is None or cur is None:
            return None
        return str(nxt - cur)

    async def _maybe_delete_source(self, uid: str, source: str) -> None:
        reply = await self.request("settings.value.get",
                                   {"key": "slicing_delete_source_file"})
        if (reply.get("value") or "false") != "true":
            return
        try:
            await asyncio.to_thread(os.remove, source)
            self.log.info("исходник удалён по настройке: %s", source)
            self.send_command("downloads.item.set_filename",
                              {"unique_id": uid, "filename": None})
        except OSError as exc:
            self.log.error("не удалось удалить исходник %s: %s", source, exc)

    # --- verify / deep adoption --------------------------------------------------------

    async def on_verify(self, env: Envelope) -> dict:
        """Контракт /verify-sliced-files: сверка детей с диском."""
        item = await self._item_or_fail(env.payload["unique_id"])
        series = await self.request("catalog.series.get",
                                    {"series_id": item["series_id"]})
        children = await self.repo.sliced_for_source(item["unique_id"])
        if not children:
            await self.repo.set_slicing_status(item["unique_id"], "none")
            return {"status": "none"}
        has_missing = False
        for child in children:
            path = os.path.join(series["save_path"], child["file_path"])
            exists = await asyncio.to_thread(os.path.exists, path)
            if exists and child["status"] == "missing":
                await self.repo.set_sliced_status(child["id"], "completed")
            elif not exists:
                if child["status"] == "completed":
                    await self.repo.set_sliced_status(child["id"], "missing")
                has_missing = True
        status = "completed_with_errors" if has_missing else "completed"
        await self.repo.set_slicing_status(item["unique_id"], status)
        await self._contribute(item["series_id"])
        return {"status": status, "has_missing_files": has_missing}

    async def on_deep_adoption(self, env: Envelope) -> dict:
        """Контракт /deep-adoption: компиляции плана без глав, чьи дети
        уже все лежат на диске, усыновляются без нарезки."""
        series_id = env.payload["series_id"]
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        items = await self.request("scan.media.list",
                                   {"series_id": series_id})
        adopted = 0
        for item in items:
            if not (item.get("episode_end")
                    and item.get("status") == "pending"
                    and not item.get("chapters")
                    and item.get("plan_status") == "in_plan_compilation"):
                continue
            try:
                chapters = await self._fetch(item["source_url"])
            except Exception as exc:  # noqa: BLE001 — best effort
                self.log.warning("deep-adoption %s: главы не получены: %s",
                                 item["unique_id"], exc)
                continue
            if not chapters:
                continue
            await self.repo.set_chapters(item["unique_id"],
                                         chapters=chapters)
            found = 0
            for i in range(len(chapters)):
                episode = item["episode_start"] + i
                rel = await self._child_name(series, item, episode)
                if await asyncio.to_thread(
                        os.path.exists,
                        os.path.join(series["save_path"], rel)):
                    await self.repo.add_sliced_file(series_id,
                                                    item["unique_id"],
                                                    episode, rel)
                    found += 1
            if found == len(chapters) and found > 0:
                self.log.info("deep-adoption: все %d детей %s найдены — "
                              "усыновляем", found, item["unique_id"])
                await self.repo.set_slicing_status(item["unique_id"],
                                                   "completed")
                self.send_command("scan.item.set_ignored", {
                    "unique_id": item["unique_id"], "is_ignored": True})
                self.send_command("downloads.item.set_filename", {
                    "unique_id": item["unique_id"], "filename": None})
                # status='completed' — колонка downloads; в оригинале
                # усыновлённая компиляция помечалась скачанной
                self.send_command("downloads.item.set_status", {
                    "unique_id": item["unique_id"], "status": "completed"})
                adopted += 1
        if adopted:
            await self._contribute(series_id)
        return {"adopted": adopted}

    # --- сервисные -----------------------------------------------------------------------

    async def on_files_list(self, env: Envelope) -> list[dict]:
        return await self.repo.sliced_for_series(env.payload["series_id"])

    async def on_files_drop_for_source(self, env: Envelope) -> None:
        """Каскад композиции (Р-21): компиляция вышла из плана —
        записи о её нарезанных детях удаляются (как в старом
        get_series_composition)."""
        deleted = await self.repo.delete_sliced_for_source(
            env.payload["unique_id"])
        if deleted:
            self.log.info("композиция: удалено %d записей о нарезке "
                          "устаревшей компиляции %s", deleted,
                          env.payload["unique_id"])

    async def on_file_set_path(self, env: Envelope) -> None:
        await self.repo.set_sliced_path(env.payload["id"],
                                        env.payload["path"])

    async def on_queue_get(self, env: Envelope) -> dict:
        tasks = await self.repo.all_tasks()
        return {"tasks": tasks, "count": len(tasks)}

    async def _broadcast_queue(self) -> None:
        tasks = await self.repo.all_tasks()
        self.publish_event("slicing.queue.changed", {"tasks": tasks})

    async def _contribute(self, series_id: int) -> None:
        flags = await self.repo.series_flags(series_id)
        self.publish_event("series.status.contribution", {
            "source": "slicing", "series_id": series_id, "flags": flags})

    async def on_series_deleted(self, env):
        """Каскад Р-19: владелец чистит slicing_tasks и sliced_files."""
        await self.repo.delete_for_series(env.payload["series_id"])
