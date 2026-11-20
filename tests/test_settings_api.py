"""The block «settings and references» of stage 5 (R-22): the credentials, the settings and the debug groups, the
constructor of rules, the trackers, the folders, the admin tab of the database."""
import json
import sqlite3

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core import logging as core_logging
from core.db import Database
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.library import LibraryModule
from modules.rules import RulesModule
from modules.settings import SettingsModule
from modules.sources import SourcesModule
from modules.trackerauth import TrackerauthModule
from modules.voices import VoicesModule
from core.reasons import parse
from series_factory import insert_series
from schema_db import migrated


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # до конкурентных подключений
    with conn:
        conn.execute(
            "INSERT INTO parser_profiles (id, name) VALUES (1, 'Занятый')")
        insert_series(conn, id=1, name="Т", name_en="T", url="http://x", source_type="torrent",
                      parser_profile_id=1)
        conn.commit()
    conn.close()
    return str(path)


class Neighbours(BaseModule):
    name = "neighbours"

    def register(self):
        async def search(env):
            return {"results": [{"id": 7, "name": "Тайтл"}]}

        async def details(env):
            return {"name": "Тайтл", "poster_path": None,
                    "status": "Returning Series", "seasons": []}

        self.handle("metadata.search", search)
        self.handle("metadata.details", details)


async def fake_parse_by_service(service, url, mirrors):
    return {"title": {"ru": "Тайтл", "en": "Title"},
            "releases": [{"link": "http://kinozal.tv/dl/1", "magnet": None,
                          "date_marker": "2026-01-01 00:00",
                          "quality": "1080p", "episodes": "1-12"}]}


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path), db_path=db_path)
    neighbours = Neighbours(bus)
    sources = SourcesModule(bus, db,
                            torrent_cache_dir=str(tmp_path / "tc"))
    # доставка страниц подменяется — тестируем on_parse: torrent_id и форму
    sources._parse_by_service = fake_parse_by_service
    modules = [gateway, neighbours, SettingsModule(bus, db),
               TrackerauthModule(bus, db), RulesModule(bus, db),
               CatalogModule(bus, db), sources,
               LibraryModule(bus, db, allowed_roots=None), VoicesModule(bus)]
    runner = Runner(bus, modules)
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client
    await runner.stop()


@pytest.mark.asyncio
async def test_a_secret_is_given_only_when_the_eye_of_its_field_asks_for_it_and_is_not_kept(system):
    """The password of a service is shown on request: its own call, not cached, nothing of it in the credentials; a service with none
    set gives an empty one, a name that is no service is no route."""
    _, _, client = system
    await client.post("/api/auth", json={
        "qbittorrent": {"url": "http://qb:8080", "username": "admin", "password": "pass-qb"},
        "vk": {"token": "vk-token-1"}, "tmdb": {"token": "tmdb-token-1"}})
    for service, secret in (("qbittorrent", "pass-qb"), ("vk", "vk-token-1"), ("tmdb", "tmdb-token-1"), ("kinozal", "")):
        answer = await client.get(f"/api/auth/{service}/secret")
        assert answer.status_code == 200 and answer.json() == {"secret": secret}, service
        assert answer.headers["cache-control"] == "no-store"
    assert (await client.get("/api/auth/nothing/secret")).status_code == 404
    # the credentials still carry no secret
    assert "pass-qb" not in (await client.get("/api/auth")).text


@pytest.mark.asyncio
async def test_debug_groups(system):
    _, _, client = system
    # debug-флаги: структура с бэкенда + включение группы (принцип 6)
    resp = await client.get("/api/settings/debug_flags")
    body = resp.json()
    names = [m["name"] for g in body["logging_modules"].values()
             for m in g]
    assert "scan" in names and "torrents" in names
    # the flags of page dumps had no reader: they are not offered
    assert set(body) == {"logging_modules"}
    await client.post("/api/settings/debug_flags",
                      json={"module": "scan", "enabled": True})
    assert "scan" in core_logging.debug_groups()
    await client.post("/api/settings/debug_flags",
                      json={"module": "scan", "enabled": False})
    assert "scan" not in core_logging.debug_groups()


@pytest.mark.asyncio
async def test_the_flag_that_clears_the_alignment_queue_is_one_of_the_pipeline_flags(system):
    bus, db, client = system
    assert (await client.get("/api/settings/conform_clear_finished")).json() == {"enabled": False}
    await client.post("/api/settings/conform_clear_finished", json={"enabled": True})
    assert (await client.get("/api/settings/conform_clear_finished")).json() == {"enabled": True}
    stored = await db.fetch_one("SELECT value FROM settings WHERE key=?", ("conform.clear_finished",))
    assert stored["value"] == "true"  # the form the bridge reads
    await client.post("/api/settings/conform_clear_finished", json={"enabled": False})
    assert (await client.get("/api/settings/conform_clear_finished")).json() == {"enabled": False}


@pytest.mark.asyncio
async def test_tmdb_wrappers(system):
    _, _, client = system
    resp = await client.post("/api/tmdb/search", json={"query": "Тайтл"})
    assert resp.json() == {"success": True,
                           "results": [{"id": 7, "name": "Тайтл"}]}
    resp = await client.post("/api/tmdb/search", json={})
    assert resp.status_code == 400
    resp = await client.get("/api/tmdb/details/7")
    assert resp.json()["success"] is True
    assert resp.json()["status"] == "Returning Series"


@pytest.mark.asyncio
async def test_trackers_list_and_mirrors(system):
    _, db, client = system
    resp = await client.get("/api/trackers")
    # A database made by the migrations knows the trackers the service reads (migration 0074).
    kinozal = next(each for each in resp.json() if each["canonical_name"] == "kinozal")
    assert {each["canonical_name"] for each in resp.json()} == {"anilibria", "anilibria_tv", "kinozal", "astar", "rutracker"}
    resp = await client.put(f"/api/trackers/{kinozal['id']}", json={
        "mirrors": ["kinozal.tv", "kinozal.me"]})
    assert resp.json()["success"] is True
    resp = await client.get("/api/trackers")
    assert next(each for each in resp.json() if each["id"] == kinozal["id"])["mirrors"] == ["kinozal.tv", "kinozal.me"]
    resp = await client.put(f"/api/trackers/{kinozal['id']}", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_directories(system, tmp_path):
    _, _, client = system
    (tmp_path / "B").mkdir()
    (tmp_path / "A").mkdir()
    resp = await client.get("/api/directories",
                            params={"path": str(tmp_path)})
    body = resp.json()
    assert [i["name"] for i in body["items"]][:2] == ["A", "B"]


@pytest.mark.asyncio
async def test_parser_profiles_crud_flow(system):
    _, _, client = system
    # создание + дубль имени
    resp = await client.post("/api/parser-profiles", json={"name": "Тест"})
    assert resp.status_code == 201
    profile_id = resp.json()["id"]
    resp = await client.post("/api/parser-profiles", json={"name": "Тест"})
    assert resp.status_code == 409

    # правило: добавление, список, обновление, порядок
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Сезон", "action_pattern": "[]",
                                   "conditions": [{"condition_type":
                                                   "contains",
                                                   "pattern": '[{"type":'
                                                   '"text","value":'
                                                   '"сезон"}]'}]})
    rule_id = resp.json()["id"]
    resp = await client.get(f"/api/parser-profiles/{profile_id}/rules")
    rules = resp.json()
    assert rules[0]["name"] == "Сезон"
    assert rules[0]["conditions"][0]["pattern"] == (
        '[{"type":"text","value":"сезон"}]')

    resp = await client.put(f"/api/parser-rules/{rule_id}",
                            json={"name": "Сезон 2"})
    assert resp.json()["success"] is True
    resp = await client.post("/api/parser-rules/reorder", json=[rule_id])
    assert resp.json()["success"] is True

    # удаление: занятый серией профиль — 400, свободный — ок
    resp = await client.delete("/api/parser-profiles/1")
    assert resp.status_code == 400
    assert parse(resp.json()["error"])["code"] == "profile_in_use"
    resp = await client.delete(f"/api/parser-profiles/{profile_id}")
    assert resp.json()["success"] is True


@pytest.mark.asyncio
async def test_database_admin_tab(system):
    _, db, client = system
    resp = await client.get("/api/database/tables")
    tables = resp.json()
    assert "series" in tables
    assert "auth" not in tables and "tracker_sessions" not in tables

    resp = await client.get("/api/database/table/auth")
    assert resp.status_code == 403
    resp = await client.get("/api/database/table/series")
    assert resp.json()[0]["name"] == "Т"

    resp = await client.post("/api/database/clear_table",
                             json={"table_name": "scan_tasks"})
    assert resp.json()["success"] is True

    # полная очистка БД удалена (Р-22)
    resp = await client.post("/api/database/clear")
    assert resp.status_code in (404, 405)


@pytest.mark.asyncio
async def test_logs_endpoint(system, tmp_path, monkeypatch):
    _, _, client = system
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "info.log").write_text(
        '{"timestamp": "2026-06-12T10:00:00", "level": "INFO", '
        '"group": "scan", "message": "один"}\n'
        '{"timestamp": "2026-06-12T11:00:00", "level": "INFO", '
        '"group": "torrents", "message": "два"}\n', encoding="utf-8")
    monkeypatch.setattr(core_logging, "LOG_DIR", str(log_dir))
    resp = await client.get("/api/logs")
    body = resp.json()
    assert [e["message"] for e in body] == ["два", "один"]  # новые первыми
    resp = await client.get("/api/logs", params={"group": "scan"})
    assert [e["message"] for e in resp.json()] == ["один"]


@pytest.mark.asyncio
async def test_saved_paths_are_fs_safe(system):
    """Saved paths become save_path prefixes: same rule applies."""
    _, _, client = system
    resp = await client.post("/api/settings/saved_paths",
                             json={"path": " /nas/Series: test\\sub "})
    assert resp.status_code == 200
    assert [p["path"] for p in resp.json()["paths"]] == ["/nas/Series test/sub"]


@pytest.mark.asyncio
async def test_parser_rule_rejects_unknown_vocabulary(system):
    _, _, client = system
    resp = await client.post("/api/parser-profiles", json={"name": "Словарь"})
    profile_id = resp.json()["id"]

    # опечатка в типе действия
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Опечатка",
                                   "action_pattern": json.dumps(
                                       [{"action_type": "assign_voiceoverr",
                                         "action_pattern": "X"}])})
    assert resp.status_code == 400
    assert "action_unknown" in resp.json()["error"]

    # опечатка в типе условия
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Опечатка условия",
                                   "conditions": [{"condition_type": "Contains",
                                                   "pattern": '[{"type":'
                                               '"text","value":"x"}]'}]})
    assert resp.status_code == 400
    assert "condition_type_unknown" in resp.json()["error"]

    # корректное правило принимается
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Рабочее",
                                   "action_pattern": json.dumps(
                                       [{"action_type": "assign_voiceover",
                                         "action_pattern": "LostFilm"}]),
                                   "conditions": [{"condition_type": "contains",
                                                   "pattern": '[{"type":'
                                               '"text","value":"x"}]'}]})
    assert resp.status_code == 200
    rule_id = resp.json()["id"]

    # то же самое при обновлении
    resp = await client.put(f"/api/parser-rules/{rule_id}",
                            json={"action_pattern": json.dumps(
                                [{"action_type": "assign_voiceoverr",
                                  "action_pattern": "X"}])})
    assert resp.status_code == 400
    assert "action_unknown" in resp.json()["error"]
