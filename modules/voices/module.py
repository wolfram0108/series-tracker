"""Module voices: the voice-overs of a series (mission voice-alignment, stages 4 and 6).

Owns voice_dubs: one row per (series, episode, voice) — the additional voice-overs that are
downloaded next to the reference video and later aligned to it.

Query:
  voices.layout {sources: [{url, player, studio, kind, episodes, links?}], mode?, reference?}
      -> {mode, reference, voices: [{key, label, role, source, fallbacks}], skipped, priority}
      The source priority is the setting `voices.source_priority` (JSON list of player kinds);
      layout.DEFAULT_PRIORITY when unset.
  voices.dubs.get {series_id} -> {items: [{season, episode, voice_key, voice_label, status,
      attempt, error, temp_path}]}
Command:
  voices.dispatch {series_id} — sent by scan after an explicit scan: read the catalog of the site,
      lay the voices out around the selected reference, record a voice_dubs row per planned
      reference episode and voice, and download what is not on disk yet into the temporary
      directory (setting `voices.temp_root`, default /nas/torrents/series-tracker).
Events in:  downloads.fetch.finished (our keys `dub:<id>`), series.deleted.
Events out: voices.dub.changed {series_id, season, episode, voice_key, status}, series.purged.

Downloading is delegated to downloads.fetch. A dub tries its main source first, then the other
sources of the same voice by priority; when none gives the file the row is `failed`, a warning is
logged, and the next dispatch offers it again from the main source. A dub is `ready` once its file
is on disk; rows left `downloading` by a restart are sent again on start.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil

from core import BaseModule, BusRequestError, is_video_source
from core.db import Database
from core.envelope import Envelope

from .layout import DEFAULT_PRIORITY, download_chain, plan_layout
from .repository import VoicesRepository

PRIORITY_KEY = "voices.source_priority"
TEMP_ROOT_KEY = "voices.temp_root"
DEFAULT_TEMP_ROOT = "/nas/torrents/series-tracker"
DUB_MAX_HEIGHT = 720
PARSE_TIMEOUT_S = 180


def _safe(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("._") or "dub"


def temp_path(root: str, series_id: int, season: int, episode: int, voice_key: str) -> str:
    return os.path.join(root, str(series_id), f"s{season:02d}e{episode:02d}", f"{_safe(voice_key)}.mp4")


class VoicesModule(BaseModule):
    name = "voices"

    def __init__(self, bus, db: Database | None = None) -> None:
        self.repo = VoicesRepository(db) if db is not None else None
        self._locks: dict[int, asyncio.Lock] = {}
        super().__init__(bus)

    def register(self) -> None:
        self.handle("voices.layout", self.on_layout)
        self.handle("voices.dubs.get", self.on_dubs_get)
        self.handle("voices.dispatch", self.on_dispatch, concurrent=True)
        self.handle("downloads.fetch.finished", self.on_fetch_finished)
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
                "temp_path")
        return {"items": [{field: row[field] for field in keep} for row in rows]}

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
        self._changed(row, "failed")

    def _changed(self, row: dict, status: str) -> None:
        self.publish_event("voices.dub.changed", {
            "series_id": row["series_id"], "season": row["season"], "episode": row["episode"],
            "voice_key": row["voice_key"], "status": status})

    # --- cascade ----------------------------------------------------------------------

    async def on_series_deleted(self, env: Envelope) -> None:
        series_id = env.payload["series_id"]
        self.send_command("downloads.fetch.cancel", {"series_id": series_id})
        await self.repo.delete_for_series(series_id)
        root = await self._temp_root()
        folder = os.path.join(root, str(series_id))
        await asyncio.to_thread(shutil.rmtree, folder, True)
        self.publish_event("series.purged", {"series_id": series_id, "owner": self.name})
