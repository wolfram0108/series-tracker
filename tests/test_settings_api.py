"""Тесты блока «настройки и справочники» этапа 5 (Р-22): авторизации,
parse_url, настройки и debug-группы, конструктор правил, трекеры,
каталоги, админ-вкладка БД."""
import json
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
from modules.voices import VoicesModule
from core import ids
from core.reasons import parse
from series_factory import insert_series


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


A_SITE_PAGE = {"ruchime": "https://1.ruchime.org/release/x/episode-3?off=vk#top",
               "dunhuatv": "https://www.dunhuatv.ru/28-x-smotret-onlajn.html?a=1#cvh=2",
               "vk_channel": "https://vk.com/@chan/videos|запрос"}


@pytest.mark.asyncio
@pytest.mark.parametrize("site", sorted(A_SITE_PAGE))
async def test_parse_url_refuses_a_page_a_site_reads_with_a_code_and_reads_nothing(system, site):
    """The gateway knows no site: `sources` says that a site reads the address, and the page is not parsed."""
    _, _, client = system
    resp = await client.post("/api/parse_url", json={"url": A_SITE_PAGE[site]})
    assert resp.status_code == 400
    assert parse(resp.json()["error"]) == {"code": "not_a_torrent_page", "params": {}}


@pytest.mark.asyncio
async def test_parse_url_of_a_torrent_page_has_only_what_a_torrent_form_needs(system):
    _, _, client = system
    body = (await client.post("/api/parse_url", json={"url": "http://kinozal.tv/details.php?id=1"})).json()
    assert set(body) == {"success", "title", "torrents", "tracker_info"}


@pytest.mark.asyncio
async def test_parse_url_of_an_address_nobody_reads_says_so_in_the_words_of_sources(system):
    _, _, client = system
    resp = await client.post("/api/parse_url", json={"url": "http://nowhere.test/page"})
    assert resp.status_code == 400
    assert "трекер не распознан" in resp.json()["error"]


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
    assert "неизвестный тип действия" in resp.json()["error"]

    # опечатка в типе условия
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Опечатка условия",
                                   "conditions": [{"condition_type": "Contains",
                                                   "pattern": '[{"type":'
                                               '"text","value":"x"}]'}]})
    assert resp.status_code == 400
    assert "неизвестный тип условия" in resp.json()["error"]

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
    assert "неизвестный тип действия" in resp.json()["error"]


@pytest.mark.asyncio
async def test_parser_rule_update_keeps_conditions_when_absent(system):
    _, _, client = system
    resp = await client.post("/api/parser-profiles", json={"name": "Условия"})
    profile_id = resp.json()["id"]
    resp = await client.post(f"/api/parser-profiles/{profile_id}/rules",
                             json={"name": "Сезон",
                                   "conditions": [{"condition_type": "contains",
                                                   "pattern": '[{"type":'
                                               '"text","value":"сезон"}]'}]})
    rule_id = resp.json()["id"]

    # обновление без conditions — условия сохраняются
    resp = await client.put(f"/api/parser-rules/{rule_id}",
                            json={"name": "Сезон 2"})
    assert resp.json()["success"] is True
    resp = await client.get(f"/api/parser-profiles/{profile_id}/rules")
    rules = resp.json()
    assert rules[0]["name"] == "Сезон 2"
    assert len(rules[0]["conditions"]) == 1

    # пустой список — условия удаляются
    resp = await client.put(f"/api/parser-rules/{rule_id}",
                            json={"conditions": []})
    assert resp.json()["success"] is True
    resp = await client.get(f"/api/parser-profiles/{profile_id}/rules")
    assert resp.json()[0]["conditions"] == []


@pytest.mark.asyncio
async def test_parser_rules_list_marks_invalid_rule(system):
    _, db, client = system
    resp = await client.post("/api/parser-profiles", json={"name": "Битое"})
    profile_id = resp.json()["id"]
    # правило с негодным шаблоном — мимо API: валидация его бы отвергла
    await db.execute(
        "INSERT INTO parser_rules (profile_id, name, priority, "
        "action_pattern, continue_after_match) VALUES (?, ?, ?, ?, 0)",
        (profile_id, "Битое", 1, "[]"))
    rule = await db.fetch_one(
        "SELECT id FROM parser_rules WHERE profile_id=?", (profile_id,))
    await db.execute(
        "INSERT INTO parser_rule_conditions (rule_id, condition_type, "
        "pattern, logical_operator) VALUES (?, ?, ?, ?)",
        (rule["id"], "contains", "не json", "AND"))
    resp = await client.get(f"/api/parser-profiles/{profile_id}/rules")
    rules = resp.json()
    assert rules[0]["valid"] is False
    assert rules[0]["error"]
