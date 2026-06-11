"""Модуль torrents: единственный собеседник qBittorrent (решение Р-1).

Этап 2 — клиентская часть: queries/commands шины поверх QbtClient.
Жизненный цикл торрент-задач (стадии, reconcile из agent_tasks) —
этап 4.

Добавление (Р-2): кандидаты infohash вычисляются локально ДО вызова
API; после добавления контрольный запрос определяет, каким из
кандидатов qBittorrent ключует торрент (v1 sha1 или укороченный
sha256 для гибридов v2). Ответ «Fails.» различается тем же запросом:
торрент уже существует → возвращаем его hash с пометкой existed.
"""
from __future__ import annotations

import base64

from core import BaseModule
from core.envelope import Envelope

from .infohash import (TorrentParseError, infohash_candidates,
                       infohash_from_magnet)
from .qbt_client import QbtClient


class TorrentsModule(BaseModule):
    name = "torrents"

    def __init__(self, bus, *, qbt_url: str, qbt_username: str,
                 qbt_password: str) -> None:
        self._qbt_config = dict(base_url=qbt_url, username=qbt_username,
                                password=qbt_password)
        self.qbt: QbtClient | None = None
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

    async def on_start(self) -> None:
        self.qbt = QbtClient(**self._qbt_config)
        await self.qbt.login()

    async def on_stop(self) -> None:
        if self.qbt:
            await self.qbt.close()

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
