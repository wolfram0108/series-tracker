"""Модуль scan — оркестратор сканирования (ревизия scanner.py +
расписание из MonitoringAgent).

Queries:
  scan.series.run {series_id, force_replace?} → {changed, tasks_created |
      vk-сводка}. changed — были ли реальные изменения (торрент: созданы
      задачи; VK: изменился состав кандидатов); по нему фронт ручного скана
      шлёт тост «Сканирование завершено» / «Обновлений нет»
      Повторный запуск при идущем скане сериала — ошибка («уже запущен»,
      находка 27).

Commands:
  scan.all.start {} — проход по всем сериалам с auto_scan_enabled
      (то же делает внутренний планировщик по расписанию из settings).

События (исходящие):
  scan.plan.updated {series_id} — план VK-загрузок пересчитан; downloads
      на него делает ТОЛЬКО усыновление готовых файлов (НЕ загрузку).
      Загрузку запускает лишь явный скан: _run_series после _scan_vk
      шлёт команду downloads.dispatch (открытие композиции — не шлёт).
  scan.status.changed {scanner_enabled, scan_interval, is_scanning,
      is_awaiting_tasks, next_scan_time} — состояние планировщика
      (контракт старого scanner_status_update).
  series.status.contribution — свёртка scanning/error (Р-11); носитель
      ошибки — scan_tasks.status='error'.

События (входящие):
  torrents.queue.changed {count} — опустошение конвейера: после полного
      прохода следующий скан назначается, когда count=0.

Контракты будущих модулей (тесты фиксируют их фейками):
  renaming.reprocess {series_id} (query) — переобработка имён перед
      сканом (вместо sleep-поллинга, находка 16);
  torrents.db.active {series_id} (query) → активные торренты серии
      (сверенные с qBittorrent);
  torrents.db.deactivate_all {series_id} (query) — для force_replace;
  torrents.register {series_id, torrent, qb_hash, link_type, replaces}
      (query, идемпотентен) — фиксация раздачи + запуск конвейера +
      замена старой.

Ресьюмабельность (настоящая, вместо находки 26): план замен — в
scan_tasks ДО выполнения; добавления идемпотентны (локальный infohash,
Р-2: повторное добавление = existed); on_start продолжает незавершённые
задачи с места обрыва.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from core import BaseModule, BusRequestError
from core.db import Database
from core.envelope import Envelope

from core import ids
from .planner import build_plan
from .repository import ScanRepository

# Режим замены раздач по трекерам (ревизия находки 28 — вместо
# подстроки в оркестраторе). fixed: новая раздача заменяет активную с
# теми же episodes; rolling: при единственной активной заменяется она,
# при нескольких — осознанно не заменяем (неоднозначно, что устарело).
REPLACE_MODE = {"astar": "fixed"}
DEFAULT_REPLACE_MODE = "rolling"

_NO_HANDLER = "нет обработчика"


class ScanError(RuntimeError):
    pass


class ScanModule(BaseModule):
    name = "scan"

    def __init__(self, bus, db: Database, *,
                 scheduler_tick: float | None = 10.0) -> None:
        self.repo = ScanRepository(db)
        self._tick = scheduler_tick  # None — без планировщика (тесты)
        self._running: set[int] = set()
        self._scan_all_running = False
        self._awaiting_pipeline = False
        super().__init__(bus)

    def register(self) -> None:
        # concurrent: долгий скан одного сериала не должен блокировать
        # очередь запросов к модулю (защита от гонки — set _running).
        self.handle("scan.series.run", self.on_run, concurrent=True)
        self.handle("scan.all.start", self.on_scan_all)
        self.handle("scan.media.list", self.on_media_list)
        self.handle("scan.media.downloaded_counts", self.on_downloaded_counts)
        self.handle("scan.item.set_ignored", self.on_set_ignored)
        self.handle("scan.composition", self.on_composition, concurrent=True)
        self.handle("scan.status.get", self.on_status_get)
        self.handle("torrents.queue.changed", self.on_queue_changed)
        self.handle("series.deleted", self.on_series_deleted)
        self.handle("settings.changed", self.on_settings_changed)
        self.handle("gateway.sse.clients", self.on_sse_clients)

    async def on_start(self) -> None:
        # Носители прошлых ошибок — в свёртку (Р-11: поставщик публикует
        # вклад при старте после reconcile).
        for task in await self.repo.error_tasks():
            self._contribute(task["series_id"], scanning=False, error=True)
        # Настоящий resume незавершённых задач (вместо удаления).
        for task in await self.repo.incomplete_tasks():
            self.log.info("возобновление прерванного скана: задача %d, "
                          "сериал %d", task["id"], task["series_id"])
            self._spawn(self._resume_task(task))
        if self._tick is not None:
            self._spawn(self._scheduler_loop())

    def _spawn(self, coro) -> None:
        self._tasks.append(asyncio.create_task(coro))

    # --- запуск сканов ---------------------------------------------------------

    async def on_run(self, env: Envelope) -> dict:
        p = env.payload
        force = p.get("force_replace")
        if force is None:  # как старый роут: режим из настройки отладки
            force = await self._debug_force_replace()
        # download=False — «полный процесс без загрузки» (сохранение свойств
        # VK-серии): пере-скан по профилю + переименование + усыновление, но
        # БЕЗ постановки новых загрузок (контракт «Сохранить → усыновление»).
        return await self._run_series(int(p["series_id"]), bool(force),
                                      download=p.get("download", True))

    async def on_media_list(self, env: Envelope) -> list[dict]:
        """Состав медиа-элементов серии (владелец строк — scan, Р-12);
        читают renaming (переобработка имён) и UI (этап 5)."""
        return await self.repo.items_for_series(env.payload["series_id"])

    async def on_set_ignored(self, env: Envelope) -> None:
        """is_ignored_by_user — наша колонка; ставят UI (этап 5) и
        slicing (нарезанная компиляция уходит из планов).

        Смена ignore меняет фактический план загрузки → публикуем
        scan.plan.updated: диспетчер downloads пересчитает задачи и
        свёртку статусов (Р-21; покрывает старый sync_vk_statuses)."""
        await self.repo.set_ignored(env.payload["unique_id"],
                                    bool(env.payload["is_ignored"]))
        item = await self.repo.get_item(env.payload["unique_id"])
        if item:
            self.publish_event("scan.plan.updated",
                               {"series_id": item["series_id"]})

    async def on_composition(self, env: Envelope) -> list[dict]:
        """Контракт GET composition для VK-серий (Р-21): при refresh —
        полный скан канала; всегда — чистка нарезки устаревших
        компиляций, синк с диском, проверка детей, пересборка плана и
        реконструкция элементов через движок правил."""
        series_id = env.payload["series_id"]
        refresh = bool(env.payload.get("refresh"))
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id})
        if refresh:
            # полный цикл Р-12: кандидаты + план + scan.plan.updated
            await self._scan_vk(series)
        items = await self.repo.items_for_series(series_id)
        # компиляции, выпавшие из плана, теряют записи о нарезке
        for item in items:
            if item.get("episode_end") and item.get("plan_status") in (
                    "replaced", "redundant"):
                self.send_command("slicing.files.drop_for_source",
                                  {"unique_id": item["unique_id"]})
        await self._neighbour_sync(series_id, items)
        if not refresh:
            # пересборка плана из всего известного (семантика старого
            # reset_plan_status + SmartCollector.collect)
            await self.repo.reset_plan_status(series_id)
            plan = build_plan(await self.repo.candidates(series_id),
                              self._quality_priority(series))
            await self.repo.set_plan_statuses(plan)
            self.publish_event("scan.plan.updated",
                               {"series_id": series_id})
        return await self._reconstruct(series)

    async def _neighbour_sync(self, series_id: int,
                              items: list[dict]) -> None:
        """Синк с диском (downloads) и проверка нарезанных детей
        (slicing) — как в старом composition; соседи опциональны."""
        try:
            await self.request("downloads.fs.sync",
                               {"series_id": series_id}, timeout=120)
        except BusRequestError as exc:
            self.log.warning("композиция: fs.sync недоступен: %s", exc)
        await self._verify_compilations(series_id, items)

    async def _verify_compilations(self, series_id: int,
                                   items: list[dict]) -> None:
        """Сверка нарезанных детей с диском (slicing): пропавший ребёнок →
        missing/completed_with_errors, исходник возвращается в дозагрузку.
        downloads.fs.sync компиляции намеренно пропускает (владелец —
        slicing), поэтому верификацию инициируем явно. F7-гард — в
        slicing._verify_item."""
        for item in items:
            if (item.get("slicing_status") or "none") in (
                    "pending", "completed", "completed_with_errors"):
                try:
                    await self.request("slicing.verify",
                                       {"unique_id": item["unique_id"]},
                                       timeout=120)
                except BusRequestError as exc:
                    self.log.warning("verify нарезки %s: %s",
                                     item["unique_id"], exc)

    async def _reconstruct(self, series: dict) -> list[dict]:
        """Элементы серии в форме старого composition-ответа."""
        items = await self.repo.items_for_series(series["id"])
        profile_id = series.get("parser_profile_id")
        results: list[dict] = [{} for _ in items]
        if profile_id and items:
            reply = await self.request("rules.apply", {
                "profile_id": profile_id,
                "titles": [i.get("source_title") or "" for i in items]},
                timeout=300)
            results = reply["results"]
        plan = []
        for item, r in zip(items, results):
            plan.append({
                "source_data": {
                    "title": item.get("source_title"),
                    "url": item.get("source_url"),
                    "publication_date": item.get("publication_date"),
                    "resolution": item.get("resolution"),
                },
                "match_events": r.get("events") or [],
                "result": {"extracted": r.get("extracted") or {}},
                "season": item.get("season"),
                "plan_status": item.get("plan_status"),
                "status": item.get("status"),
                "unique_id": item.get("unique_id"),
                "final_filename": item.get("final_filename"),
                "slicing_status": item.get("slicing_status") or "none",
                "is_ignored_by_user": bool(item.get("is_ignored_by_user")),
                "source_title": item.get("source_title"),
            })
        return plan

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}} — скачанные VK-эпизоды (Р-19)."""
        return {"counts": await self.repo.downloaded_counts()}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит media_items и scan_tasks."""
        await self.repo.delete_for_series(env.payload["series_id"])

    async def on_scan_all(self, env: Envelope) -> dict:
        if self._scan_all_running:
            self.log.warning("полный проход уже идёт — повтор отклонён")
            return {"started": False}
        force = bool((env.payload or {}).get("force_replace")) \
            or await self._debug_force_replace()
        self._spawn(self._scan_all(force))
        return {"started": True}

    async def on_status_get(self, env: Envelope) -> dict:
        """Состояние планировщика (контракт GET /api/scanner/status)."""
        return await self._status_payload()

    async def on_settings_changed(self, env: Envelope) -> None:
        """Р-20: смена настроек сканера пересчитывает расписание от
        текущего момента (вместо немедленного полного скана старой
        системы — лишняя нагрузка на трекеры)."""
        key = env.payload.get("key")
        if key not in ("scanner_agent_enabled", "scan_interval_minutes"):
            return
        if await self._setting("scanner_agent_enabled", "false") == "true":
            await self._schedule_next()
        else:
            await self._broadcast_status()

    async def on_sse_clients(self, env: Envelope) -> None:
        """Новый SSE-клиент: публикуем текущее состояние, чтобы вкладка
        настроек не жила на дефолтах до первого изменения (Р-20)."""
        if env.payload.get("count", 0) > 0:
            await self._broadcast_status()

    async def _debug_force_replace(self) -> bool:
        return await self._setting("debug_force_replace", "false") == "true"

    async def _run_series(self, series_id: int, force: bool = False,
                          download: bool = True) -> dict:
        if series_id in self._running:
            raise ScanError(f"скан сериала {series_id} уже запущен")
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            if not series.get("parser_profile_id"):
                raise ScanError(
                    f"для сериала «{series.get('name')}» не назначен "
                    "профиль правил")
            # Прошлая ошибка сбрасывается новым сканом (Р-11).
            await self.repo.delete_error_tasks(series_id)
            await self._reprocess_names(series_id)
            if series["source_type"] == "vk_video":
                # Сверка диска перед сканом (R4, ситуации VF1/VF3/VC1):
                # пропавший скачанный .mp4 → сброс completed→pending, чтобы
                # dispatch перекачал. Симметрично торрент-ветке, которая
                # стартует с torrents.fs.verify. Без этого «удалил VK-файл →
                # обычный скан не замечает». Гард недоступного пути (F7/VF7) —
                # внутри downloads.on_fs_sync; композиция зовёт fs.sync сама.
                try:
                    await self.request("downloads.fs.sync",
                                       {"series_id": series_id}, timeout=120)
                except BusRequestError as exc:
                    self.log.warning("сверка диска перед VK-сканом "
                                     "пропущена: %s", exc)
                result = await self._scan_vk(series)
                # VF2: проверка нарезанных детей на КАЖДОМ скане (а не только
                # при открытии композиции). Пропавший sliced-файл → missing/
                # completed_with_errors + исходник назад в дозагрузку, который
                # тут же подхватит dispatch. До скана — после _scan_vk, чтобы
                # верификация шла по актуальному плану.
                await self._verify_compilations(
                    series_id, await self.repo.items_for_series(series_id))
                # Явный скан — ЕДИНСТВЕННЫЙ путь к загрузке: после скрейпа
                # и усыновления (scan.plan.updated) приказываем downloads
                # докачать недостающее. Открытие композиции и «Сохранить»
                # (download=False) этого НЕ делают — только усыновление.
                if download:
                    self.send_command("downloads.dispatch",
                                      {"series_id": series_id})
            else:
                result = await self._scan_torrents(series, force)
            self._contribute(series_id, scanning=False, error=False)
            return result
        except Exception as exc:
            # Носитель ошибки: error-запись в scan_tasks, если её не
            # оставила торрент-фаза.
            if not await self._has_error_task(series_id):
                task_id = await self.repo.create_task(series_id, [])
                await self.repo.set_error(task_id, str(exc))
            self._contribute(series_id, scanning=False, error=True)
            raise
        finally:
            self._running.discard(series_id)

    async def _has_error_task(self, series_id: int) -> bool:
        return any(t["series_id"] == series_id
                   for t in await self.repo.error_tasks())

    async def _reprocess_names(self, series_id: int) -> None:
        """Переобработка имён до скана — request/reply вместо
        create+sleep-поллинга до 600 с (находка 16)."""
        try:
            await self.request("renaming.reprocess", {"series_id": series_id},
                               timeout=600)
        except BusRequestError as exc:
            if _NO_HANDLER in str(exc):
                self.log.warning("модуль renaming ещё не подключён — "
                                 "переобработка перед сканом пропущена")
            else:
                raise

    # --- VK-ветка ---------------------------------------------------------------

    async def _scan_vk(self, series: dict) -> dict:
        series_id = series["id"]
        channel_url, _, query = series["url"].partition("|")
        reply = await self.request("sources.vk.scan", {
            "channel_url": channel_url, "query": query,
            "search_mode": series.get("vk_search_mode") or "search"},
            timeout=900)
        videos = reply["videos"]

        applied = await self.request("rules.apply", {
            "profile_id": series["parser_profile_id"],
            "titles": [v["title"] for v in videos]}, timeout=300)
        candidates = self._vk_candidates(series_id, videos,
                                         applied["results"])
        stats = await self.repo.upsert_candidates(series_id, candidates)
        self.log.info("VK-скан %d: кандидатов %d (добавлено %d, обновлено "
                      "%d, удалено фантомов %d)", series_id, len(candidates),
                      stats["added"], stats["updated"], stats["deleted"])

        plan_input = await self.repo.candidates(series_id)
        plan = build_plan(plan_input, self._quality_priority(series))
        await self.repo.set_plan_statuses(plan)
        in_plan = sum(1 for s in plan.values() if s.startswith("in_plan"))
        self.publish_event("scan.plan.updated", {"series_id": series_id})
        # «изменения есть» = СОСТАВ кандидатов изменился: появился новый
        # эпизод (added) или пропал (deleted). updated НЕ берём — он растёт
        # на каждом перескане (безусловная перезапись существующих), это не
        # «обновление». Иначе «обновлений нет».
        changed = bool(stats["added"] or stats["deleted"])
        return {"source_type": "vk_video", "candidates": stats,
                "in_plan": in_plan, "changed": changed}

    @staticmethod
    def _quality_priority(series: dict) -> list[int]:
        try:
            return json.loads(series.get("vk_quality_priority") or "[]")
        except (json.JSONDecodeError, TypeError):
            return []

    @staticmethod
    def _vk_candidates(series_id: int, videos: list[dict],
                       results: list[dict]) -> list[dict]:
        """Видео + извлечения правил -> строки media_items. Кандидат
        обязан иметь извлечённый episode или start (семантика
        оригинала); даты — naive UTC '%Y-%m-%d %H:%M:%S' (формат
        хранения прод-БД)."""
        candidates = []
        for video, outcome in zip(videos, results):
            extracted = outcome.get("extracted") or {}
            if outcome.get("excluded"):
                continue
            if extracted.get("episode") is None \
                    and extracted.get("start") is None:
                continue
            pub = datetime.fromisoformat(
                video["publication_date"].replace("Z", "+00:00"))
            item = {
                "series_id": series_id,
                "unique_id": ids.media_unique_id(video["url"], pub, series_id),
                "source_url": video["url"],
                "publication_date": pub.astimezone(timezone.utc)
                                       .strftime("%Y-%m-%d %H:%M:%S"),
                "resolution": video.get("resolution"),
                "source_title": video["title"],
                "season": extracted.get("season"),
                "voiceover_tag": extracted.get("voiceover"),
            }
            if extracted.get("episode") is not None:
                item["episode_start"] = extracted["episode"]
            else:
                item["episode_start"] = extracted["start"]
                if extracted.get("end") is not None:
                    item["episode_end"] = extracted["end"]
            candidates.append(item)
        return candidates

    # --- торрент-ветка -------------------------------------------------------------

    async def _scan_torrents(self, series: dict, force: bool) -> dict:
        series_id = series["id"]
        # Сверка файлов на диске (R1, ситуации F1/F2): пропавший renamed-файл
        # → recheck-задача (восстановление средствами qB). Легаси делал это
        # на КАЖДОМ скане; в новом fs.verify висела только на открытии
        # модалки — пропажа файла сканом не лечилась.
        try:
            await self.request("torrents.fs.verify",
                               {"series_id": series_id}, timeout=120)
            # реконсиляция застрявших раздач (P10): активная, но не докачана
            # и без задачи → загнать в конвейер заново.
            await self.request("torrents.drive_incomplete",
                               {"series_id": series_id}, timeout=120)
        except BusRequestError as exc:
            self.log.warning("сверка файлов перед сканом пропущена: %s", exc)
        if force:
            self.log.warning("force_replace: все активные торренты "
                             "сериала %d будут заменены", series_id)
            await self.request("torrents.db.deactivate_all",
                               {"series_id": series_id}, timeout=120)
        active = await self.request("torrents.db.active",
                                    {"series_id": series_id}, timeout=60)

        parsed = await self.request("sources.parse", {"url": series["url"]},
                                    timeout=300)
        plan_items = self._replace_plan(series, parsed, active)
        if not plan_items:
            self.log.info("сериал %d: новых раздач нет", series_id)
            return {"source_type": "torrent", "tasks_created": 0,
                    "changed": False}

        task_id = await self.repo.create_task(series_id, plan_items)
        return await self._execute_torrent_task(
            task_id, series, plan_items, {})

    def _replace_plan(self, series: dict, parsed: dict,
                      active: list[dict]) -> list[dict]:
        """Факты парсера + активные раздачи -> план замен."""
        releases = []
        for r in parsed.get("releases", []):
            link_for_id = r.get("link") or r.get("magnet")
            if not link_for_id:
                continue
            releases.append({**r, "torrent_id": ids.torrent_id(
                link_for_id, r.get("date_marker"))})

        if series.get("quality"):
            wanted = {q.strip() for q in series["quality"].split(";")
                      if q.strip()}
            releases = [r for r in releases if r.get("quality") in wanted]

        mode = REPLACE_MODE.get(parsed.get("service", ""),
                                DEFAULT_REPLACE_MODE)
        active_ids = {t["torrent_id"] for t in active}
        plan = []
        for rel in releases:
            if rel["torrent_id"] in active_ids:
                continue  # дата не менялась — раздача уже у нас
            old = None
            if mode == "fixed":
                old = next((t for t in active
                            if t.get("episodes") == rel.get("episodes")), None)
            elif mode == "rolling" and len(active) == 1:
                old = active[0]
            plan.append({
                "site_torrent": {
                    "torrent_id": rel["torrent_id"],
                    "link": rel.get("link"),
                    "magnet": rel.get("magnet"),
                    "date_time": rel.get("date_marker"),
                    "quality": rel.get("quality"),
                    "episodes": rel.get("episodes"),
                },
                "old": ({"torrent_id": old["torrent_id"],
                         "qb_hash": old.get("qb_hash"),
                         "db_id": old.get("id")} if old else None),
            })
        return plan

    async def _execute_torrent_task(self, task_id: int, series: dict,
                                    plan_items: list[dict],
                                    results: dict) -> dict:
        """Выполнение журнала: каждый шаг идемпотентен, результаты
        фиксируются по мере — после падения продолжаем отсюда же."""
        series_id = series["id"]
        try:
            for idx, item in enumerate(plan_items):
                if str(idx) in results:
                    continue  # сделано до падения
                st = item["site_torrent"]
                add_payload: dict = {"save_path": series["save_path"],
                                     "paused": True}
                if st.get("link"):
                    file_reply = await self.request(
                        "sources.torrent_file.get",
                        {"url": st["link"], "torrent_id": st["torrent_id"]},
                        timeout=180)
                    add_payload["content_b64"] = file_reply["content_b64"]
                else:
                    add_payload["magnet"] = st["magnet"]
                added = await self.request("torrents.add", add_payload,
                                           timeout=120)
                results[str(idx)] = {"hash": added["hash"],
                                     "link_type": added["link_type"]}
                await self.repo.update_results(task_id, results)

            if not results:
                raise ScanError("не удалось добавить ни одной раздачи")

            created = 0
            for idx_str, res in results.items():
                item = plan_items[int(idx_str)]
                reg = await self.request("torrents.register", {
                    "series_id": series_id,
                    "torrent": item["site_torrent"],
                    "qb_hash": res["hash"],
                    "link_type": res["link_type"],
                    "replaces": item["old"]}, timeout=120)
                # existed=True — тот же infohash (перевыкладка, ПУНКТ 3):
                # ничего не создано, в «изменения» не идёт.
                if not (reg or {}).get("existed"):
                    created += 1

            await self.repo.delete_task(task_id)
            return {"source_type": "torrent", "tasks_created": created,
                    "changed": created > 0}
        except Exception as exc:
            await self.repo.set_error(task_id, str(exc))
            raise

    async def _resume_task(self, task: dict) -> None:
        series_id = task["series_id"]
        if series_id in self._running:
            return
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            await self._execute_torrent_task(
                task["id"], series, task["task_data"],
                dict(task["results_data"]))
            self._contribute(series_id, scanning=False, error=False)
        except Exception:
            self.log.exception("возобновление задачи %d не удалось",
                               task["id"])
            self._contribute(series_id, scanning=False, error=True)
        finally:
            self._running.discard(series_id)

    # --- свёртка статусов (Р-11) ------------------------------------------------

    def _contribute(self, series_id: int, *, scanning: bool,
                    error: bool) -> None:
        self.publish_event("series.status.contribution", {
            "source": "scan", "series_id": series_id,
            "flags": {"scanning": scanning, "error": error}})

    # --- планировщик автосканирования ----------------------------------------------

    async def _scheduler_loop(self) -> None:
        await self._broadcast_status()
        while True:
            await asyncio.sleep(self._tick)
            try:
                await self._scheduler_tick()
            except Exception:  # noqa: BLE001 — цикл не должен умирать
                self.log.exception("ошибка такта планировщика")

    async def _scheduler_tick(self) -> None:
        if self._scan_all_running or self._awaiting_pipeline:
            return
        if await self._setting("scanner_agent_enabled", "false") != "true":
            return
        next_ts = await self._setting("next_scan_timestamp", None)
        if not next_ts:
            return
        next_time = datetime.fromisoformat(next_ts)
        if next_time.tzinfo is None:
            next_time = next_time.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) >= next_time:
            self.log.info("настало время планового сканирования")
            self._spawn(self._scan_all())

    async def _scan_all(self, force: bool = False) -> None:
        self._scan_all_running = True
        await self._broadcast_status()
        try:
            all_series = await self.request("catalog.series.list", {},
                                            timeout=30)
            for s in all_series:
                if not s.get("auto_scan_enabled"):
                    continue
                if await self._pipeline_busy(s["id"]):
                    self.log.info("автоскан: сериал %d пропущен — у него "
                                  "активные задачи конвейера", s["id"])
                    continue
                try:
                    await self._run_series(s["id"], force)
                except Exception as exc:  # noqa: BLE001 — проход не прерываем
                    self.log.error("автоскан сериала %d: %s", s["id"], exc)
        finally:
            self._scan_all_running = False
            self._awaiting_pipeline = True
            await self._broadcast_status()
            await self._check_pipeline_empty()

    async def _pipeline_busy(self, series_id: int) -> bool:
        """Поведение старого автоскана: сериал с активными задачами
        конвейера не сканируется (ручной скан — без этой проверки)."""
        try:
            queue = await self.request("torrents.queue.get",
                                       {"series_id": series_id}, timeout=10)
            return queue.get("count", 0) > 0
        except BusRequestError as exc:
            if _NO_HANDLER in str(exc):
                return False
            raise

    async def _check_pipeline_empty(self) -> None:
        """После прохода: если конвейер уже пуст — назначить следующий
        скан сразу, не дожидаясь события."""
        try:
            queue = await self.request("torrents.queue.get", {}, timeout=10)
            count = queue.get("count", 0) if isinstance(queue, dict) \
                else len(queue)
        except BusRequestError as exc:
            if _NO_HANDLER not in str(exc):
                raise
            self.log.warning("конвейер torrents ещё не подключён — "
                             "следующий скан назначается сразу")
            count = 0
        if count == 0:
            await self._schedule_next()

    async def on_queue_changed(self, env: Envelope) -> None:
        if self._awaiting_pipeline and env.payload.get("count", 0) == 0:
            self.log.info("конвейер опустел — назначаю следующий скан")
            await self._schedule_next()

    async def _schedule_next(self) -> None:
        self._awaiting_pipeline = False
        interval = int(await self._setting("scan_interval_minutes", "60"))
        next_time = datetime.now(timezone.utc) + timedelta(minutes=interval)
        await self.request("settings.value.set", {
            "key": "next_scan_timestamp", "value": next_time.isoformat()},
            timeout=10)
        self.log.info("следующее сканирование: %s", next_time.isoformat())
        await self._broadcast_status()

    async def _setting(self, key: str, default: str | None) -> str | None:
        reply = await self.request("settings.value.get", {"key": key},
                                   timeout=10)
        value = reply.get("value")
        return value if value is not None else default

    async def _status_payload(self) -> dict:
        """Форма старого scanner_status_update / GET /api/scanner/status."""
        return {
            "scanner_enabled":
                await self._setting("scanner_agent_enabled", "false") == "true",
            "scan_interval":
                int(await self._setting("scan_interval_minutes", "60")),
            "is_scanning": self._scan_all_running or bool(self._running),
            "is_awaiting_tasks": self._awaiting_pipeline,
            "next_scan_time": await self._setting("next_scan_timestamp", None),
        }

    async def _broadcast_status(self) -> None:
        """Контракт старого scanner_status_update (индикатор сканера)."""
        self.publish_event("scan.status.changed",
                           await self._status_payload())
