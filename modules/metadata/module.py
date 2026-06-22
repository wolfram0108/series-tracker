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
import json

import httpx

from core import BaseModule, BusRequestError
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
        self.handle("metadata.seasons.recompute", self.on_seasons_recompute)
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
        # Назначение/смена TMDB — пересчитать многосезонный агрегат СРАЗУ, по
        # уже известным из нейминга сезонам, не дожидаясь следующего скана
        # (иначе число серий многосезонника не появляется после назначения).
        self.send_command("metadata.seasons.recompute",
                          {"series_id": env.payload["series_id"]})
        return {"ok": True}

    async def on_series_deleted(self, env: Envelope) -> None:
        """Каскад Р-19: владелец чистит своё по событию."""
        await self.repo.delete_for_series(env.payload["series_id"])

    async def on_details(self, env: Envelope) -> dict:
        return await self._details(int(env.payload["tmdb_id"]))

    async def _details(self, tmdb_id: int) -> dict:
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

    # --- агрегат многосезонника (число серий по реальным сезонам) ----------------

    async def on_seasons_recompute(self, env: Envelope) -> dict:
        """Пересчёт числа серий многосезонной серии. Реальные сезоны берём из
        нейминга (торрент: Season NN; VK: media_items.season), счётчики серий
        по сезонам — из TMDB (кэш в маппинге; сеть только на новый сезон),
        total = сумма по реальным сезонам (сезон 0/спешелы исключены, реш. #2).
        Публикует series.updated с обновлённым tmdb_info → карточка без F5.
        Событийный (скан/рейм/усыновление/композиция), без таймеров (реш. #3).
        Одиночный режим (поле сезона задано) не трогаем — там total уже верен."""
        series_id = env.payload["series_id"]
        mapping = await self.repo.get_mapping(series_id)
        if not mapping or not mapping.get("tmdb_id"):
            return {"skipped": "no_tmdb"}
        series = await self.request("catalog.series.get",
                                    {"series_id": series_id}, timeout=10)
        if (series.get("season") or "").strip():
            return {"skipped": "single_season"}
        real = await self._real_seasons(series)
        if not real:
            return {"skipped": "no_seasons"}
        counts = await self._season_counts(mapping, real)
        total = sum(counts.get(s, 0) for s in real)
        await self.repo.set_aggregate(series_id, total, counts)
        self.publish_event("series.updated", {
            "series_id": series_id,
            "tmdb_info": await self.repo.get_mapping(series_id)})
        self.log.info("агрегат TMDB серии %d: сезоны %s → %d серий",
                      series_id, sorted(real), total)
        return {"total": total, "seasons": sorted(real)}

    async def _real_seasons(self, series: dict) -> set[int]:
        """Реальные сезоны из нейминга (≠0). Источник — владелец данных:
        торрент → torrents.seasons, VK → scan.seasons (Р-7, чужое — query)."""
        topic = ("scan.seasons" if series.get("source_type") == "vk_video"
                 else "torrents.seasons")
        try:
            reply = await self.request(topic, {"series_id": series["id"]},
                                       timeout=30)
        except BusRequestError as exc:
            self.log.warning("сезоны серии %d недоступны (%s): %s",
                             series["id"], topic, exc)
            return set()
        out: set[int] = set()
        for s in reply.get("seasons", []):
            try:
                n = int(s)
            except (TypeError, ValueError):
                continue
            if n != 0:  # сезон 0 (спешелы) исключаем — с TMDB не совпадают
                out.add(n)
        return out

    async def _season_counts(self, mapping: dict,
                             real: set[int]) -> dict[int, int]:
        """Счётчики серий по сезонам шоу. Кэш в маппинге; сеть к TMDB — только
        когда среди реальных есть сезон не из кэша (новый сезон появился)."""
        try:
            cache = {int(k): v for k, v in json.loads(
                mapping.get("season_episode_counts") or "{}").items()}
        except (ValueError, TypeError, AttributeError):
            cache = {}
        if not real <= set(cache):
            details = await self._details(int(mapping["tmdb_id"]))
            cache = {s["season_number"]: s["episode_count"]
                     for s in details["seasons"]
                     if s.get("season_number") is not None
                     and s.get("episode_count") is not None}
        return cache
