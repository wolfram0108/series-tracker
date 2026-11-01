"""Module scan — the orchestrator of scans: the schedule, the torrent scan, and, for a series of kind `video`, the
synchronization of its sources and the choice of the reference of every episode (stage 10).

A video series is not read here. The tables of its pages are read by `sources`, the numbers come from the constructor
through `composition`; scan only waits for the tables, asks for the layout, chooses the reference of every slot
(season, episode, part) from it (`reference.py`) and keeps the plan: the rows of media_items (`candidates.py`,
`planner.py`) and the outcome of the choice in `scan_references`.

Queries:
  scan.series.run {series_id, force_replace?, download?} → {changed, ...}. changed tells whether anything was
      actually changed: torrent — tasks were created; video — the references or the rows of the plan differ from
      what they were before the run. A video series answers {kind: "video", changed, sources: [{source_id, url,
      state: ready | stale | skipped, reason}], references: {by_level, own, other_player, none, unfit}, in_plan}.
      `stale` — the read of the page failed and the last complete table stays in force; `skipped` — it failed and there is no table.
      A scan of a series that is being scanned is refused ("уже запущен", finding 27). The steps of a video series
      are tied by signals: renaming.reprocess, downloads.fs.sync, sources.table.get for every source at once and
      the wait for sources.table.ready / .failed of its page, composition.recompute (its answer means the layout is
      counted from these tables), composition.get with voices.settings.get, the choice and the plan, and with
      download (the default) downloads.dispatch and voices.dispatch. A failed step is the one signal of failure:
      the error carrier (scan_tasks.status='error') holds its reason. The wait for a table has a safeguard
      (TABLE_WAIT_SAFEGUARD_S): when it runs out the source counts as failed, reason `table_wait_timeout`.
  scan.series.prepare {series_id} → the summary of a torrent scan without a download: the torrent is added to
      qBittorrent, split into its files (torrent_files) and left paused — the user picks the episodes in the
      composition, the next scan starts the download.
  scan.output.get {series_id} → [Reference]: every slot of the series. One Reference per slot: an episode with
      parts has one per part. Marks: by_level, own, other_player, none, unfit, and `off` for a slot the user switched
      off (an episode or a season switched off, or a row of the plan switched off): it takes nothing and plans no
      voice-over (core/contracts/reference.py).
  scan.output.switch {series_id, season, episode?, off} → {success}: switches an episode (without `episode`, a whole
      season) off or on. The memory of the series (ignored_seasons, ignored_episodes) and the rows of the plan change
      together, so the choice holds for the rows that exist and for those a new release brings; switching a season on
      switches on all of it. What changed goes out as scan.reference.changed. A series the catalog does not have is
      refused with the catalog's own answer.

Commands:
  scan.all.start {} — a pass over all series with auto_scan_enabled (what the internal scheduler does by the
      schedule from the settings).

Events (outgoing):
  scan.plan.updated {series_id} — the plan was counted again; downloads only adopts finished files on it (it does NOT
      download). The download is started by an explicit scan alone (downloads.dispatch).
  scan.reference.changed (ReferenceChanged: series_id and the whole Reference) — the reference or the mark of a slot
      differs from the stored one (a slot whose row left the layout reports `none` with the reason `reference_none`; a
      slot the user switched off reports `off` with the reason `reference_off`); sent
      after the write. Voices follow it, a window shows it without a second request.
  scan.status.changed {scanner_enabled, scan_interval, is_scanning, is_awaiting_tasks, next_scan_time} — the state
      of the scheduler (the contract of the old scanner_status_update).
  series.status.contribution — the fold of scanning/error (R-11); the carrier of the error is scan_tasks.status='error'.
  series.purged {series_id, owner} — report: the tables of the series are cleaned.

Events (incoming):
  composition.changed {series_id, rev, episodes, summary_only} — the layout or the levers changed: the references
      and the plan of the series are counted again (the whole series: the count is a pure function, an episode the
      change did not touch comes out the same). Never starts a download. `summary_only` is skipped.
  voices.settings.changed — the order of players is an input of the choice: every video series is counted again.
  sources.table.ready / .failed {url, ...} — the verdicts a synchronization waits for.
  torrents.queue.changed {count} — the pipeline is empty: after a full pass the next scan is set when count=0.

Neighbours (all over the bus): catalog.series.get / .list (kind, sources), composition.get / .recompute,
voices.settings.get (the order of players), sources.table.get, downloads.dispatch / .fs.sync / .queue.get,
voices.dispatch, renaming.reprocess. Scan asks nothing of composition at start: its state is its own database.

Resumability: the plan of replacements of a torrent scan is in scan_tasks BEFORE it is carried out; additions are
idempotent (local infohash, R-2: adding again = existed); on_start goes on with the unfinished tasks. A video series
keeps its references and rows in the database; what a restart interrupted is brought back by the events of
composition and by the next scan.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from core import BaseModule, BusRequestError, ids, torrent_source
from core.contracts import CompositionReply, OutputSwitch, Reference, ReferenceChanged, SeriesSource, VoiceSettingsView
from core.db import Database
from core.envelope import Envelope
from core.voice_names import Groups
from core.ignored import parse_memory
from core.reasons import reason
from . import candidates, output, planner, reference, sync
from .repository import ScanRepository
from .sync import TABLE_WAIT_SAFEGUARD_S, TableSignals

# Режим замены раздач по трекерам (ревизия находки 28 — вместо
# подстроки в оркестраторе). fixed: новая раздача заменяет активную с
# теми же episodes; rolling: при единственной активной заменяется она,
# при нескольких — осознанно не заменяем (неоднозначно, что устарело).
REPLACE_MODE = {"astar": "fixed"}
DEFAULT_REPLACE_MODE = "rolling"

# Safeguards of requests, not syncs: an answer that is not there by then fails the step.
STEP_TIMEOUT_S = 30.0
# Counting a layout asks the constructor once per source, up to 300 s each.
RECOMPUTE_TIMEOUT_S = 900.0

_NO_HANDLER = "нет обработчика"


class ScanError(RuntimeError):
    pass


class SyncFailed(ScanError):
    """A step of a synchronization failed; the message is a reason (core/reasons), not a sentence."""


class ScanAborted(RuntimeError):
    """Серию удалили во время скана — работа прекращается (Д2)."""


@dataclass(frozen=True)
class Refreshed:
    """What a count of the references and the plan came to."""

    in_plan: int
    references: list[dict]
    plan_changed: bool


def _sources_of(series: dict) -> list[SeriesSource]:
    return [SeriesSource.model_validate(each) for each in series.get("sources", [])]


def _torrent_source(series: dict) -> SeriesSource:
    """The source of a torrent series: the address and the profile of a torrent live in the source (Д-38)."""
    found = torrent_source(series)
    if found is None:
        raise ScanError(f"у сериала «{series.get('name')}» нет торрент-источника")
    return SeriesSource.model_validate(found)


class ScanModule(BaseModule):
    name = "scan"

    def __init__(self, bus, db: Database, *, scheduler_tick: float | None = 10.0,
                 table_wait_s: float = TABLE_WAIT_SAFEGUARD_S) -> None:
        self.repo = ScanRepository(db)
        self._tick = scheduler_tick  # None — без планировщика (тесты)
        self._table_wait_s = table_wait_s
        self._signals = TableSignals()
        self._locks: dict[int, asyncio.Lock] = {}
        self._running: set[int] = set()
        self._aborted: set[int] = set()
        self._scan_all_running = False
        self._awaiting_pipeline = False
        super().__init__(bus)

    def register(self) -> None:
        # concurrent: долгий скан одного сериала не должен блокировать
        # очередь запросов к модулю (защита от гонки — set _running).
        self.handle("scan.series.run", self.on_run, concurrent=True)
        self.handle("scan.series.prepare", self.on_prepare, concurrent=True)
        self.handle("scan.all.start", self.on_scan_all)
        self.handle("scan.media.list", self.on_media_list)
        self.handle("scan.media.downloaded_counts", self.on_downloaded_counts)
        self.handle("scan.seasons", self.on_seasons)
        self.handle("scan.item.set_ignored", self.on_set_ignored)
        self.handle("scan.items.set_ignored_bulk", self.on_set_ignored_bulk)
        self.handle("scan.output.switch", self.on_output_switch)
        self.handle("scan.output.get", self.on_output_get)
        self.handle("scan.status.get", self.on_status_get)
        self.handle("composition.changed", self.on_composition_changed)
        self.handle("voices.settings.changed", self.on_voice_settings_changed)
        self.handle("sources.table.ready", self.on_table_ready)
        self.handle("sources.table.failed", self.on_table_failed)
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
        """is_ignored_by_user is our column; the UI and slicing set it.

        Switching a row off changes the real plan of the download, so scan.plan.updated goes out and downloads
        counts its tasks and the fold of statuses again (R-21). The row also leaves the output, so the references
        that changed with it are announced."""
        item = await self.repo.get_item(env.payload["unique_id"])
        if item is None:
            return
        ignored = bool(env.payload["is_ignored"])
        await self._announcing(item["series_id"], self.repo.set_ignored(item["unique_id"], ignored))
        self.publish_event("scan.plan.updated", {"series_id": item["series_id"]})

    async def on_set_ignored_bulk(self, env: Envelope) -> dict:
        """Второй, независимый от ignored_seasons контрол: разовая массовая
        правка is_ignored_by_user по текущим элементам ОДНОГО сезона.
        ignored_seasons (декларативная политика, влияет и на будущие
        эпизоды) не трогаем — здесь только снимок media_items на сейчас,
        новые эпизоды сезона по умолчанию останутся is_ignored_by_user=0."""
        series_id = env.payload["series_id"]
        n = await self._announcing(series_id, self.repo.set_ignored_bulk_for_season(
            series_id, int(env.payload["season"]), bool(env.payload["is_ignored"])))
        self.publish_event("scan.plan.updated", {"series_id": series_id})
        return {"updated": n}

    async def on_output_switch(self, env: Envelope) -> dict:
        """An episode, or a whole season, is switched off or on by the user. The memory of the series and the rows of
        the plan change together, so that the choice holds for the rows that are there and for those that come with a
        new release; what the output shows of it goes out as references. Switching a season on switches on all of it."""
        request = OutputSwitch.model_validate(env.payload)
        series_id = request.series_id

        async def apply() -> None:
            series = await self.request("catalog.series.get", {"series_id": series_id}, timeout=STEP_TIMEOUT_S)
            seasons, memory = self._ignored_seasons(series), self._ignored_episodes(series)
            if request.episode is None:
                seasons = seasons | {request.season} if request.off else seasons - {request.season}
                if not request.off:
                    memory.pop(request.season, None)
                    await self.repo.set_ignored_bulk_for_season(series_id, request.season, False)
            else:
                numbers = memory.get(request.season, set())
                numbers = numbers | {request.episode} if request.off else numbers - {request.episode}
                memory[request.season] = numbers
                await self.repo.set_ignored_for_episode(series_id, request.season, request.episode, request.off)
            await self.request("catalog.series.update", {"series_id": series_id, "fields": {
                "ignored_seasons": json.dumps(sorted(seasons)),
                "ignored_episodes": json.dumps({str(season): sorted(numbers) for season, numbers in memory.items() if numbers}),
            }}, timeout=STEP_TIMEOUT_S)

        await self._announcing(series_id, apply())
        self.publish_event("scan.plan.updated", {"series_id": series_id})
        return {"success": True}

    async def on_output_get(self, env: Envelope) -> list[dict]:
        """The references of the series that are wanted, in the order of season, episode, part."""
        series_id = int(env.payload["series_id"])
        series = await self.request("catalog.series.get", {"series_id": series_id}, timeout=STEP_TIMEOUT_S)
        shown = await self._output_of(series)
        return [output.reference_of(each).model_dump(mode="json") for each in shown]

    async def _output_of(self, series: dict) -> list[dict]:
        items = {each["unique_id"]: each for each in await self.repo.items_for_series(series["id"])}
        return output.shown(await self.repo.references(series["id"]), items, self._ignored_episodes(series),
                            self._ignored_seasons(series))

    async def _announcing(self, series_id: int, action):
        """Awaits `action` (a change of the rows that may switch a reference off) and announces what changed in the
        output of the series. A series the catalog does not have, or a torrent one, has no output to announce."""
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id}, timeout=STEP_TIMEOUT_S)
            before = await self._output_of(series)
        except (BusRequestError, asyncio.TimeoutError):
            return await action
        result = await action
        # The action may have changed the memory of the series: the output after it is read with the series as it is now.
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id}, timeout=STEP_TIMEOUT_S)
            after = await self._output_of(series)
        except (BusRequestError, asyncio.TimeoutError):
            return result
        for each in output.changes(before, after):
            self._announce(series_id, each)
        return result

    def _announce(self, series_id: int, changed: Reference) -> None:
        self.publish_event("scan.reference.changed", ReferenceChanged(
            series_id=series_id, **changed.model_dump()).model_dump(mode="json"))

    async def on_downloaded_counts(self, env: Envelope) -> dict:
        """{counts: {series_id: n}} — скачанные VK-эпизоды (Р-19)."""
        return {"counts": await self.repo.downloaded_counts()}

    async def on_seasons(self, env: Envelope) -> dict:
        """{seasons: [..]} — реальные сезоны VK-серии (distinct media_items.
        season) для агрегата TMDB по нескольким сезонам."""
        return {"seasons": await self.repo.seasons_for_series(
            env.payload["series_id"])}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит media_items и scan_tasks; идущий
        скан серии прерывается (Д2 — как у загрузки, нарезки и переноса)."""
        series_id = env.payload["series_id"]
        self._aborted.add(series_id)
        # A count of the plan that is writing finishes first; one that comes after sees the series aborted.
        async with self._lock(series_id):
            await self.repo.delete_for_series(series_id)
        self._locks.pop(series_id, None)
        self.publish_event("series.purged",
                           {"series_id": series_id, "owner": self.name})

    async def on_prepare(self, env: Envelope) -> dict:
        """Контракт «Получить данные» торрент-композиции: раздача попадает
        в клиент и разбирается на состав, закачка не начинается."""
        return await self._prepare_series(int(env.payload["series_id"]))

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

    # --- signals of the neighbours ---------------------------------------------------------------

    def on_table_ready(self, env: Envelope) -> None:
        self._signals.ready(env.payload["url"])

    def on_table_failed(self, env: Envelope) -> None:
        self._signals.failed(env.payload["url"], env.payload.get("reason"))

    async def on_composition_changed(self, env: Envelope) -> None:
        """The layout or the levers changed: the references and the plan follow, nothing is downloaded."""
        if env.payload.get("summary_only"):
            return  # no row and no lever moved, only what a window shows
        await self._follow_layout(int(env.payload["series_id"]))

    async def on_voice_settings_changed(self, env: Envelope) -> None:
        """The order of players decides ties of the choice, so a new order may change references."""
        try:
            listing = await self.request("catalog.series.list", {}, timeout=STEP_TIMEOUT_S)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.warning("порядок плееров изменён, список сериалов недоступен: %s", exc)
            return
        for series in listing:
            if series.get("kind") == "video":
                await self._follow_layout(series["id"])

    async def _follow_layout(self, series_id: int) -> None:
        """Counts the references of a video series again after an event; a failure is logged, the next event or the
        next scan counts again."""
        try:
            series = await self.request("catalog.series.get", {"series_id": series_id}, timeout=STEP_TIMEOUT_S)
            if series.get("kind") == "video":
                await self._refresh(series)
        except ScanAborted as exc:
            self.log.info("пересчёт референсов прерван: %s", exc)
        except (BusRequestError, asyncio.TimeoutError, ScanError) as exc:
            self.log.warning("сериал %d: референсы не пересчитаны: %s", series_id, exc)
        except Exception:  # noqa: BLE001 — one series must not stop the handling of the events of the others
            self.log.exception("сериал %d: сбой пересчёта референсов", series_id)

    def _lock(self, series_id: int) -> asyncio.Lock:
        return self._locks.setdefault(series_id, asyncio.Lock())

    def _alive(self, series_id: int) -> None:
        if series_id in self._aborted:
            raise ScanAborted(f"сериал {series_id} удалён во время скана")

    async def _step(self, topic: str, payload: dict, *, timeout: float = STEP_TIMEOUT_S):
        """A request of a synchronization: any failure of it is the one signal that the step failed."""
        try:
            return await self.request(topic, payload, timeout=timeout)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            if "LookupError" in str(exc):
                raise ScanAborted(f"сериал {payload.get('series_id')} удалён во время скана") from exc
            self.log.error("синхронизация: запрос %s не выполнен: %s: %s", topic, type(exc).__name__, exc)
            raise SyncFailed(reason("scan_step_failed", step=topic)) from exc

    async def _run_series(self, series_id: int, force: bool = False,
                          download: bool = True) -> dict:
        if series_id in self._running:
            raise ScanError(f"скан сериала {series_id} уже запущен")
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            video = series.get("kind") == "video"
            if video:
                if not any(each.parser_profile_id for each in _sources_of(series)):
                    raise SyncFailed(reason("scan_no_profile"))
            elif not _torrent_source(series).parser_profile_id:
                raise ScanError(
                    f"для сериала «{series.get('name')}» не назначен "
                    "профиль правил")
            # Прошлая ошибка сбрасывается новым сканом (Р-11).
            await self.repo.delete_error_tasks(series_id)
            await self._reprocess_names(series_id)
            if video:
                result = await self._sync_video(series, download)
            else:
                result = await self._scan_torrents(series, _torrent_source(series), force)
            self._contribute(series_id, scanning=False, error=False)
            # Многосезонный агрегат TMDB: реальные сезоны могли измениться
            # (новый сезон/файлы) — пересчитать число серий по сезонам.
            self.send_command("metadata.seasons.recompute",
                              {"series_id": series_id})
            return result
        except ScanAborted as exc:
            self.log.info("скан сериала %d прерван: %s", series_id, exc)
            self._contribute(series_id, scanning=False, error=False)
            return {"changed": False, "aborted": True}
        except Exception as exc:
            # Носитель ошибки: error-запись в scan_tasks, если её не
            # оставила торрент-фаза. Серии нет — носитель не пишем.
            if await self._series_alive(series_id) \
                    and not await self._has_error_task(series_id):
                task_id = await self.repo.create_task(series_id, [])
                await self.repo.set_error(task_id, str(exc))
            self._contribute(series_id, scanning=False, error=True)
            raise
        finally:
            self._running.discard(series_id)

    async def _prepare_series(self, series_id: int) -> dict:
        """Состав раздачи для композиции без запуска закачки.

        Отдельный путь, а не флаг скана: здесь нет ни переобработки имён,
        ни диспетчеризации — только «добудь раздачу и покажи, что в ней»."""
        if series_id in self._running:
            raise ScanError(f"скан сериала {series_id} уже запущен")
        self._running.add(series_id)
        self._contribute(series_id, scanning=True, error=False)
        try:
            series = await self.request("catalog.series.get",
                                        {"series_id": series_id})
            if series.get("kind") == "video":
                raise ScanError("подготовка состава — только для торрентов")
            await self.repo.delete_error_tasks(series_id)
            result = await self._scan_torrents(series, _torrent_source(series), False, prepare=True)
            self._contribute(series_id, scanning=False, error=False)
            return result
        except ScanAborted as exc:
            self.log.info("подготовка сериала %d прервана: %s", series_id, exc)
            self._contribute(series_id, scanning=False, error=False)
            return {"source_type": "torrent", "tasks_created": 0,
                    "changed": False, "aborted": True}
        except Exception as exc:
            if await self._series_alive(series_id) \
                    and not await self._has_error_task(series_id):
                task_id = await self.repo.create_task(series_id, [])
                await self.repo.set_error(task_id, str(exc))
            self._contribute(series_id, scanning=False, error=True)
            raise
        finally:
            self._running.discard(series_id)

    async def _has_error_task(self, series_id: int) -> bool:
        return any(t["series_id"] == series_id
                   for t in await self.repo.error_tasks())

    async def _series_alive(self, series_id: int) -> bool:
        """Есть ли серия в каталоге: носитель ошибки пишется только
        существующей серии — иначе внешний ключ не даст записать."""
        try:
            await self.request("catalog.series.get",
                                {"series_id": series_id}, timeout=10)
            return True
        except BusRequestError:
            return False

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

    # --- video branch: synchronisation and the plan of references ----------------------------------

    async def _sync_video(self, series: dict, download: bool) -> dict:
        """Tables of all sources -> layout -> references and plan -> (with download) the download and the voice-overs.

        Each step starts on the signal that the one before it ended, and any step that fails fails the scan."""
        series_id = series["id"]
        # Disk check before the scan: a downloaded file that is gone is reset from completed to pending so that dispatch
        # fetches it again; an unreachable path is absorbed inside downloads.on_fs_sync.
        try:
            await self.request("downloads.fs.sync", {"series_id": series_id}, timeout=120)
        except BusRequestError as exc:
            self.log.warning("сверка диска перед видео-сканом пропущена: %s", exc)
        before = await self._fingerprint(series_id)
        self._alive(series_id)
        tables = await sync.sync_tables(_sources_of(series), self.request, self._signals, self._table_wait_s)
        for each in tables:
            if each.reason:
                self.log.warning("сериал %d, источник %d: страница %s — %s, причина %s", series_id, each.source_id,
                                 each.url, each.state, each.reason)
        self._alive(series_id)
        await self._step("composition.recompute", {"series_id": series_id}, timeout=RECOMPUTE_TIMEOUT_S)
        self._alive(series_id)
        refreshed = await self._refresh(series, announce=True)
        changed = before != await self._fingerprint(series_id)
        # An explicit scan is the only way to a download: after the plan, downloads is told to fetch what is missing and
        # voices to take the voice-overs of the same episodes; a plain save (download=False) and a recount on an event do not.
        if download:
            self.send_command("downloads.dispatch", {"series_id": series_id})
            self.send_command("voices.dispatch", {"series_id": series_id})
        return {"kind": "video", "changed": changed, "sources": [each.as_dict() for each in tables],
                "references": output.marks(refreshed.references), "in_plan": refreshed.in_plan}

    async def _fingerprint(self, series_id: int) -> tuple:
        """The references and the rows of the plan as stored: two equal fingerprints mean the run changed nothing."""
        records = await self.repo.references(series_id)
        rows = sorted(each["unique_id"] for each in await self.repo.items_for_series(series_id))
        return tuple(tuple(each.items()) for each in records), tuple(rows)

    async def _refresh(self, series: dict, *, announce: bool = False) -> Refreshed:
        """Counts the references of the series from the layout as it stands, writes them with the plan in one
        transaction and announces what changed. Counting twice with the same inputs changes nothing."""
        series_id = series["id"]
        async with self._lock(series_id):
            self._alive(series_id)
            layout = CompositionReply.model_validate(await self._step("composition.get", {"series_id": series_id}))
            voices = VoiceSettingsView.model_validate(await self._step("voices.settings.get", {}))
            existing = await self.repo.items_for_series(series_id)
            stored = await self.repo.references(series_id)
            chosen = reference.choose(layout.rows, layout.settings, voices.priority,
                                      [each.source_id for each in layout.sources],
                                      previous=[output.reference_of(each) for each in stored],
                                      player_groups=Groups.of(voices.player_groups))
            memory, seasons = self._ignored_episodes(series), self._ignored_seasons(series)
            placements = candidates.place(series_id, chosen, existing)
            winners = {each.slot: each.unique_id for each in placements}
            by_id = {each["unique_id"]: each for each in existing}
            inserts = [candidates.new_row(series_id, each, memory) for each in placements if each.is_new]
            updates = []
            for each in placements:
                if not each.is_new:
                    changed = {name: value for name, value in each.fields.items() if by_id[each.unique_id].get(name) != value}
                    if changed:
                        updates.append((each.unique_id, changed))
            deletes = await self._leftovers(series_id, existing, set(winners.values()))
            rows = candidates.rows_after(existing, inserts, updates, deletes)
            statuses, superseded = planner.plan_with_supersession(rows, winners, seasons)
            records = [{**each.reference.model_dump(mode="json"), "unique_id": winners.get(each.slot)}
                       for each in chosen]
            shown_before = output.shown(stored, by_id, memory, seasons)
            shown_after = output.shown(records, {each["unique_id"]: each for each in rows}, memory, seasons)
            plan_changed = bool(inserts or updates or deletes) or any(
                (by_id[unique_id].get("plan_status"), by_id[unique_id].get("superseded_by"))
                != (statuses.get(unique_id, "candidate"), superseded.get(unique_id))
                for unique_id in by_id if unique_id not in deletes)
            self._alive(series_id)
            try:
                await self.repo.save_outcome(series_id, inserts=inserts, updates=updates, deletes=deletes,
                                             statuses=statuses, superseded=superseded, references=records)
            except Exception as exc:
                if "FOREIGN KEY" in str(exc):
                    raise ScanAborted(f"сериал {series_id} удалён во время скана") from exc
                raise
        self.log.info("сериал %d: раскладка %d, слотов %d, в плане %d, добавлено строк %d, убрано %d", series_id,
                      layout.rev, len(chosen), sum(1 for each in statuses.values() if each == "in_plan_single"),
                      len(inserts), len(deletes))
        if announce or plan_changed:
            self.publish_event("scan.plan.updated", {"series_id": series_id})
        for each in output.changes(shown_before, shown_after):
            self._announce(series_id, each)
        return Refreshed(sum(1 for each in statuses.values() if each == "in_plan_single"), shown_after, plan_changed)

    async def _leftovers(self, series_id: int, existing: list[dict], kept: set[str]) -> list[str]:
        """The rows to delete: those that stand for nothing and have no task in the queue of downloads. When the
        queue cannot be told, nothing is deleted this time, so that no task is left without its row."""
        if not candidates.obsolete(existing, kept, ()):
            return []
        try:
            queue = await self.request("downloads.queue.get", {}, timeout=STEP_TIMEOUT_S)
        except BusRequestError as exc:
            if _NO_HANDLER not in str(exc):
                self.log.warning("сериал %d: очередь загрузок недоступна, лишние строки оставлены: %s", series_id, exc)
                return []
            queue = {"tasks": []}
        except asyncio.TimeoutError:
            self.log.warning("сериал %d: очередь загрузок не ответила, лишние строки оставлены", series_id)
            return []
        busy = {task["task_key"] for task in queue.get("tasks", []) if task.get("series_id") == series_id}
        return candidates.obsolete(existing, kept, busy)

    @staticmethod
    def _ignored_episodes(series: dict) -> dict[int, set[int]]:
        """Episodes the user switched off in advance, as for torrents: their rows are created switched off,
        so neither the download nor the voice-overs take them."""
        try:
            return parse_memory(json.loads(series.get("ignored_episodes") or "{}"))
        except (json.JSONDecodeError, TypeError):
            return {}

    @staticmethod
    def _ignored_seasons(series: dict) -> set[int]:
        """Сезоны, помеченные пользователем к игнору (VU11): их эпизоды в
        план не попадают."""
        try:
            return {int(s) for s in
                    json.loads(series.get("ignored_seasons") or "[]")}
        except (json.JSONDecodeError, TypeError, ValueError):
            return set()

    # --- торрент-ветка -------------------------------------------------------------

    async def _scan_torrents(self, series: dict, source: SeriesSource, force: bool,
                             prepare: bool = False) -> dict:
        series_id = series["id"]
        # Сверка файлов на диске (R1, ситуации F1/F2): пропавший renamed-файл
        # → recheck-задача (восстановление средствами qB). Легаси делал это
        # на КАЖДОМ скане; в новом fs.verify висела только на открытии
        # модалки — пропажа файла сканом не лечилась.
        try:
            await self.request("torrents.fs.verify",
                               {"series_id": series_id}, timeout=120)
            # реконсиляция застрявших раздач (P10): активная, но не докачана
            # и без задачи → загнать в конвейер заново. В режиме подготовки
            # пропускается: она бы сняла паузу до выбора серий.
            if not prepare:
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

        parsed = await self.request("sources.parse", {"url": source.url},
                                    timeout=300)
        if series_id in self._aborted:
            raise ScanAborted(f"сериал {series_id} удалён во время скана")
        plan_items = self._replace_plan(series, parsed, active)
        if not plan_items:
            self.log.info("сериал %d: новых раздач нет", series_id)
            return {"source_type": "torrent", "tasks_created": 0,
                    "changed": False}

        task_id = await self.repo.create_task(series_id, plan_items)
        return await self._execute_torrent_task(
            task_id, series, plan_items, {}, prepare=prepare)

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
                                    results: dict,
                                    prepare: bool = False) -> dict:
        """Выполнение журнала: каждый шаг идемпотентен, результаты
        фиксируются по мере — после падения продолжаем отсюда же.

        prepare — раздача добавляется и разбирается на состав, но в
        конвейер не идёт: закачку запускает следующий скан."""
        series_id = series["id"]
        try:
            for idx, item in enumerate(plan_items):
                if series_id in self._aborted:
                    raise ScanAborted(f"сериал {series_id} удалён во время скана")
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
                    "replaces": item["old"],
                    "start_pipeline": not prepare}, timeout=600)
                # existed=True — тот же infohash (перевыкладка, ПУНКТ 3):
                # ничего не создано, в «изменения» не идёт.
                if not (reg or {}).get("existed"):
                    created += 1

            await self.repo.delete_task(task_id)
            return {"source_type": "torrent", "tasks_created": created,
                    "changed": created > 0}
        except ScanAborted:
            raise
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
