"""Тесты модулей settings, metadata, library (этап 2)."""
import asyncio
import subprocess
import sys

import httpx
import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.db import Database
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from modules.settings import SettingsModule


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    return str(path)


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
async def test_settings_set_get_and_event(system):
    bus, _, modules = system
    probe = _probe(modules)
    sub = bus.subscribe("settings.changed")

    await probe.request("settings.value.set",
                        {"key": "tmdb_token", "value": "т0кен"}, timeout=5)
    reply = await probe.request("settings.value.get",
                                {"key": "tmdb_token"}, timeout=5)
    assert reply["value"] == "т0кен"

    env = sub.queue.get_nowait()
    assert env.topic == "settings.changed"
    assert env.payload == {"key": "tmdb_token", "value": "т0кен"}


@pytest.mark.asyncio
async def test_settings_get_missing_returns_none(system):
    _, _, modules = system
    reply = await _probe(modules).request("settings.value.get",
                                          {"key": "нет такого"}, timeout=5)
    assert reply["value"] is None


@pytest.mark.asyncio
async def test_settings_saved_paths_crud(system):
    # сохранённые пути: add (с игнором дублей) / list / remove
    probe = _probe(system[2])
    await probe.request("settings.paths.add", {"path": "/nas/a"}, timeout=5)
    await probe.request("settings.paths.add", {"path": "/nas/b"}, timeout=5)
    await probe.request("settings.paths.add", {"path": "/nas/a"}, timeout=5)
    lst = await probe.request("settings.paths.list", {}, timeout=5)
    assert [p["path"] for p in lst["paths"]] == ["/nas/a", "/nas/b"]
    pid = lst["paths"][0]["id"]
    reply = await probe.request("settings.paths.remove", {"id": pid}, timeout=5)
    assert [p["path"] for p in reply["paths"]] == ["/nas/b"]


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


@pytest.mark.asyncio
async def test_metadata_without_token_fails_loudly(system):
    _, _, modules = system
    with pytest.raises(BusRequestError, match="токен TMDB"):
        await _probe(modules).request("metadata.search",
                                      {"query": "x"}, timeout=5)


@pytest.mark.asyncio
async def test_metadata_mapping_persists_year(system):
    # год (миграция 0003) доезжает через map.set → map.get: нужен для
    # имени каталога «Имя (год) [tmdbid-XXXX]» в инфо-режиме.
    bus, db, modules = system
    await db.execute(
        "INSERT INTO series (id, url, name, name_en, site, save_path, "
        "auto_scan_enabled, source_type, vk_search_mode) "
        "VALUES (1, 'u', 'Очень странные дела', 'Stranger Things', "
        "'kinozal', '/nas', 1, 'torrent', 'search')")
    probe = _probe(modules)
    await probe.request("metadata.map.set", {
        "series_id": 1,
        "tmdb_data": {"tmdb_id": 66732, "tmdb_season_number": 1,
                      "total_episodes": 8, "series_name": "Очень странные дела",
                      "year": "2016"}}, timeout=5)
    reply = await probe.request("metadata.map.get", {"series_id": 1}, timeout=5)
    assert reply["mapping"]["year"] == "2016"


# --- library --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_library_lists_only_directories_sorted(system, tmp_path):
    _, _, modules = system
    (tmp_path / "b_dir").mkdir()
    (tmp_path / "A_dir").mkdir()
    (tmp_path / "file.mkv").write_text("x")

    reply = await _probe(modules).request(
        "library.directories.list", {"path": str(tmp_path)}, timeout=5)
    assert [i["name"] for i in reply["items"]] == ["A_dir", "b_dir"]
    assert all(i["type"] == "directory" for i in reply["items"])


@pytest.mark.asyncio
async def test_library_allowed_roots_enforced(db_path, tmp_path):
    bus = Bus()
    modules = [LibraryModule(bus, Database(db_path),
                              allowed_roots=[str(tmp_path)]), Probe(bus)]
    runner = Runner(bus, modules)
    await runner.start()
    try:
        probe = modules[-1]
        ok = await probe.request("library.directories.list",
                                 {"path": str(tmp_path)}, timeout=5)
        assert ok["path"] == str(tmp_path)
        with pytest.raises(BusRequestError, match="запрещён"):
            await probe.request("library.directories.list",
                                {"path": "/etc"}, timeout=5)
        # обход через .. нормализуется ДО проверки
        with pytest.raises(BusRequestError, match="запрещён"):
            await probe.request("library.directories.list",
                                {"path": f"{tmp_path}/../../etc"}, timeout=5)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_library_missing_path(system):
    _, _, modules = system
    with pytest.raises(BusRequestError, match="не каталог"):
        await _probe(modules).request("library.directories.list",
                                      {"path": "/нет/такого"}, timeout=5)


# --- агрегатор многосезонника (TMDB) --------------------------------------------

class _Neighbour(BaseModule):
    """Соседи агрегатора: catalog.series.get + сезоны из нейминга."""
    name = "neigh"

    def __init__(self, bus):
        self.series = {"id": 1, "source_type": "torrent", "season": ""}
        self.seasons = {"seasons": []}
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self._get)
        self.handle("torrents.seasons", self._seasons)
        self.handle("scan.seasons", self._seasons)

    async def _get(self, env):
        return self.series

    async def _seasons(self, env):
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
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode) VALUES "
            "(1, 'u', 'Т', 'T', 'kinozal', '/m', 'waiting', 'torrent', 0, "
            "'search')")
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
async def test_seasons_recompute_sums_excluding_season0(agg_system, monkeypatch):
    bus, neigh, mods = agg_system
    probe = mods[-1]
    await probe.request("settings.value.set",
                        {"key": "tmdb_token", "value": "x"}, timeout=5)
    await probe.request("metadata.map.set", {"series_id": 1, "tmdb_data": {
        "tmdb_id": 42, "tmdb_season_number": 1}}, timeout=5)
    neigh.seasons = {"seasons": [1, 2, 0]}  # сезон 0 (спешелы) должен выпасть
    calls: list = []
    monkeypatch.setattr(httpx.AsyncClient, "get", _tmdb_details_stub(calls))
    sub = bus.subscribe("series.updated")

    reply = await probe.request("metadata.seasons.recompute",
                                {"series_id": 1}, timeout=10)
    assert reply == {"total": 50, "seasons": [1, 2]}  # 26+24, без сезона 0
    mapping = (await probe.request("metadata.map.get",
                                   {"series_id": 1}, timeout=5))["mapping"]
    assert mapping["total_episodes"] == 50
    # реактивность: событие для карточки с обновлённым tmdb_info
    env = await asyncio.wait_for(sub.queue.get(), 2)
    assert env.payload["series_id"] == 1
    assert env.payload["tmdb_info"]["total_episodes"] == 50


@pytest.mark.asyncio
async def test_seasons_recompute_cache_no_network_on_repeat(agg_system, monkeypatch):
    """Повторный пересчёт без новых сезонов — без сети; новый сезон — снова сеть."""
    bus, neigh, mods = agg_system
    probe = mods[-1]
    await probe.request("settings.value.set",
                        {"key": "tmdb_token", "value": "x"}, timeout=5)
    await probe.request("metadata.map.set", {"series_id": 1, "tmdb_data": {
        "tmdb_id": 42, "tmdb_season_number": 1}}, timeout=5)
    # TMDB сначала знает сезоны 0,1,2 (без 3); набор изменяемый
    seasons_data = [{"season_number": 0, "episode_count": 10},
                    {"season_number": 1, "episode_count": 26},
                    {"season_number": 2, "episode_count": 24}]
    calls: list = []

    async def fake_get(self, path, **kwargs):
        calls.append(path)
        return httpx.Response(200, json={
            "name": "T", "poster_path": None, "status": "x",
            "seasons": list(seasons_data)},
            request=httpx.Request("GET", "http://t"))
    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    neigh.seasons = {"seasons": [1, 2]}
    await probe.request("metadata.seasons.recompute", {"series_id": 1}, timeout=10)
    assert len(calls) == 1                       # первый раз — сеть
    await probe.request("metadata.seasons.recompute", {"series_id": 1}, timeout=10)
    assert len(calls) == 1                       # повтор — из кэша, без сети
    # сезон 3 появился и в нейминге, и в TMDB → 3 нет в кэше {0,1,2} → сеть
    seasons_data.append({"season_number": 3, "episode_count": 20})
    neigh.seasons = {"seasons": [1, 2, 3]}
    r = await probe.request("metadata.seasons.recompute", {"series_id": 1}, timeout=10)
    assert len(calls) == 2
    assert r["total"] == 70                      # 26+24+20


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


@pytest.mark.asyncio
async def test_seasons_recompute_skips_single_and_no_tmdb(agg_system):
    bus, neigh, mods = agg_system
    probe = mods[-1]
    # нет TMDB-маппинга → skip
    assert (await probe.request("metadata.seasons.recompute",
                                {"series_id": 1}, timeout=5))["skipped"] == "no_tmdb"
    # одиночный режим (season задан) → skip, total не трогаем
    await probe.request("metadata.map.set", {"series_id": 1, "tmdb_data": {
        "tmdb_id": 42, "tmdb_season_number": 1, "total_episodes": 26}}, timeout=5)
    neigh.series = {"id": 1, "source_type": "torrent", "season": "s01"}
    assert (await probe.request("metadata.seasons.recompute",
                                {"series_id": 1}, timeout=5))["skipped"] == "single_season"
