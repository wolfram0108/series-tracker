"""Тесты блока «настройки и справочники» этапа 5 (Р-22): авторизации,
parse_url, настройки и debug-группы, конструктор правил, трекеры,
каталоги, админ-вкладка БД."""
import sqlite3
import subprocess
import sys

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
from core import ids


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}",
                        "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")  # до конкурентных подключений
    with conn:
        conn.execute(
            "INSERT INTO trackers (id, canonical_name, display_name, "
            "mirrors, parser_class, auth_type, ui_features) VALUES "
            "(1, 'kinozal', 'Kinozal', '[\"kinozal.tv\"]', "
            "'KinozalParser', 'kinozal', '{}')")
        conn.execute(
            "INSERT INTO parser_profiles (id, name, preferred_voiceovers) "
            "VALUES (1, 'Занятый', '')")
        conn.execute(
            "INSERT INTO series (id, url, name, name_en, site, save_path, "
            "state, source_type, auto_scan_enabled, vk_search_mode, "
            "parser_profile_id) VALUES (1, 'http://x', 'Т', 'T', 'kinozal', "
            "'/media/t', 'waiting', 'torrent', 0, 'search', 1)")
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
               LibraryModule(bus, db, allowed_roots=None)]
    runner = Runner(bus, modules)
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client
    await runner.stop()


@pytest.mark.asyncio
async def test_auth_roundtrip(system):
    _, _, client = system
    resp = await client.post("/api/auth", json={
        "qbittorrent": {"url": "http://qb:8080", "username": "admin",
                        "password": "pass"},
        "vk": {"token": "vk-token-1"},
        "tmdb": {"token": "tmdb-token-1"}})
    assert resp.json() == {"success": True}

    # Этап 3: GET НЕ отдаёт секреты — только логин/URL и флаги «задан».
    resp = await client.get("/api/auth")
    body = resp.json()
    assert body["qbittorrent"] == {"url": "http://qb:8080",
                                   "username": "admin", "has_password": True}
    assert "password" not in body["qbittorrent"]
    assert body["vk"]["has_password"] is True and "password" not in body["vk"]
    assert body["tmdb"] == {"configured": True}
    assert body["kinozal"] == {}

    # пустой пароль при сохранении НЕ затирает существующий (Этап 3)
    await client.post("/api/auth", json={
        "qbittorrent": {"url": "http://qb:9090", "username": "admin2",
                        "password": ""}})
    qb = (await client.get("/api/auth")).json()["qbittorrent"]
    assert qb["url"] == "http://qb:9090" and qb["username"] == "admin2"
    assert qb["has_password"] is True  # пароль сохранён, не затёрт


@pytest.mark.asyncio
async def test_parse_url_form(system):
    _, _, client = system
    resp = await client.post("/api/parse_url", json={"url": "http://kinozal.tv/details.php?id=1"})
    body = resp.json()
    assert body["success"] is True
    assert body["title"] == {"ru": "Тайтл", "en": "Title"}
    t = body["torrents"][0]
    assert t["date_time"] == "2026-01-01 00:00"  # date_marker → date_time
    # torrent_id посчитан в sources по формуле-констрейнту (core/ids)
    assert t["torrent_id"] == ids.torrent_id("http://kinozal.tv/dl/1",
                                             "2026-01-01 00:00")
    assert body["tracker_info"]["canonical_name"] == "kinozal"


@pytest.mark.asyncio
async def test_settings_flags_and_debug_groups(system):
    _, _, client = system
    resp = await client.get("/api/settings/force_replace")
    assert resp.json() == {"enabled": False}
    await client.post("/api/settings/force_replace",
                      json={"enabled": True})
    resp = await client.get("/api/settings/force_replace")
    assert resp.json() == {"enabled": True}

    resp = await client.get("/api/settings/parallel_downloads")
    assert resp.json() == {"value": 2}
    await client.post("/api/settings/parallel_downloads",
                      json={"value": 4})
    resp = await client.get("/api/settings/parallel_downloads")
    assert resp.json() == {"value": 4}

    # debug-флаги: структура с бэкенда + включение группы (принцип 6)
    resp = await client.get("/api/settings/debug_flags")
    body = resp.json()
    names = [m["name"] for g in body["logging_modules"].values()
             for m in g]
    assert "scan" in names and "torrents" in names
    await client.post("/api/settings/debug_flags",
                      json={"module": "scan", "enabled": True})
    assert "scan" in core_logging.debug_groups()
    await client.post("/api/settings/debug_flags",
                      json={"module": "scan", "enabled": False})
    assert "scan" not in core_logging.debug_groups()


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
    assert resp.json()[0]["canonical_name"] == "kinozal"
    resp = await client.put("/api/trackers/1", json={
        "mirrors": ["kinozal.tv", "kinozal.me"]})
    assert resp.json()["success"] is True
    resp = await client.get("/api/trackers")
    assert resp.json()[0]["mirrors"] == ["kinozal.tv", "kinozal.me"]
    resp = await client.put("/api/trackers/1", json={})
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
                                                   "pattern": "сезон"}]})
    rule_id = resp.json()["id"]
    resp = await client.get(f"/api/parser-profiles/{profile_id}/rules")
    rules = resp.json()
    assert rules[0]["name"] == "Сезон"
    assert rules[0]["conditions"][0]["pattern"] == "сезон"

    resp = await client.put(f"/api/parser-rules/{rule_id}",
                            json={"name": "Сезон 2"})
    assert resp.json()["success"] is True
    resp = await client.post("/api/parser-rules/reorder", json=[rule_id])
    assert resp.json()["success"] is True

    # тест конструктора — форма старого ответа
    resp = await client.post("/api/parser-profiles/test", json={
        "profile_id": profile_id,
        "videos": [{"title": "Тайтл 5 серия"}]})
    body = resp.json()
    assert body[0]["source_data"] == {"title": "Тайтл 5 серия"}
    assert "extracted" in body[0]["result"]

    # удаление: занятый серией профиль — 400, свободный — ок
    resp = await client.delete("/api/parser-profiles/1")
    assert resp.status_code == 400
    assert "используется" in resp.json()["error"]
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
