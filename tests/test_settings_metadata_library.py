"""Тесты модулей settings, metadata, library (этап 2)."""
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
