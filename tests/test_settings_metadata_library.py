"""Тесты модулей settings, metadata, library (этап 2)."""
import asyncio

import httpx
import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.db import Database
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from modules.settings import SettingsModule
from series_factory import insert_series, series_card



class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    modules = [SettingsModule(bus, db), MetadataModule(bus, db),
               LibraryModule(bus, db), Probe(bus)]
    runner = Runner(bus, modules)
    await runner.start()
    yield bus, db, modules
    await runner.stop()


def _probe(modules) -> Probe:
    return modules[-1]


# --- settings -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settings_saved_paths_crud(system):
    # сохранённые пути: add (с игнором дублей) / list / remove
    probe = _probe(system[2])
    await probe.request("settings.paths.add", {"path": "/nas/a"}, timeout=5)
    await probe.request("settings.paths.add", {"path": "/nas/b"}, timeout=5)
    await probe.request("settings.paths.add", {"path": "/nas/a"}, timeout=5)
    lst = await probe.request("settings.paths.list", {}, timeout=5)
    assert [p.path for p in lst.paths] == ["/nas/a", "/nas/b"]
    pid = lst.paths[0].id
    reply = await probe.request("settings.paths.remove", {"id": pid}, timeout=5)
    assert [p.path for p in reply.paths] == ["/nas/b"]


# --- metadata -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_metadata_search_formats_results(system, monkeypatch):
    _, _, modules = system
    probe = _probe(modules)
    await probe.request("settings.value.set",
                        {"key": "tmdb_token", "value": "x"}, timeout=5)

    # поиск идёт двумя запросами: ru-RU (основной) и en-US — английское имя
    # доезжает в name_en (для тайтлов без русского перевода).
    async def fake_get(self, path, **kwargs):
        assert path == "/search/tv"
        name = ("Во все тяжкие" if kwargs["params"]["language"] == "ru-RU"
                else "Breaking Bad")
        return httpx.Response(200, json={"results": [{
            "id": 4638, "name": name,
            "original_name": "Breaking Bad",
            "first_air_date": "2008-01-20",
            "poster_path": "/p.jpg", "overview": "химия"}]},
            request=httpx.Request("GET", "http://t"))

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    reply = await probe.request("metadata.search",
                                {"query": "breaking"}, timeout=5)
    assert reply["results"] == [{
        "id": 4638, "name": "Во все тяжкие", "name_en": "Breaking Bad",
        "original_name": "Breaking Bad", "year": "2008",
        "poster_path": "/p.jpg", "overview": "химия"}]


# --- library --------------------------------------------------------------------

# --- агрегатор многосезонника (TMDB) --------------------------------------------

class _Neighbour(BaseModule):
    """Соседи агрегатора: catalog.series.get + сезоны из нейминга."""
    name = "neigh"

    def __init__(self, bus):
        self.series = series_card(id=1, season="")
        self.seasons = {"seasons": []}
        self.asked: list[str] = []
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self._get)
        self.handle("planner.seasons", self._scan_seasons)

    async def _get(self, env):
        return self.series

    async def _scan_seasons(self, env):
        self.asked.append("planner.seasons")
        return self.seasons


def _tmdb_details_stub(calls: list):
    """Мок TMDB /tv/{id}: сезоны 0→10, 1→26, 2→24, 3→20. Считает вызовы."""
    async def fake_get(self, path, **kwargs):
        calls.append(path)
        return httpx.Response(200, json={
            "name": "T", "poster_path": None, "status": "x",
            "seasons": [{"season_number": 0, "episode_count": 10},
                        {"season_number": 1, "episode_count": 26},
                        {"season_number": 2, "episode_count": 24},
                        {"season_number": 3, "episode_count": 20}]},
            request=httpx.Request("GET", "http://t"))
    return fake_get


@pytest.fixture
async def agg_system(db_path):
    import sqlite3
    with sqlite3.connect(db_path) as conn:
        insert_series(conn, id=1, name="Т", name_en="T", site="kinozal", save_path="/m", state="waiting",
                      url="u", source_type="torrent")
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    neigh = _Neighbour(bus)
    mods = [SettingsModule(bus, db), MetadataModule(bus, db), neigh, Probe(bus)]
    runner = Runner(bus, mods)
    await runner.start()
    yield bus, neigh, mods
    await runner.stop()


@pytest.mark.asyncio
async def test_map_set_triggers_recompute(agg_system, monkeypatch):
    """Назначение/смена TMDB сразу пересчитывает агрегат — не ждёт скана."""
    bus, neigh, mods = agg_system
    probe = mods[-1]
    await probe.request("settings.value.set",
                        {"key": "tmdb_token", "value": "x"}, timeout=5)
    neigh.seasons = {"seasons": [1, 2]}
    monkeypatch.setattr(httpx.AsyncClient, "get", _tmdb_details_stub([]))
    sub = bus.subscribe("series.updated")
    await probe.request("metadata.map.set", {"series_id": 1, "tmdb_data": {
        "tmdb_id": 42, "tmdb_season_number": 1}}, timeout=5)
    # recompute сработал автоматически от map.set → событие с агрегатом
    env = await asyncio.wait_for(sub.queue.get(), 2)
    assert env.payload["tmdb_info"]["total_episodes"] == 50  # 26+24
