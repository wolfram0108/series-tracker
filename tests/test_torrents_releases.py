"""The life of a release of a torrent source (stage 15) on the model of qBittorrent (tests/qbit_model.py) reached by the real
client of the module over HTTP: «Скачать» into the staging folder, `ready` once every wanted file is there, the update into
the same folder (the old version leaves without its files, the files the new one does not list are removed, nothing else
is written twice), the update held while a sync of a series with the page runs, the files not to download, the release
gone from the client, a page no source lists any more."""
from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import datetime, timedelta

import pytest

from app_stand import Served
from core.bus import Bus
from core.module import BaseModule
from core.contracts import SeriesListReply
from core.topics import SCAN_RUN_CHANGED
from series_factory import series_card
from core.contracts.torrent import PageRelease, PageReleases
from core.db import Database
from calc.schedule import next_at
from core.topics import TORRENTS_RELEASE_CHANGED
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
        self.auto: set[int] = set()  # the series in the automatic mode
        self.sync_on = "false"  # the setting scanner_agent_enabled

    def register(self) -> None:
        for topic, handler in (("sources.releases.get", self.on_releases), ("sources.torrent_file.get", self.on_file),
                               ("sources.site.resolve", self.on_resolve),
                               ("catalog.series.get", self.on_series), ("catalog.series.list", self.on_list),
                               ("scan.runs.get", self.on_runs), ("settings.value.get", self.on_setting),
                               ("settings.value.set", self.on_set)):
            self.handle(topic, handler)

    async def on_releases(self, env):
        return PageReleases(url=PAGE, site="kinozal", title="Тайтл", releases=self.page).model_dump(mode="json")

    async def on_file(self, env):
        return {"content_b64": base64.b64encode(self.torrents[env.payload["url"]]).decode(), "from_cache": False}

    async def on_resolve(self, env):
        return {"site": "kinozal", "canonical_url": env.payload["url"], "source_type": "torrent"}

    async def on_series(self, env):
        return series_card(id=env.payload["series_id"],
                           sources=[{"url": u} for u in self.series.get(env.payload["series_id"], [])])

    async def on_list(self, env):
        return SeriesListReply(series=tuple(series_card(
            id=sid, name=f"Сериал {sid}", auto_scan_enabled=sid in self.auto, sources=[{"url": u} for u in urls])
            for sid, urls in self.series.items()))

    async def on_runs(self, env):
        return {"runs": [], "as_of": 0}

    async def on_setting(self, env):
        if env.payload["key"] == "scanner_agent_enabled":
            return {"key": env.payload["key"], "value": self.sync_on}
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
    told = bus.subscribe(TORRENTS_RELEASE_CHANGED.name)
    await world.start()
    await torrents.start()
    yield model, world, torrents, told
    await torrents.stop()
    await world.stop()
    await client.close()
    served.close()


async def until(told, state: str, wanted=lambda news: True) -> dict:
    """The next news of the release in `state` that `wanted` accepts; every news before it is passed by (the client's word
    of the torrent is told too, so one state can be told several times)."""
    async def wait():
        while True:
            news = (await told.queue.get()).payload
            if news["state"] == state and wanted(news):
                return news
    return await asyncio.wait_for(wait(), LIMIT_S)


async def test_a_window_that_connects_gets_the_picture_of_the_monitor_at_once(stand):
    model, world, torrents, told = stand
    h = world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    await until(told, "ready")
    monitor = torrents.bus.subscribe("torrents.progress.changed")
    world.publish_event("gateway.sse.clients", {"count": 1})
    tasks = (await asyncio.wait_for(monitor.queue.get(), LIMIT_S)).payload["tasks"]
    assert [(t["kind"], t["qb_hash"], t["status"]) for t in tasks] == [("release", h, "ready")]


async def test_an_update_comes_into_the_same_folder_and_leaves_only_the_files_of_the_new_version(stand):
    model, world, torrents, told = stand
    h1 = world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    first = await until(told, "ready")
    folder = first["folder"]
    kept = os.stat(f"{folder}/Show/E01.mkv").st_mtime_ns

    h2 = world.lists(model, release(V2), published="d2", link="L2")
    assert (await world.request("torrents.release.check", {"url": PAGE})).releases == ("page",)
    updating = await until(told, "updating")
    ready = await until(told, "ready", lambda news: news["infohash"] == h2)

    assert h1 not in model.torrents and h2 in model.torrents
    assert ("torrents/delete", {"hashes": h1, "deleteFiles": "false"}) in model.calls
    assert updating["infohash"] == ready["infohash"] == h2 and ready["folder"] == folder
    on_disk = sorted(os.path.relpath(os.path.join(root, name), folder) for root, _, names in os.walk(folder) for name in names)
    assert on_disk == sorted(path for path, _ in V2)  # old_name.mkv, renamed to E04 by the uploader, is gone
    assert os.stat(f"{folder}/Show/E01.mkv").st_mtime_ns == kept  # a file the update did not change is not written again
    versions = await torrents.releases.store.versions((await torrents.releases.store.get(PAGE, "page"))["id"])
    assert [v["infohash"] for v in versions] == [h1, h2]

    # The client checks the files again (the check an update begins with): the window is told what the client says and the
    # series whose source is the page shows a check on its card while it lasts, then its release is ready again.
    status = torrents.bus.subscribe("series.status.contribution")

    async def flags_until(wanted: dict[str, bool]) -> None:
        async def wait():
            while True:
                flags = (await status.queue.get()).payload["flags"]
                if all(flags[name] == on for name, on in wanted.items()):
                    return
        await asyncio.wait_for(wait(), LIMIT_S)

    model.checking.add(h2)
    await flags_until({"checking": True, "ready": False})
    assert (await world.request("torrents.releases.get", {"url": PAGE})).releases[0].client.startswith("checking")
    model.checking.discard(h2)
    await flags_until({"checking": False, "ready": True})


async def test_an_update_waits_for_the_end_of_a_sync_of_a_series_with_the_page(stand):
    model, world, torrents, told = stand
    world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    await until(told, "ready")

    world.publish_event(SCAN_RUN_CHANGED, {"series_id": 1, "active": True, "seq": 1})
    await asyncio.wait_for(_until_held(torrents), LIMIT_S)
    h2 = world.lists(model, release(V2), published="d2", link="L2")
    await world.request("torrents.release.check", {"url": PAGE})
    waiting = await until(told, "ready", lambda news: news["pending"])
    assert h2 not in model.torrents

    world.publish_event(SCAN_RUN_CHANGED, {"series_id": 1, "active": False, "seq": 2})
    ready = await until(told, "ready", lambda news: news["infohash"] == h2)
    assert not ready["pending"]


async def _until_held(torrents) -> None:
    while not torrents.releases.held(PAGE):
        await asyncio.sleep(0.01)


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


async def test_a_check_tells_when_it_was_and_the_next_check_of_the_schedule_and_a_page_past_its_time_is_due(stand):
    """«Перечитать» and the times of the schedule check the page: every watched release gets `checked_at`, and `fresh_until`
    is the next check of the schedule after it; a change of the schedule tells every release again. A page not checked
    since the last time of the schedule is due (the loop checks it at once, the start too)."""
    model, world, torrents, told = stand
    world.lists(model, release(V1), published="d1", link="L1")
    await world.request("torrents.release.download", {"url": PAGE, "release": "page"})
    ready = await until(told, "ready")
    assert (ready["checked_at"], ready["fresh_until"]) == (None, None)

    schedule = {"syncs": ["00:00", "12:00"], "torrent_mode": "between", "torrent_free": [], "torrent_between": 1}
    world.publish_event("settings.changed", {"key": "schedule", "value": json.dumps(schedule)})
    while torrents.releases.checks != (360, 1080):
        await asyncio.wait_for(told.queue.get(), LIMIT_S)
    await world.request("torrents.release.check", {"url": PAGE})
    checked = await until(told, "ready", lambda news: news["checked_at"] is not None)
    at = datetime.fromisoformat(checked["checked_at"])
    assert datetime.fromisoformat(checked["fresh_until"]) == next_at((360, 1080), at)

    assert await torrents.releases.due(None, {PAGE}) == []
    assert await torrents.releases.due(at - timedelta(minutes=1), {PAGE}) == []
    assert await torrents.releases.due(at + timedelta(minutes=1), {PAGE}) == [PAGE]
    assert await torrents.releases.due(at + timedelta(minutes=1), set()) == []

