"""Module voices: the voice-overs of a series (mission voice-alignment, stages 4 and 6).

Owns voice_dubs: one row per (series, episode, voice) — the additional voice-overs that are
downloaded next to the reference video and later aligned to it.

Query:
  voices.layout {sources: [{url, player, studio, kind, episodes, links?}], mode?, reference?}
      -> {mode, reference, voices: [{key, label, role, source, fallbacks}], skipped, priority}
      The source priority is the setting `voices.source_priority` (JSON list of player kinds);
      layout.DEFAULT_PRIORITY when unset.
  voices.dubs.get {series_id} -> {items: [{season, episode, voice_key, voice_label, status,
      attempt, error, temp_path, job_id, aligned_path, sidecar_path, outcome, ref_uid, ref_path,
      detail, state}]}  (detail = outcome or error: what the interface shows next to a state)
Command:
  voices.dispatch {series_id} — sent by scan after an explicit scan: read the catalog of the site,
      lay the voices out around the selected reference, record a voice_dubs row per planned
      reference episode and voice, and download what is not on disk yet into the temporary
      directory (setting `voices.temp_root`, default /nas/torrents/series-tracker).
Events in:  downloads.fetch.finished (our keys `dub:<id>`), conform.queue.changed (waiting or running
            jobs of the service), series.downloaded.changed,
            downloads.item.renamed, library.relocation.finished, conform.job.finished,
            conform.daemon.up, series.deleted.
Events out: voices.dub.changed {series_id, season, episode, voice_key, voice_label, status, state, detail} —
            state is what the user sees (states.py: planned, queued, running, done, error), status the
            step behind it; detail is the reason of a failure, a rejection or a stale track, else null;
            status is one of
            ready, aligning, aligned, placed, rejected, failed, moved (the track followed its video),
            stale (its reference was replaced) and removed (the episode and its video are gone);
            series.purged,
            voices.episode.settled {series_id, season, episode} — every voice-over of the episode is
            placed, rejected or failed and its temporary files are gone.

Downloading is delegated to downloads.fetch. A dub tries its main source first, then the other
sources of the same voice by priority; when none gives the file the row is `failed`, a warning is
logged, and the next dispatch offers it again from the main source. A dub is `ready` once its file
is on disk; rows left `downloading` by a restart are sent again on start.

Alignment (stage 7). An episode goes to the alignment service as one job once its reference video
is on disk and none of its voice-overs is still being downloaded or aligned: the `ready` ones are
sent (conform.submit). A voice-over that was not downloaded does not hold the episode back, and one
that arrives later makes a job of its own. When a job ends, a voice-over that converged and raised
neither `suspect` nor `critical` becomes `aligned`; any other is `rejected` with the reason and a
job that failed makes its voice-overs `failed` (the next scan offers them again). An `aligned`
track is moved next to the reference video as `<video name>.<voice title>.<lang>.<ext>` and the row
becomes `placed` with the track's path; the series stays playable at every step. When every
voice-over of the episode is placed, rejected or failed, the episode's temporary directory is
removed. Without the service, or while the reference is missing, rows wait and are retried on
series.downloaded.changed and conform.daemon.up. Following the reference (stage 8). A track is valid for the reference it was aligned to (`ref_uid`).
The same reference under another name or in another directory: the track is moved after it. A
different reference (a higher resolution, another voice or source took the slot): every track
of the episode is removed from the library at once, since it would play out of step with the new
video, and the voice-overs start over against the new reference. An episode that left the plan
and whose video is gone loses its whole set; a track the user deleted is not made again.
Deleting a series removes only records and temporary files: videos stay, and so do their tracks.

The service leaves an empty `_tmp` directory
next to every reference video it worked on; it is removed once no job of the series is running.
The series' own folder in the temporary directory goes when every voice-over of the series is
settled and the folder is empty.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections import defaultdict

from core import BaseModule, BusRequestError, is_video_source
from core.db import Database
from core.envelope import Envelope

from .layout import DEFAULT_PRIORITY, download_chain, plan_layout
from .placement import move, place, sidecar_path
from .repository import VoicesRepository
from .states import public_state

PRIORITY_KEY = "voices.source_priority"
TEMP_ROOT_KEY = "voices.temp_root"
DEFAULT_TEMP_ROOT = "/nas/torrents/series-tracker"
DUB_MAX_HEIGHT = 720
PARSE_TIMEOUT_S = 180
SUBMIT_TIMEOUT_S = 60
SUBMIT_RETRY_S = 30.0
SUBMIT_RETRIES = 5
SCRATCH_DIR = "_tmp"
WORKING = ("pending", "downloading", "aligning")
FINAL = ("placed", "rejected", "failed")


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "dub"


def episode_dir(root: str, series_id: int, season: int, episode: int) -> str:
    return os.path.join(root, str(series_id), f"s{season:02d}e{episode:02d}")


def temp_path(root: str, series_id: int, season: int, episode: int, voice_key: str) -> str:
    return os.path.join(episode_dir(root, series_id, season, episode), f"{_safe(voice_key)}.mp4")


def stem(path: str | None) -> str:
    return os.path.splitext(os.path.basename(path or ""))[0]


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db: Database | None = None) -> None:
        self.repo = VoicesRepository(db) if db is not None else None
        self._locks: dict[int, asyncio.Lock] = {}
        self._kicked: set[int] = set()
        self._retries: dict[int, int] = {}
        self._retry_pending: set[int] = set()
        self._job_status: dict[str, str] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("voices.layout", self.on_layout)
        self.handle("voices.dubs.get", self.on_dubs_get)
        self.handle("voices.dispatch", self.on_dispatch, concurrent=True)
        self.handle("downloads.fetch.finished", self.on_fetch_finished)
        self.handle("series.downloaded.changed", self.on_downloaded)
        self.handle("downloads.item.renamed", self.on_downloaded)
        self.handle("library.relocation.finished", self.on_relocated)
        self.handle("conform.job.finished", self.on_job_finished)
        self.handle("conform.queue.changed", self.on_conform_picture)
        self.handle("conform.daemon.up", self.on_daemon_up)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        if self.repo is None:
            return
        requeued = await self.repo.requeue_interrupted()
        pending = await self.repo.pending()
        if requeued or pending:
            self.log.info("озвучки: к докачке %d (прерванных %d)", len(pending), requeued)
        for row in pending:
            await self._start(row)
        for series_id in await self.repo.series_waiting():
            self._kick(series_id)

    # --- settings ---------------------------------------------------------------------

    async def _setting(self, key: str) -> str | None:
        reply = await self.request("settings.value.get", {"key": key}, timeout=10)
        return reply.get("value")

    async def _priority(self) -> list[str]:
        try:
            value = json.loads(await self._setting(PRIORITY_KEY) or "null")
        except (json.JSONDecodeError, TypeError):
            value = None
        if isinstance(value, list) and value and all(isinstance(item, str) for item in value):
            return value
        return list(DEFAULT_PRIORITY)

    async def _temp_root(self) -> str:
        return (await self._setting(TEMP_ROOT_KEY)) or DEFAULT_TEMP_ROOT

    # --- queries ----------------------------------------------------------------------

    async def on_layout(self, env: Envelope) -> dict:
        payload = env.payload
        return plan_layout(payload["sources"], payload.get("mode") or "manual",
                           payload.get("reference"), await self._priority())

    async def on_dubs_get(self, env: Envelope) -> dict:
        rows = await self.repo.for_series(int(env.payload["series_id"]))
        keep = ("season", "episode", "voice_key", "voice_label", "status", "attempt", "error",
                "temp_path", "job_id", "aligned_path", "sidecar_path", "outcome", "ref_uid", "ref_path")
        return {"items": [{**{field: row[field] for field in keep}, "detail": row["outcome"] or row["error"],
                           "state": public_state(row["status"], self._job_status.get(row["job_id"]))}
                          for row in rows]}

    # --- planning ---------------------------------------------------------------------

    async def on_dispatch(self, env: Envelope) -> None:
        series_id = int(env.payload["series_id"])
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            try:
                await self._dispatch(series_id)
            except (BusRequestError, LookupError, ValueError, OSError, asyncio.TimeoutError) as exc:
                self.log.warning("сериал %d: озвучки не подготовлены: %s", series_id, exc)

    async def _dispatch(self, series_id: int) -> None:
        series = await self.request("catalog.series.get", {"series_id": series_id})
        if not is_video_source(series["source_type"]) or series["source_type"] == "vk_video":
            return
        parsed = await self.request("sources.parse", {"url": series["url"], "with_links": True},
                                    timeout=PARSE_TIMEOUT_S)
        sources = parsed.get("sources") or []
        if not sources:
            return
        layout = plan_layout(sources, "manual", parsed.get("selected_source"), await self._priority())
        dubs = [voice for voice in layout["voices"] if voice["role"] == "dub"]
        reference = layout["reference"]
        if not dubs or reference is None:
            await self._forget_all(series_id)
            return
        episodes = await self._reference_episodes(series_id, reference)
        self.log.info("сериал %d: озвучки — референс %s, дополнительных %d, серий плана на сайте %d",
                      series_id, reference["studio"], len(dubs), len(episodes))
        root = await self._temp_root()
        planned: set[tuple[int, int, str]] = set()
        for season, episode, site_episode in episodes:
            for voice in dubs:
                chain = [{"player": source["player"], "studio": source["studio"],
                          "url": source["links"][str(site_episode)]}
                         for source in download_chain(voice, site_episode)
                         if str(site_episode) in source.get("links", {})]
                planned.add((season, episode, voice["key"]))
                await self.repo.upsert(series_id, season, episode, site_episode,
                                       voice["key"], voice["label"], chain)
        for gone in await self.repo.drop_unplanned(series_id, planned):
            self.log.info("сериал %d: озвучка %s серии %d вышла из плана", series_id,
                          gone["voice_label"], gone["episode"])
        await self._recheck_ready(series_id)
        for row in await self.repo.pending(series_id):
            await self._start(row, root)
        for row in await self.repo.for_series(series_id):
            if row["status"] == "failed":
                self.log.warning("сериал %d, s%02de%02d: озвучка %s недоступна: %s", series_id,
                                 row["season"], row["episode"], row["voice_label"], row["error"])
                self._changed(row, "failed", row["error"])
        self._kick(series_id)

    async def _reference_episodes(self, series_id: int, reference: dict) -> list[tuple[int, int, int]]:
        """(season, episode, site episode) of every planned reference episode that the site lists."""
        by_link = {link: int(number) for number, link in (reference.get("links") or {}).items()}
        reply = await self.request("downloads.episodes.get", {"series_id": series_id}, timeout=30)
        found, lost = [], []
        for item in reply["items"]:
            site_episode = by_link.get(item["source_url"])
            if site_episode is None:
                lost.append(item["episode_start"])
            else:
                found.append((item["season"], item["episode_start"], site_episode))
        if lost:
            self.log.warning("сериал %d: серии плана не найдены в каталоге референса, озвучки не "
                             "скачиваются: %s", series_id, ", ".join(map(str, lost)))
        return found

    async def _forget_all(self, series_id: int) -> None:
        await self.repo.drop_unplanned(series_id, set())

    async def _recheck_ready(self, series_id: int) -> None:
        """A ready dub whose file vanished from the temporary directory is downloaded again."""
        for row in await self.repo.for_series(series_id):
            if row["status"] == "ready" and not await asyncio.to_thread(os.path.exists, row["temp_path"]):
                await self.repo.mark_pending(row["id"], "файл озвучки пропал")

    # --- downloads --------------------------------------------------------------------

    async def _start(self, row: dict, root: str | None = None) -> None:
        chain = row["chain"]
        if row["attempt"] >= len(chain):
            await self._fail(row, row["error"] or "нет источника")
            return
        path = temp_path(root or await self._temp_root(), row["series_id"], row["season"],
                         row["episode"], row["voice_key"])
        await self.repo.mark_downloading(row["id"], path)
        self.send_command("downloads.fetch", {
            "key": f"dub:{row['id']}", "url": chain[row["attempt"]]["url"], "path": path,
            "max_height": DUB_MAX_HEIGHT, "series_id": row["series_id"]})

    async def on_fetch_finished(self, env: Envelope) -> None:
        payload = env.payload
        key = str(payload.get("key") or "")
        if not key.startswith("dub:"):
            return
        row = await self.repo.get(int(key[4:]))
        if row is None:
            return
        if payload.get("ok"):
            await self.repo.mark_ready(row["id"])
            self.log.info("сериал %d, s%02de%02d: озвучка %s скачана (%s)", row["series_id"],
                          row["season"], row["episode"], row["voice_label"],
                          row["chain"][row["attempt"]]["player"])
            self._changed(row, "ready")
            self._kick(row["series_id"])
            return
        error = str(payload.get("error") or "загрузка не удалась")
        source = row["chain"][row["attempt"]]
        self.log.info("сериал %d, s%02de%02d: озвучка %s не скачана из %s: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], source["player"], error)
        if row["attempt"] + 1 < len(row["chain"]):
            await self.repo.advance(row["id"], error)
            await self._start(await self.repo.get(row["id"]))
        else:
            await self._fail(row, error)

    async def _fail(self, row: dict, error: str) -> None:
        await self.repo.mark_failed(row["id"], error)
        self.log.warning("сериал %d, s%02de%02d: озвучка %s не скачана ни из одного источника: %s",
                         row["series_id"], row["season"], row["episode"], row["voice_label"], error)
        self._changed(row, "failed", error)

    def _changed(self, row: dict, status: str, detail: str | None = None, job_id: str | None = None) -> None:
        job_status = self._job_status.get(job_id or row.get("job_id"))
        self.publish_event("voices.dub.changed", {
            "series_id": row["series_id"], "season": row["season"], "episode": row["episode"],
            "voice_key": row["voice_key"], "voice_label": row["voice_label"], "status": status,
            "state": public_state(status, job_status), "detail": detail})

    async def on_conform_picture(self, env: Envelope) -> None:
        """A job of the service went from waiting to running (or vanished): its voice-overs follow."""
        states = {task["id"]: task["status"] for task in env.payload.get("tasks") or []}
        changed = {job for job in states.keys() | self._job_status.keys()
                   if states.get(job) != self._job_status.get(job)}
        self._job_status = states
        if self.repo is None:
            return
        for job_id in changed:
            for row in await self.repo.by_job(job_id):
                if row["status"] == "aligning":
                    self._changed(row, "aligning")

    # --- alignment --------------------------------------------------------------------

    async def on_downloaded(self, env: Envelope) -> None:
        self._kick(int(env.payload["series_id"]))

    async def on_relocated(self, env: Envelope) -> None:
        if env.payload.get("success"):
            self._kick(int(env.payload["series_id"]))

    async def on_daemon_up(self, env: Envelope) -> None:
        for job_id in await self.repo.aligning_jobs():
            await self._reconcile(job_id)
        for series_id in await self.repo.series_waiting():
            self._kick(series_id)

    def _kick(self, series_id: int) -> None:
        """Look at the series once more; kicks that arrive while it is scheduled merge into one."""
        if series_id in self._kicked:
            return
        self._kicked.add(series_id)
        task = asyncio.create_task(self._advance_locked(series_id), name=f"voices:advance:{series_id}")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def _advance_locked(self, series_id: int) -> None:
        async with self._locks.setdefault(series_id, asyncio.Lock()):
            self._kicked.discard(series_id)
            try:
                await self._advance(series_id)
            except (BusRequestError, asyncio.TimeoutError, OSError) as exc:
                self.log.warning("сериал %d: выравнивание озвучек отложено: %s", series_id, exc)
            except Exception:  # noqa: BLE001 - one series must not stop the others
                self.log.exception("сериал %d: сбой оркестрации озвучек", series_id)

    async def _advance(self, series_id: int) -> None:
        rows = await self.repo.for_series(series_id)
        if not rows:
            return
        series = await self.request("catalog.series.get", {"series_id": series_id})
        in_plan, references = await self._reference_state(series)
        root = await self._temp_root()
        episodes = sorted({(row["season"], row["episode"]) for row in rows})
        restart = False
        for season, episode in episodes:
            group = await self.repo.for_episode(series_id, season, episode)
            current = references.get((season, episode))
            if current is None:
                if (season, episode) not in in_plan:
                    await self._drop_orphans(series_id, season, episode, group, root)
                continue
            reference, uid = current
            if await self._follow(series_id, season, episode, group, reference, uid):
                restart = True
                continue
            for row in group:
                if row["status"] == "aligned":
                    await self._place(row, reference)
            group = await self.repo.for_episode(series_id, season, episode)
            ready = [row for row in group if row["status"] == "ready"]
            if ready and not any(row["status"] in WORKING for row in group):
                await self._submit(series, season, episode, reference, uid, ready, root)
            await self._tidy(series_id, season, episode, root)
        if restart:
            self.send_command("voices.dispatch", {"series_id": series_id})
        await self._sweep_scratch(series_id, {os.path.dirname(path) for path, _ in references.values()})
        if all(row["status"] in FINAL for row in await self.repo.for_series(series_id)):
            await asyncio.to_thread(self._remove_if_empty, os.path.join(root, str(series_id)))

    async def _sweep_scratch(self, series_id: int, folders: set[str]) -> None:
        """The service's scratch directory is made next to the reference when a job starts: while
        one runs it must stay, and the series lock keeps a new job from starting meanwhile."""
        if any(row["status"] == "aligning" for row in await self.repo.for_series(series_id)):
            return
        for folder in folders:
            await asyncio.to_thread(self._remove_if_empty, os.path.join(folder, SCRATCH_DIR))

    @staticmethod
    def _remove_if_empty(path: str) -> None:
        try:
            os.rmdir(path)
        except OSError:
            pass

    async def _reference_state(self, series: dict) -> tuple[set, dict[tuple[int, int], tuple[str, str]]]:
        """The planned episodes, and (file, media item) of the reference that is on disk, by episode.

        When a slot holds a superseded file and its winner, the winner is the reference."""
        reply = await self.request("downloads.episodes.get", {"series_id": series["id"]}, timeout=30)
        in_plan, found = set(), {}
        for item in reply["items"]:
            slot = (item["season"], item["episode_start"])
            in_plan.add(slot)
            if not item.get("final_filename"):
                continue
            path = os.path.join(series["save_path"], item["final_filename"])
            if not await asyncio.to_thread(os.path.isfile, path):
                continue
            if slot not in found or not item.get("superseded_by"):
                found[slot] = (path, item["unique_id"])
        return in_plan, found

    async def _follow(self, series_id: int, season: int, episode: int, group: list[dict],
                      reference: str, uid: str) -> bool:
        """Keep the tracks of an episode in step with its reference; True when they went stale."""
        stale = [row for row in group if row["ref_uid"] and row["ref_uid"] != uid]
        if stale:
            for row in group:
                if row["ref_uid"] and row["ref_uid"] != uid:
                    await self._discard_files(row)
                    await self.repo.reset(row["id"], "референс серии заменён")
                    self._changed(row, "stale", "референс серии заменён")
            self.log.info("сериал %d, s%02de%02d: референс заменён, дорожки устарели и удалены: %d",
                          series_id, season, episode, len(stale))
            return True
        for row in group:
            if row["status"] == "placed":
                await self._follow_name(row, reference)
        return False

    async def _follow_name(self, row: dict, reference: str) -> None:
        old = row["sidecar_path"]
        if not old:
            return
        expected = sidecar_path(reference, row["voice_label"], os.path.splitext(old)[1] or ".flac")
        present = await asyncio.to_thread(os.path.isfile, old)
        if expected == old and present:
            if row["ref_path"] != reference:
                await self.repo.set_sidecar(row["id"], old, reference)
            return
        if present:
            await asyncio.to_thread(move, old, expected)
        elif expected == old or not await asyncio.to_thread(os.path.isfile, expected):
            await self.repo.mark_rejected(row["id"], "дорожка удалена")
            self.log.info("сериал %d, s%02de%02d: дорожка %s удалена пользователем, заново не создаётся",
                          row["series_id"], row["season"], row["episode"], row["voice_label"])
            self._changed(row, "rejected", "дорожка удалена")
            return
        await self.repo.set_sidecar(row["id"], expected, reference)
        self.log.info("сериал %d, s%02de%02d: дорожка %s следует за видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], expected)
        self._changed(row, "moved")

    async def _drop_orphans(self, series_id: int, season: int, episode: int, group: list[dict],
                            root: str) -> None:
        """An episode left the plan: with its video gone, its set of tracks goes too."""
        if any(row["status"] == "downloading" for row in group):
            return
        for row in group:
            if row["ref_path"] and await asyncio.to_thread(os.path.isfile, row["ref_path"]):
                return
        for row in group:
            await self._discard_files(row)
            await self.repo.delete(row["id"])
            self._changed(row, "removed")
        await asyncio.to_thread(shutil.rmtree, episode_dir(root, series_id, season, episode), True)
        self.log.info("сериал %d, s%02de%02d: серии нет в плане и видео нет — дорожки удалены",
                      series_id, season, episode)

    @staticmethod
    async def _discard_files(row: dict) -> None:
        for path in (row["sidecar_path"], row["aligned_path"], row["temp_path"]):
            if path:
                await asyncio.to_thread(VoicesModule._remove_file, path)

    @staticmethod
    def _remove_file(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    async def _submit(self, series: dict, season: int, episode: int, reference: str, uid: str,
                      ready: list[dict], root: str) -> None:
        folder = episode_dir(root, series["id"], season, episode)
        label = f"{series['name']} s{season:02d}e{episode:02d}"
        try:
            reply = await self.request("conform.submit", {
                "ref": reference, "dubs": [row["temp_path"] for row in ready],
                "out_dir": os.path.join(folder, "_aligned"), "cache_dir": os.path.join(folder, "_cache"),
                "label": label}, timeout=SUBMIT_TIMEOUT_S)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.warning("%s: постановка не удалась: %s", label, exc)
            self._retry_later(series["id"])
            return
        if reply.get("unavailable"):
            self.log.info("%s: озвучки ждут сервис выравнивания: %s", label, reply["unavailable"])
            if reply.get("retry"):
                self._retry_later(series["id"])
            return
        self._retries.pop(series["id"], None)
        if reply.get("rejected"):
            for row in ready:
                await self.repo.mark_rejected(row["id"], reply["rejected"])
                self._changed(row, "rejected", reply["rejected"])
            self.log.warning("%s: задача выравнивания отклонена: %s", label, reply["rejected"])
            return
        await self.repo.mark_aligning([row["id"] for row in ready], reply["job_id"], uid, reference)
        for row in ready:
            self._changed(row, "aligning", job_id=reply["job_id"])

    def _retry_later(self, series_id: int) -> None:
        """A transient failure with the service up: ask again after a pause, a few times only."""
        if series_id in self._retry_pending:
            return
        attempt = self._retries.get(series_id, 0) + 1
        if attempt > SUBMIT_RETRIES:
            self._retries.pop(series_id, None)
            self.log.warning("сериал %d: сервис выравнивания не принял серии за %d попыток, "
                             "дальше — по следующему событию", series_id, SUBMIT_RETRIES)
            return
        self._retries[series_id] = attempt
        self._retry_pending.add(series_id)

        async def later() -> None:
            await asyncio.sleep(SUBMIT_RETRY_S)
            self._retry_pending.discard(series_id)
            self._kick(series_id)

        task = asyncio.create_task(later(), name=f"voices:retry:{series_id}")
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    async def on_job_finished(self, env: Envelope) -> None:
        await self._apply_job(env.payload)

    async def _reconcile(self, job_id: str) -> None:
        """A job sent before a restart: learn how it ended, or send its voice-overs again."""
        try:
            view = await self.request("conform.job.get", {"job_id": job_id}, timeout=30)
        except (BusRequestError, asyncio.TimeoutError) as exc:
            self.log.info("задача выравнивания %s: итог пока неизвестен (%s)", job_id, exc)
            return
        if view["status"] == "gone":
            rows = [row for row in await self.repo.by_job(job_id) if row["status"] == "aligning"]
            await self.repo.release_to_ready([row["id"] for row in rows])
            for series_id in {row["series_id"] for row in rows}:
                self._kick(series_id)
        elif view["status"] in ("done", "failed", "cancelled"):
            await self._apply_job(view)

    async def _apply_job(self, view: dict) -> None:
        rows = [row for row in await self.repo.by_job(view["job_id"]) if row["status"] == "aligning"]
        if not rows:
            return
        results = {stem(result["dub"]): result for result in view.get("results") or []}
        for row in rows:
            if view["status"] != "done":
                error = view.get("error") or f"задача выравнивания: {view['status']}"
                await self.repo.mark_failed(row["id"], error)
                self.log.warning("сериал %d, s%02de%02d: озвучка %s не выровнена: %s", row["series_id"],
                                 row["season"], row["episode"], row["voice_label"], error)
                self._changed(row, "failed", error)
                continue
            result = results.get(stem(row["temp_path"]))
            verdict = await self._verdict(result)
            if verdict is None:
                await self.repo.mark_aligned(row["id"], result["out_path"])
                self._changed(row, "aligned")
            else:
                await self.repo.mark_rejected(row["id"], verdict)
                self.log.warning("сериал %d, s%02de%02d: озвучка %s отклонена: %s", row["series_id"],
                                 row["season"], row["episode"], row["voice_label"], verdict)
                self._changed(row, "rejected", verdict)
        for series_id in {row["series_id"] for row in rows}:
            self._kick(series_id)

    @staticmethod
    async def _verdict(result: dict | None) -> str | None:
        """Why a result may not be placed next to the video; None when it may."""
        if result is None:
            return "в задаче нет результата по этой озвучке"
        if not result.get("ok"):
            return result.get("error") or "выравнивание не удалось"
        if result.get("critical"):
            return "критично: " + "; ".join(result["critical"])
        if result.get("suspect"):
            return "подозрительно: " + "; ".join(result.get("warnings") or ["остаток вне допуска"])
        if not result.get("out_path") or not await asyncio.to_thread(os.path.isfile, result["out_path"]):
            return "выходной файл не найден"
        return None

    async def _place(self, row: dict, reference: str) -> None:
        source = row["aligned_path"]
        if not source or not await asyncio.to_thread(os.path.isfile, source):
            await self.repo.mark_failed(row["id"], "выровненный файл пропал")
            self._changed(row, "failed", "выровненный файл пропал")
            return
        destination = sidecar_path(reference, row["voice_label"], os.path.splitext(source)[1] or ".flac")
        await asyncio.to_thread(place, source, destination)
        await self.repo.mark_placed(row["id"], destination, reference)
        self.log.info("сериал %d, s%02de%02d: дорожка %s рядом с видео: %s", row["series_id"],
                      row["season"], row["episode"], row["voice_label"], destination)
        self._changed(row, "placed")

    async def _tidy(self, series_id: int, season: int, episode: int, root: str) -> None:
        """The episode is settled: its temporary files are of no use any more."""
        group = await self.repo.for_episode(series_id, season, episode)
        if not group or any(row["status"] not in FINAL for row in group):
            return
        folder = episode_dir(root, series_id, season, episode)
        if not (any(row["temp_path"] or row["aligned_path"] for row in group)
                or await asyncio.to_thread(os.path.isdir, folder)):
            return
        await asyncio.to_thread(shutil.rmtree, folder, True)
        await self.repo.forget_temp(series_id, season, episode)
        self.publish_event("voices.episode.settled",
                           {"series_id": series_id, "season": season, "episode": episode})

    # --- cascade ----------------------------------------------------------------------

    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self.send_command("downloads.fetch.cancel", {"series_id": series_id})
        await self.repo.delete_for_series(series_id)
        root = await self._temp_root()
        folder = os.path.join(root, str(series_id))
        await asyncio.to_thread(shutil.rmtree, folder, True)
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})
