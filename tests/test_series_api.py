"""Тесты блока «серии» этапа 5 (Р-19): CRUD catalog, событийный каскад
удаления, HTTP-роуты gateway (формы старого контракта routes/series.py)."""
import asyncio
import json
import sqlite3

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.contracts.availability import Action, NotAllowed, refusal
from core.db import Database
from core.reasons import parse, reason
from modules.catalog import CatalogModule
from modules.gateway import GatewayModule
from modules.library import LibraryModule
from modules.metadata import MetadataModule
from series_factory import insert_series, source
from settle import settle
from schema_db import migrated


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    migrated(path)
    with sqlite3.connect(path) as conn:
        insert_series(conn, id=1, url="http://kinozal.tv/details.php?id=7", source_type="torrent")
        insert_series(conn, id=2, name="ВК", name_en="Show", site="vk", save_path="/media/s",
                      url="u|q", source_type="vk_video")
        # данные для каскада удаления серии 2
        conn.execute(
            "INSERT INTO series_tmdb_mappings (series_id, tmdb_id, "
            "tmdb_season_number, total_episodes) VALUES (2, 99, 1, 12)")
        conn.commit()
    return str(path)


class Neighbours(BaseModule):
    """Фейки соседей gateway: то, что в проде отвечают scan/torrents/
    sources/downloads — здесь фиксированные ответы + журнал вызовов."""
    name = "neighbours"

    def __init__(self, bus):
        self.calls: list[tuple[str, dict]] = []
        self.planned: dict[int, str] = {}
        self.syncing: set[int] = set()  # the series whose sync the owner of runs says is under way
        super().__init__(bus)

    def register(self):
        async def counts_vk(env):
            return {"counts": {2: 5}}

        async def counts_torrent(env):
            return {"counts": {1: 3}}

        async def queue(env):
            return {"count": 1, "tasks": [
                {"torrent_hash": "h1", "series_id": 1, "stage": "renaming"}]}

        async def progress(env):
            return {"tasks": [{"qb_hash": "h1", "series_id": 1,
                               "series_name": "Тайтл", "status": "downloading",
                               "progress": 55, "dlspeed": 1024, "eta": 60}]}

        async def fs_verify(env):
            self.calls.append(("fs.verify", env.payload))
            return {"missing": 0}

        async def plan_move(env):
            self.calls.append(("plan", env.payload))
            self.planned[env.payload["series_id"]] = env.payload["new_path"]
            return {"new_path": env.payload["new_path"]}

        async def planned_move(env):
            return {"new_path": self.planned.get(env.payload["series_id"])}

        async def edit_begin(env):
            self.calls.append(("edit.begin", env.payload))
            if env.payload["series_id"] in self.syncing:
                raise NotAllowed(refusal(Action.EDIT, "scanning"))
            return {"ok": True}

        async def edit_end(env):
            self.calls.append(("edit.end", env.payload))

        self.handle("planner.downloaded_counts", counts_vk)
        self.handle("torrents.db.downloaded_counts", counts_torrent)
        self.handle("torrents.queue.get", queue)
        self.handle("torrents.db.progress.list", progress)
        self.handle("torrents.fs.verify", fs_verify)
        self.handle("library.relocation.plan", plan_move)
        self.handle("scan.edit.begin", edit_begin)
        self.handle("scan.edit.end", edit_end)
        self.handle("library.relocation.get", planned_move)

        async def dubs(env):
            return {"items": [
                {"season": 1, "episode": 2, "voice_key": "blackcat", "voice_label": "BlackCat",
                 "status": "rejected", "state": "error", "hint": None,
                 "detail": {"code": "suspect_residual", "params": {}}, "temp_path": "/secret/x.mp4", "job_id": "j1"},
                {"season": 1, "episode": 2, "voice_key": "red", "voice_label": "RedHeadSound",
                 "status": "placed", "state": "done", "hint": "done", "detail": None, "temp_path": None, "job_id": "j1"}]}

        self.handle("voices.dubs.get", dubs)


@pytest.fixture
async def system(db_path, tmp_path):
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    neighbours = Neighbours(bus)
    runner = Runner(bus, [gateway, CatalogModule(bus, db),
                          MetadataModule(bus, db), neighbours])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, db, client, neighbours
    await runner.stop()


# --- GET: список и карточка -------------------------------------------------------


def topics(spy) -> list[str]:
    out = []
    while not spy.queue.empty():
        env = spy.queue.get_nowait()
        if env.kind != "reply":
            out.append(env.topic)
    return out


@pytest.mark.asyncio
async def test_series_list_shape(system):
    _, _, client, _ = system
    resp = await client.get("/api/series")
    assert resp.status_code == 200
    data = {s["id"]: s for s in resp.json()}
    assert set(data) == {1, 2}
    # the card: the sources come from the catalog, the series itself has no address and no kind
    torrent, vk = data[1], data[2]
    assert "kind" not in torrent and "kind" not in vk
    assert [(each["url"], each["source_type"]) for each in torrent["sources"]] == [
        ("http://kinozal.tv/details.php?id=7", "torrent")]
    assert [(each["url"], each["source_type"]) for each in vk["sources"]] == [("u|q", "vk_video")]
    for card in (torrent, vk):
        assert not {"url", "source_type", "parser_profile_id", "vk_search_mode", "vk_quality_priority",
                    "availability"} & set(card)
    assert torrent["downloaded_episodes_count"] == 3   # счётчик torrents
    assert vk["downloaded_episodes_count"] == 5        # счётчик scan
    assert vk["tmdb_info"]["tmdb_id"] == 99
    assert torrent["tmdb_info"] is None
    assert torrent["is_busy"] is False
    assert torrent["statuses"] == ["waiting"]
    assert "state" not in torrent  # колонка похоронена (Р-11)


@pytest.mark.asyncio
async def test_series_details_and_404(system):
    bus, _, client, neighbours = system
    spy = bus.subscribe("#")
    resp = await client.get("/api/series/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"][0]["source_type"] == "torrent"
    assert not {"kind", "tracker_info"} & set(body)

    video = (await client.get("/api/series/2")).json()
    assert not {"kind", "tracker_info", "availability"} & set(video)
    # there is no report of players any more: the summary of the sources is composition's, the window takes it itself
    assert "scan.availability" not in topics(spy)

    resp = await client.get("/api/series/777")
    assert resp.status_code == 404
    assert resp.json() == {"error": reason("series_not_found")}


# --- создание и удаление ----------------------------------------------------------


@pytest.mark.asyncio
async def test_add_series_and_event(system):
    bus, _, client, neighbours = system
    sub = bus.subscribe("series.added")
    sources = bus.subscribe("series.source.added")
    resp = await client.post("/api/series", json={
        "name": "Новый",
        "name_en": "New", "site": "kinozal", "save_path": "/media/n",
        "sources": [{"url": "http://kinozal.tv/details.php?id=9", "source_type": "torrent"}],
        "tmdb_data": {"tmdb_id": 5, "tmdb_season_number": 1,
                      "total_episodes": 8}})
    assert resp.status_code == 200
    series_id = resp.json()["series_id"]
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["id"] == series_id
    assert env.payload["name"] == "Новый"
    assert env.payload["statuses"] == ["waiting"]
    # the source appeared like any added later
    added = await asyncio.wait_for(sources.queue.get(), 3)
    assert added.payload["series_id"] == series_id
    # TMDB-маппинг записан владельцем
    details = (await client.get(f"/api/series/{series_id}")).json()
    assert details["tmdb_info"]["tmdb_id"] == 5


@pytest.mark.asyncio
async def test_add_series_of_several_sources_keeps_their_order_and_choices(system):
    _, _, client, neighbours = system
    resp = await client.post("/api/series", json={
        "name": "Видео", "name_en": "Video", "site": "somesite", "save_path": "/media/v",
        "sources": [{"url": "https://one.test/p", "source_type": "web_video", "vibix_season": 2},
                    {"url": "chan|query", "source_type": "vk_video", "players_off": ["vk"]}]})
    assert resp.status_code == 200
    details = (await client.get(f"/api/series/{resp.json()['series_id']}")).json()
    assert [(each["url"], each["position"], each["vibix_season"], each["players_off"])
            for each in details["sources"]] == [("https://one.test/p", 0, 2, []), ("chan|query", 1, None, ["vk"])]
    assert all("vk_search_mode" not in each for each in details["sources"])


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [{"url": "http://x"}, {"source_type": "torrent"}, {"parser_profile_id": 1},
                                      {"vk_search_mode": "search"}, {"vk_quality_priority": "[]"}])
async def test_add_series_does_not_take_the_fields_that_left_the_series(system, removed):
    _, _, client, _ = system
    body = {"name": "Т", "name_en": "T", "site": "s", "save_path": "/media/t",
            "sources": [{"url": "http://kinozal.tv/details.php?id=9", "source_type": "torrent"}], **removed}
    resp = await client.post("/api/series", json=body)
    assert resp.status_code == 422
    assert next(iter(removed)) in json.dumps(resp.json(), ensure_ascii=False)
    assert len((await client.get("/api/series")).json()) == 2  # the series is not created


@pytest.mark.asyncio
async def test_add_series_needs_a_source_and_takes_no_releases(system):
    _, _, client, _ = system
    base = {"name": "Т", "name_en": "T", "site": "s", "save_path": "/media/t"}
    assert (await client.post("/api/series", json=base)).status_code == 422
    assert (await client.post("/api/series", json={**base, "sources": []})).status_code == 422
    # the releases of a torrent page are its source's (stage 15), not a field of the form
    resp = await client.post("/api/series", json={
        **base, "sources": [{"url": "http://kinozal.tv/details.php?id=9", "source_type": "torrent"}],
        "torrents": [{"torrent_id": "t9", "link": "http://kinozal.tv/9"}]})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_add_series_with_a_source_the_catalog_refuses_leaves_no_series_and_says_why(system):
    _, _, client, _ = system
    resp = await client.post("/api/series", json={
        "name": "Т", "name_en": "T", "site": "s", "save_path": "/media/t",
        "sources": [{"url": "https://one.test/p", "source_type": "web_video"},
                    {"url": "https://one.test/p", "source_type": "web_video"}]})
    assert resp.status_code == 400
    assert parse(resp.json()["error"])["code"] == "source_exists"
    assert len((await client.get("/api/series")).json()) == 2


@pytest.mark.asyncio
async def test_add_series_rejects_relative_save_path(system):
    _, _, client, _ = system
    resp = await client.post("/api/series", json={
        "name": "Кривой", "name_en": "Bad", "site": "kinozal", "save_path": "Z/Donghua",
        "sources": [{"url": "http://kinozal.tv/details.php?id=10", "source_type": "torrent"}]})

    assert resp.status_code == 400
    assert "save_path_relative" in resp.json()["error"]


@pytest.mark.asyncio
async def test_delete_cascade_by_owners(db_path, tmp_path):
    """series.deleted: каждый владелец чистит свои таблицы (Р-19)."""
    bus = Bus()
    db = Database(db_path)
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path))
    # реальные владельцы без внешних зависимостей; scan требует соседей
    # только в работе, подписка на series.deleted — чистая БД
    from modules.scan import ScanModule
    modules = [gateway, CatalogModule(bus, db), MetadataModule(bus, db),
               ScanModule(bus, db, scheduler=False), LibraryModule(bus, db)]
    runner = Runner(bus, modules)
    await runner.start()
    try:
        transport = httpx.ASGITransport(app=gateway.app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://test") as client:
            sub = bus.subscribe("series.deleted")
            resp = await client.delete("/api/series/2")
            assert resp.status_code == 200
            env = await asyncio.wait_for(sub.queue.get(), 3)
            assert env.payload == {"series_id": 2}

        async def _empty(table, where="series_id=2"):
            rows = await db.fetch_all(
                f"SELECT * FROM {table} WHERE {where}")
            return not rows

        await settle(bus, *modules)  # the cascade is an event: it ends when every owner has handled it
        assert await _empty("series", "id=2")
        assert await _empty("series_tmdb_mappings")
    finally:
        await runner.stop()


# --- обновление свойств -----------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("series_id", [1, 2])
async def test_update_without_path_change_touches_no_file_whatever_the_sources(system, series_id):
    """A save changes the card and the plan only: the files follow it at the next sync (И-59), so the gateway asks no
    renaming, no relocation and no scan."""
    bus, db, client, neighbours = system
    spy = bus.subscribe("#")
    resp = await client.post(f"/api/series/{series_id}", json={
        "name": "Переименован", "save_path": "/media/t" if series_id == 1 else "/media/s"})
    assert resp.status_code == 200
    row = await db.fetch_one("SELECT name FROM series WHERE id=?", (series_id,))
    assert row["name"] == "Переименован"
    await settle(bus, neighbours)  # the command is background work of the gateway: done when the bus is quiet
    # the path goes to library, which knows the series is there already
    assert ("plan", {"series_id": series_id, "new_path": "/media/t" if series_id == 1 else "/media/s"}) in neighbours.calls
    # the layout of the series is counted again on series.updated; the gateway starts no sync
    assert "scan.series.run" not in topics(spy)


@pytest.mark.asyncio
async def test_update_with_path_change_plans_the_move_and_the_card_shows_it(system):
    _, db, client, neighbours = system
    resp = await client.post("/api/series/1", json={
        "name": "Тайтл", "save_path": "/media/new"})
    assert resp.status_code == 200
    assert ("plan", {"series_id": 1, "new_path": "/media/new"}) in neighbours.calls
    # save_path is written by library after the move in a sync; the gateway does not touch it
    row = await db.fetch_one("SELECT save_path FROM series WHERE id=1")
    assert row["save_path"] == "/media/t"
    card = (await client.get("/api/series/1")).json()
    assert (card["save_path"], card["planned_save_path"]) == ("/media/t", "/media/new")


@pytest.mark.asyncio
@pytest.mark.parametrize("removed", [{"url": "http://x"}, {"source_type": "torrent"}, {"parser_profile_id": 1},
                                      {"vk_search_mode": "get_all"}, {"vk_quality_priority": "[]"},
                                      {"sources": []}])
async def test_update_does_not_take_what_belongs_to_the_sources(system, removed):
    _, db, client, neighbours = system
    resp = await client.post("/api/series/1", json={"name": "Другой", **removed})
    assert resp.status_code == 422
    assert (await db.fetch_one("SELECT name FROM series WHERE id=1"))["name"] == "Тайтл"
    assert not any(call[0] == "relocate" for call in neighbours.calls)


# --- мелкие точки ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_toggle_auto_scan_and_update_event(system):
    bus, db, client, _ = system
    sub = bus.subscribe("series.updated")
    resp = await client.post("/api/series/1/toggle_auto_scan",
                             json={"enabled": True})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    # дельта несёт применённые поля + обязательные statuses/is_busy
    assert env.payload["auto_scan_enabled"] is True
    assert env.payload["is_busy"] is False
    assert env.payload["statuses"] == ["waiting"]
    row = await db.fetch_one(
        "SELECT auto_scan_enabled FROM series WHERE id=1")
    assert row["auto_scan_enabled"] == 1

    resp = await client.post("/api/series/1/toggle_auto_scan", json={})
    assert resp.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("series_id", [1, 2])
async def test_state_route_drives_viewing_and_asks_each_owner_to_check_its_files(system, series_id):
    bus, _, client, neighbours = system
    sub = bus.subscribe("series.status.changed")
    spy = bus.subscribe("#")
    resp = await client.post(f"/api/series/{series_id}/state",
                             json={"state": ["viewing"]})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert env.payload["series_id"] == series_id
    assert "viewing" in env.payload["statuses"]
    # opening the window asks the owner of the torrent files to check its own with the disk; the gateway chooses nothing
    await settle(bus, neighbours)
    assert ("fs.verify", {"series_id": series_id}) in neighbours.calls
    assert "catalog.series.get" not in topics(spy)
    resp = await client.post(f"/api/series/{series_id}/state", json={"state": []})
    assert resp.status_code == 200
    env = await asyncio.wait_for(sub.queue.get(), 3)
    assert "viewing" not in env.payload["statuses"]


@pytest.mark.asyncio
async def test_small_endpoints(system):
    _, db, client, _ = system
    resp = await client.post("/api/series/1/ignored-seasons",
                             json={"seasons": [0, 3]})
    assert resp.status_code == 200
    row = await db.fetch_one(
        "SELECT ignored_seasons FROM series WHERE id=1")
    assert json.loads(row["ignored_seasons"]) == [0, 3]

    resp = await client.put("/api/series/1/ignored-episodes",
                            json={"episodes": {"1": [5, 6]}})
    assert resp.status_code == 200
    row = await db.fetch_one(
        "SELECT ignored_episodes FROM series WHERE id=1")
    assert json.loads(row["ignored_episodes"]) == {"1": [5, 6]}
    resp = await client.put("/api/series/1/ignored-episodes", json={})
    assert resp.status_code == 400

    # the priority of resolutions of the user left with its column: the rule of the reference applies
    resp = await client.put("/api/series/2/vk-quality-priority", json={"priority": ["1080p", "720p"]})
    assert resp.status_code in (404, 405)

    # the progress of the torrents comes from torrent_progress of the torrents module, not from the pipeline
    resp = await client.get("/api/series/active_torrents")
    body = resp.json()
    assert body[0]["qb_hash"] == "h1"
    assert body[0]["series_name"] == "Тайтл"
    assert body[0]["progress"] == 55


@pytest.mark.asyncio
async def test_create_and_update_store_fs_safe_names(system):
    """The column owner sanitizes path and file-name fields on every
    write, so forbidden characters never reach the DB from any client."""
    _, _, client, _ = system
    resp = await client.post("/api/series", json={
        "name": "Кто: доктор",
        "name_en": '  Doctor "Who"?  ', "site": "kinozal",
        "save_path": "/media/Doctor Who: Flux (2021)\\extra",
        "sources": [{"url": "http://kinozal.tv/details.php?id=10", "source_type": "torrent"}],
        "quality_override": "WEB-DL/1080p"})
    assert resp.status_code == 200
    series_id = resp.json()["series_id"]
    details = (await client.get(f"/api/series/{series_id}")).json()
    assert details["name"] == "Кто: доктор"  # display title stays as typed
    assert details["name_en"] == "Doctor Who"
    assert details["save_path"] == "/media/Doctor Who Flux (2021)/extra"
    assert details["quality_override"] == "WEB-DL1080p"

    resp = await client.post(f"/api/series/{series_id}",
                             json={"name_en": "New*Name", "season": "s01"})
    assert resp.status_code == 200
    details = (await client.get(f"/api/series/{series_id}")).json()
    assert details["name_en"] == "NewName"


@pytest.mark.asyncio
async def test_a_save_of_the_card_is_one_admitted_change_and_is_refused_whole_while_a_sync_runs(system):
    """The owner of the runs admits the save before its first write and hears its end after its last: a sync does not open
    between the card and the plan of its folder (И-60). Refused, nothing of the save is written."""
    bus, db, client, neighbours = system
    resp = await client.post("/api/series/1", json={"name": "Тайтл", "save_path": "/media/new"})
    assert resp.status_code == 200
    await settle(bus, neighbours)  # the end of the change is a command
    names = [call[0] for call in neighbours.calls if call[0] in ("edit.begin", "plan", "edit.end")]
    assert names == ["edit.begin", "plan", "edit.end"]
    neighbours.calls.clear()
    neighbours.syncing.add(1)
    resp = await client.post("/api/series/1", json={"name": "Другое", "save_path": "/media/other"})
    assert resp.status_code == 409
    assert (await db.fetch_one("SELECT name FROM series WHERE id=1"))["name"] == "Тайтл"
    assert [call[0] for call in neighbours.calls] == ["edit.begin"]
