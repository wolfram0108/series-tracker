"""The life of a release of a torrent source (stage 15) on the model of qBittorrent (tests/qbit_model.py) reached by the real
client of the module over HTTP: «Скачать» into the staging folder, `ready` once every wanted file is there, the update into
the same folder (the old version leaves without its files, the files the new one does not list are removed, nothing else
is written twice), the update held while a sync of a series with the page runs, the files not to download, the release
gone from the client, a page no source lists any more."""
from __future__ import annotations

import asyncio
import base64
import os

import pytest

from app_stand import Served
from core import BaseModule, Bus
from core.contracts import RUN_CHANGED
from core.contracts.torrent import RELEASE_CHANGED_TOPIC, PageRelease, PageReleases
from core.db import Database
from modules.torrents.module import TorrentsModule
from modules.torrents.qbt_client import QbtClient
from qbit_model import V1, V2, QbitModel, release
from schema_db import migrated

PAGE = "https://kinozal.tv/details.php?id=7"
LIMIT_S = 10.0


class World(BaseModule):
    """The modules `torrents` asks: the tracker page (sources), the series and their sources (catalog), the open syncs
    (scan) and the settings."""
    name = "world"

    def __init__(self, bus, staging: str) -> None:
        super().__init__(bus)
        self.staging = staging
        self.page: list[PageRelease] = []
        self.torrents: dict[str, bytes] = {}
        self.series: dict[int, list[str]] = {1: [PAGE]}

    def register(self) -> None:
        for topic, handler in (("sources.releases.get", self.on_releases), ("sources.torrent_file.get", self.on_file),
                               ("sources.site.resolve", self.on_resolve), ("sources.term.get", self.on_term),
                               ("catalog.series.get", self.on_series), ("catalog.series.list", self.on_list),
                               ("scan.runs.get", self.on_runs), ("settings.value.get", self.on_setting),
                               ("settings.value.set", self.on_set)):
            self.handle(topic, handler)

    async def on_releases(self, env):
        return PageReleases(url=PAGE, site="kinozal", title="Тайтл", releases=self.page).model_dump(mode="json")

    async def on_file(self, env):
        return {"content_b64": base64.b64encode(self.torrents[env.payload["url"]]).decode()}

    async def on_resolve(self, env):
        return {"site": "kinozal", "canonical_url": env.payload["url"], "source_type": "torrent"}

    async def on_term(self, env):
        return {"hours": 1}

    async def on_series(self, env):
        return {"id": env.payload["series_id"], "sources": [{"url": u} for u in self.series.get(env.payload["series_id"], [])]}

    async def on_list(self, env):
        return [{"id": sid, "name": f"Сериал {sid}", "sources": [{"url": u, "source_type": "torrent"} for u in urls]}
                for sid, urls in self.series.items()]

    async def on_runs(self, env):
        return {"runs": [], "as_of": 0}

    async def on_setting(self, env):
        return {"key": env.payload["key"], "value": self.staging}

    async def on_set(self, env):
        self.staging = env.payload["value"]
        return env.payload

    def lists(self, model: QbitModel, files, *, published: str, link: str, hold: bool = False) -> str:
        meta, h = model.seed(files, hold=hold)
        self.torrents[link] = meta
        self.page = [PageRelease(release="page", title="1-12", link=link, published=published)]
        return h


@pytest.fixture
async def stand(tmp_path):
    model = QbitModel()
    served = Served(model.app)
    client = QbtClient(served.url, model.username, model.password, retries=1)
    await client.login()
    bus = Bus()
    db = Database(str(migrated(tmp_path / "t.db")))
    world = World(bus, str(tmp_path / "staging"))
    torrents = TorrentsModule(bus, db, qbt=client, pipeline_poll=0.02, monitor_active=0.02, monitor_idle=0.05)
    told = bus.subscribe(RELEASE_CHANGED_TOPIC)
    await world.start()
    await torrents.start()
    yield model, world, torrents, told
    await torrents.stop()
    await world.stop()
    await client.close()
    served.close()


async def until(told, state: str) -> dict:
    """The next news of the release in `state`; every news before it is passed by."""
    async def wait():
        while True:
            news = (await told.queue.get()).payload
            if news["state"] == state:
                return news
    return await asyncio.wait_for(wait(), LIMIT_S)


async def test_a_release_downloads_into_its_folder_and_is_ready_when_every_file_is_there(stand):
    model, world, torrents, told = stand
    h = world.lists(model, release(V1), published="05.05.2026 10:15:00", link="https://dl.kinozal.tv/download.php?id=7")

    view = await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    ready = await until(told, "ready")

    assert view["folder"].startswith(world.staging) and ready["folder"] == view["folder"]
    assert sorted(f["path"] for f in ready["files"]) == sorted(path for path, _ in V1)
    assert all(f["progress"] == 1 for f in ready["files"])
    for path, content in release(V1):
        assert open(f"{ready['folder']}/{path}", "rb").read() == content
    assert model.torrents[h].category == "st-source"
    kept = (await world.request("torrents.releases.get", {"url": PAGE}))["releases"]
    assert [(each["release"], each["state"], each["infohash"]) for each in kept] == [("page", "ready", h)]


async def test_the_monitor_shows_a_release_the_client_holds_for_every_series_with_its_page_ready_or_not(stand):
    model, world, torrents, told = stand
    world.series[2] = [PAGE]
    h = world.lists(model, release(V1), published="d1", link="L1", hold=True)
    monitor = torrents.bus.subscribe("torrents.progress.changed")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})

    async def release_tasks(done: bool):
        while True:
            tasks = [t for t in (await monitor.queue.get()).payload["tasks"] if t.get("kind") == "release"]
            if bool(tasks) != done and all(t["files_total"] for t in tasks):
                return tasks
    working = await asyncio.wait_for(release_tasks(False), LIMIT_S)
    assert [(t["series_id"], t["series_name"], t["qb_hash"], t["status"], t["files_total"]) for t in working] == [
        (1, "Сериал 1", h, "downloading", len(V1)), (2, "Сериал 2", h, "downloading", len(V1))]
    assert all(0 <= t["progress"] < 100 and t["files_done"] < t["files_total"] for t in working)
    model.deliver(h)
    await until(told, "ready")

    async def ready_tasks():
        while True:
            tasks = [t for t in (await monitor.queue.get()).payload["tasks"] if t.get("kind") == "release"]
            if tasks and all(t["status"] == "ready" for t in tasks):
                return tasks
    ready = await asyncio.wait_for(ready_tasks(), LIMIT_S)
    assert [(t["series_id"], t["progress"], t["files_done"], t["files_total"]) for t in ready] == [
        (1, 100.0, len(V1), len(V1)), (2, 100.0, len(V1), len(V1))]


async def test_the_folder_of_the_releases_is_an_absolute_path_written_to_the_settings(stand):
    model, world, torrents, told = stand
    refused = await world.request("torrents.staging.set", {"value": "staging"})
    assert not refused["ok"] and "staging_dir_not_absolute" in refused["reason"]
    assert await world.request("torrents.staging.set", {"value": " /nas/staging "}) == {"ok": True, "value": "/nas/staging"}
    assert world.staging == "/nas/staging"
    assert await world.request("torrents.staging.get", {}) == {"value": "/nas/staging"}


async def test_no_download_begins_without_the_staging_folder_or_for_a_release_the_page_does_not_list(stand):
    model, world, torrents, told = stand
    world.lists(model, release(V1), published="d1", link="L1")
    with pytest.raises(Exception, match="release_not_on_page"):
        await world.request("torrents.release.download", {"url": PAGE, "release": "1080p"})
    torrents.releases.staging = ""
    with pytest.raises(Exception, match="staging_dir_not_set"):
        await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    assert model.torrents == {}


async def test_an_update_comes_into_the_same_folder_and_leaves_only_the_files_of_the_new_version(stand):
    model, world, torrents, told = stand
    h1 = world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    first = await until(told, "ready")
    folder = first["folder"]
    kept = os.stat(f"{folder}/Show/E01.mkv").st_mtime_ns

    h2 = world.lists(model, release(V2), published="d2", link="L2")
    assert (await world.request("torrents.release.check", {"url": PAGE}))["releases"] == ["page"]
    updating = await until(told, "updating")
    ready = await until(told, "ready")

    assert h1 not in model.torrents and h2 in model.torrents
    assert ("torrents/delete", {"hashes": h1, "deleteFiles": "false"}) in model.calls
    assert updating["infohash"] == ready["infohash"] == h2 and ready["folder"] == folder
    on_disk = sorted(os.path.relpath(os.path.join(root, name), folder) for root, _, names in os.walk(folder) for name in names)
    assert on_disk == sorted(path for path, _ in V2)  # old_name.mkv, renamed to E04 by the uploader, is gone
    assert os.stat(f"{folder}/Show/E01.mkv").st_mtime_ns == kept  # a file the update did not change is not written again
    versions = await torrents.releases.store.versions((await torrents.releases.store.get(PAGE, "page"))["id"])
    assert [v["infohash"] for v in versions] == [h1, h2]


async def test_an_update_waits_for_the_end_of_a_sync_of_a_series_with_the_page(stand):
    model, world, torrents, told = stand
    world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    await until(told, "ready")

    world.publish_event(RUN_CHANGED, {"series_id": 1, "active": True, "seq": 1})
    await asyncio.wait_for(_until_held(torrents), LIMIT_S)
    h2 = world.lists(model, release(V2), published="d2", link="L2")
    await world.request("torrents.release.check", {"url": PAGE})
    waiting = (await told.queue.get()).payload
    assert waiting["pending"] and waiting["state"] == "ready" and h2 not in model.torrents

    world.publish_event(RUN_CHANGED, {"series_id": 1, "active": False, "seq": 2})
    ready = await until(told, "ready")
    assert ready["infohash"] == h2 and not ready["pending"]


async def _until_held(torrents) -> None:
    while not torrents.releases.held(PAGE):
        await asyncio.sleep(0.01)


async def test_a_file_not_to_download_stays_off_the_disk_and_the_release_is_ready_without_it(stand):
    model, world, torrents, told = stand
    h = world.lists(model, release(V1), published="d1", link="L1", hold=True)
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    await world.request("torrents.release.priorities", {"url": PAGE, "release": "page", "skip": ["Show/E02.mkv"]})
    model.deliver(h)
    ready = await until(told, "ready")

    assert {f["path"]: f["priority"] for f in ready["files"]}["Show/E02.mkv"] == 0
    assert not os.path.exists(f"{ready['folder']}/Show/E02.mkv") and os.path.exists(f"{ready['folder']}/Show/E01.mkv")


async def test_a_release_gone_from_the_client_fails_with_that_reason(stand):
    model, world, torrents, told = stand
    h = world.lists(model, release(V1), published="d1", link="L1", hold=True)
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    model.torrents.pop(h)
    failed = await until(told, "failed")
    assert "torrent_gone_from_client" in failed["reason"]


async def test_a_page_no_source_lists_leaves_the_client_with_its_files(stand):
    model, world, torrents, told = stand
    h = world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    folder = (await until(told, "ready"))["folder"]

    world.series = {1: []}
    world.publish_event("series.source.removed", {"series_id": 1, "url": PAGE})
    async def gone():
        while os.path.exists(folder) or await torrents.releases.store.of_page(PAGE):
            await asyncio.sleep(0.01)
    await asyncio.wait_for(gone(), LIMIT_S)
    assert h not in model.torrents
