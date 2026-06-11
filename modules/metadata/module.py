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

import httpx

from core import BaseModule
from core.envelope import Envelope

BASE_URL = "https://api.themoviedb.org/3"


class TmdbError(RuntimeError):
    pass


class MetadataModule(BaseModule):
    name = "metadata"

    def register(self) -> None:
        self.handle("metadata.search", self.on_search)
        self.handle("metadata.details", self.on_details)

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
        data = await self._get("/search/tv", {
            "query": query, "include_adult": "false",
            "language": "ru-RU", "page": "1"})
        results = [{
            "id": r.get("id"),
            "name": r.get("name"),
            "original_name": r.get("original_name"),
            "year": (r.get("first_air_date") or "")[:4],
            "poster_path": r.get("poster_path"),
            "overview": r.get("overview") or "",
        } for r in data.get("results", [])]
        return {"results": results}

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
