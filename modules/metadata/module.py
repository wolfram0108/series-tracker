"""Модуль metadata — интеграция с TMDB; владелец series_tmdb_mappings.

Отличия от старого utils/tmdb_client.py (по разбору):
- ошибки TMDB не глотаются молча (старый возвращал []/None и проблема
  тонула) — проброс в reply, потребитель видит причину;
- токен — не прямое чтение чужой таблицы settings, а query
  `settings.value.get` через шину (Р-7);
- httpx async с таймаутом вместо requests в потоке.

Форматы ответов search/details повторяют сегодняшний контракт фронта —
их судьба решается на этапе 5 (ревизия), здесь они «как сейчас».
"""
from __future__ import annotations

import asyncio

import httpx

from core import BaseModule
from core.db import Database
from core.envelope import Envelope

from .repository import MetadataRepository

BASE_URL = "https://api.themoviedb.org/3"


class TmdbError(RuntimeError):
    pass


class MetadataModule(BaseModule):
    name = "metadata"

    def __init__(self, bus, db: Database) -> None:
        self.repo = MetadataRepository(db)
        super().__init__(bus)

    def register(self) -> None:
        self.handle("metadata.search", self.on_search)
        self.handle("metadata.details", self.on_details)
        self.handle("metadata.map.get", self.on_map_get)
        self.handle("metadata.map.list", self.on_map_list)
        self.handle("metadata.map.set", self.on_map_set)
        self.handle("series.deleted", self.on_series_deleted)

    async def on_start(self) -> None:
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=10.0)

    async def on_stop(self) -> None:
        await self._http.aclose()

    async def _headers(self) -> dict:
        reply = await self.request("settings.value.get",
                                   {"key": "tmdb_token"}, timeout=5)
        token = reply["value"]
        if not token:
            raise TmdbError("токен TMDB не настроен")
        return {"accept": "application/json",
                "Authorization": f"Bearer {token}"}

    async def _get(self, path: str, params: dict) -> dict:
        resp = await self._http.get(path, params=params,
                                    headers=await self._headers())
        resp.raise_for_status()
        return resp.json()

    async def on_search(self, env: Envelope) -> dict:
        query = env.payload["query"]
        params = {"query": query, "include_adult": "false", "page": "1"}
        # Два запроса параллельно: ru-RU (как раньше) и en-US — чтобы у
        # тайтлов без русского перевода (азиатские релизы: TMDB отдаёт в
        # name оригинал-иероглифы) было читаемое английское имя. Английский
        # — опциональное обогащение: его сбой не валит поиск.
        data, en_data = await asyncio.gather(
            self._get("/search/tv", {**params, "language": "ru-RU"}),
            self._get("/search/tv", {**params, "language": "en-US"}),
            return_exceptions=True,
        )
        if isinstance(data, BaseException):
            raise data
        en_names = ({} if isinstance(en_data, BaseException)
                    else {r.get("id"): r.get("name")
                          for r in en_data.get("results", [])})
        results = [{
            "id": r.get("id"),
            "name": r.get("name"),
            "name_en": en_names.get(r.get("id")),
            "original_name": r.get("original_name"),
            "year": (r.get("first_air_date") or "")[:4],
            "poster_path": r.get("poster_path"),
            "overview": r.get("overview") or "",
        } for r in data.get("results", [])]
        return {"results": results}

    # --- series_tmdb_mappings (наша таблица, Р-19) ------------------------------

    async def on_map_get(self, env: Envelope) -> dict:
        mapping = await self.repo.get_mapping(env.payload["series_id"])
        return {"mapping": mapping}

    async def on_map_list(self, env: Envelope) -> dict:
        return {"mappings": await self.repo.all_mappings()}

    async def on_map_set(self, env: Envelope) -> dict:
        await self.repo.upsert_mapping(env.payload["series_id"],
                                       env.payload["tmdb_data"])
        return {"ok": True}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит своё по событию."""
        await self.repo.delete_for_series(env.payload["series_id"])

    async def on_details(self, env: Envelope) -> dict:
        tmdb_id = int(env.payload["tmdb_id"])
        data = await self._get(f"/tv/{tmdb_id}", {"language": "ru-RU"})
        return {
            "name": data.get("name"),
            "poster_path": data.get("poster_path"),
            "status": data.get("status"),
            "seasons": [{
                "season_number": s.get("season_number"),
                "episode_count": s.get("episode_count"),
                "air_date": s.get("air_date"),
                "name": s.get("name"),
            } for s in data.get("seasons", [])],
        }
