"""The border of scan and downloads with the real modules on one database: what the user changes in the output reaches the
queue of downloads, and a new video behind a row is fetched as a video of its own.

Catalog, scan and downloads are real; the other neighbours are `World` of tests/scan_support.py, which here leaves the
queue and the download to the real module. What a test waits for is a row of the database or a token of a neighbour,
never time; the one number (STUCK_S) only ends a test that is stuck.
"""
import asyncio
import json
import sqlite3

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.scan import ScanModule
from scan_support import STUCK_S, World, migrated_db, row
from series_factory import create_series, source
from test_downloads_module import FakeDownloader

URL = "https://fake.test/p/1"


class Neighbours(World):
    """The world of scan without the modules this test makes real."""

    def register(self) -> None:
        h = self.handle
        h("renaming.reprocess", self.on_reprocess)
        h("voices.dispatch", self.on_dispatch)
        h("voices.settings.get", self.on_voice_settings)
        h("composition.get", self.on_layout)
        h("composition.recompute", self.on_recompute)
        h("sources.table.get", self.on_table)
        h("torrents.queue.get", self.on_torrent_queue)
        h("settings.value.get", self.on_setting_get)
        h("settings.value.set", self.on_setting_set)
        h("rules.format_filename", self.on_format)
        h("sources.video.stream", self.on_stream)

    format_gate: asyncio.Event | None = None  # while it is closed, nobody gets the name of a file
    format_asked: asyncio.Event | None = None

    async def on_format(self, env) -> dict:
        if self.format_asked is not None:
            self.format_asked.set()
        if self.format_gate is not None:
            await self.format_gate.wait()
        item = env.payload["media_item"]
        return {"filename": f"Серия {item['episode_start']:02d}.mp4"}

    async def on_stream(self, env) -> dict:
        return {"handled": False}


class UrlDownloader(FakeDownloader):
    """The file it writes holds the address it was asked for, so a test can tell whose video lies where."""

    async def __call__(self, url, path, on_progress, **kw):
        done = await super().__call__(url, path, on_progress, **kw)
        if done[0]:
            with open(path, "wb") as out:
                out.write(url.encode())
        return done


class Probe(BaseModule):
    name = "probe"


class Rig:
    def __init__(self, db_path, series_id, source_id, runner, world, downloader, probe, media) -> None:
        self.media = media
        self.db_path, self.series_id, self.source_id = db_path, series_id, source_id
        self.runner, self.world, self.downloader, self.probe = runner, world, downloader, probe

    def query(self, sql: str, params=()) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(each) for each in conn.execute(sql, params)]

    def execute(self, sql: str, params=()) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(sql, params)
            conn.commit()

    def tasks(self) -> dict[str, str]:
        return {each["task_key"]: each["status"] for each in self.query(
            "SELECT task_key, status FROM download_tasks WHERE series_id=?", (self.series_id,))}

    def row(self, key: str, episode: int, **fields):
        return row(self.series_id, self.source_id, key, 1, episode, **fields)

    async def run(self, **payload) -> dict:
        return await self.probe.request("scan.series.run", {"series_id": self.series_id, **payload}, timeout=STUCK_S * 2)

    async def until(self, condition, what: str) -> None:
        for _ in range(400):
            if condition():
                return
            await asyncio.sleep(0.02)
        raise AssertionError((what, self.tasks(), self.query("SELECT unique_id, status, plan_status FROM media_items")))


@pytest.fixture
async def rig(tmp_path):
    db_path = migrated_db(tmp_path / "boundary.db")
    db = Database(db_path)
    created = await create_series(db, name="Т", name_en="T", site="x", save_path=str(tmp_path / "media"), sources=[
        source(url=URL, source_type="web_video", parser_profile_id=1)])
    bus = Bus()
    world = Neighbours(bus)
    world.settings_values["max_parallel_downloads"] = "1"
    downloader = UrlDownloader()
    probe = Probe(bus)
    runner = Runner(bus, [world, CatalogModule(bus, db), ScanModule(bus, db, scheduler_tick=None, table_wait_s=STUCK_S),
                          DownloadsModule(bus, db, downloader=downloader), probe])
    await runner.start()
    world.sources = [(created.source_ids[0], URL)]
    made = Rig(db_path, created.series_id, created.source_ids[0], runner, world, downloader, probe, tmp_path / "media")
    yield made
    downloader.gate and downloader.gate.set()
    await runner.stop()


async def test_an_episode_the_user_switches_off_loses_its_waiting_download_and_the_one_under_way_goes_on(rig):
    rig.downloader.gate = asyncio.Event()
    rig.world.rows = [rig.row("a", 1, link="https://v/1"), rig.row("b", 2, link="https://v/2")]
    await rig.run()
    await rig.until(lambda: sorted(rig.tasks().values()) == ["downloading", "pending"], "a download under way and one waiting")
    waiting = next(key for key, status in rig.tasks().items() if status == "pending")
    episode = rig.query("SELECT episode_start FROM media_items WHERE unique_id=?", (waiting,))[0]["episode_start"]

    await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episode": episode, "off": True},
                            timeout=STUCK_S)
    await rig.until(lambda: waiting not in rig.tasks(), "the waiting download dropped")
    assert list(rig.tasks().values()) == ["downloading"]

    await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episode": episode, "off": False},
                            timeout=STUCK_S)
    assert rig.query("SELECT plan_status FROM media_items WHERE unique_id=?", (waiting,))[0]["plan_status"] == "in_plan_single"
    assert waiting not in rig.tasks()  # switching on only gives the place back: a download is asked for by a scan


async def test_another_video_behind_the_row_of_a_fetched_one_is_fetched_and_the_old_file_is_displaced(rig):
    rig.world.rows = [rig.row("a", 1, link="https://v/old")]
    await rig.run()
    await rig.until(lambda: rig.query("SELECT status FROM media_items")[0]["status"] == "completed", "the first video fetched")
    [old] = rig.query("SELECT unique_id, final_filename FROM media_items")
    assert rig.downloader.started == ["https://v/old"] and old["final_filename"]

    rig.world.rows = [rig.row("a", 1, link="https://v/new")]
    await rig.run()
    await rig.until(lambda: "https://v/new" in rig.downloader.started, "the new video is asked for")
    await rig.until(lambda: any(each["status"] == "completed" and each["unique_id"] != old["unique_id"]
                                for each in rig.query("SELECT unique_id, status FROM media_items")), "the new video fetched")
    rows = {each["unique_id"]: each for each in rig.query("SELECT * FROM media_items")}
    [new] = [each for uid, each in rows.items() if uid != old["unique_id"] and each["plan_status"] == "in_plan_single"]
    assert new["source_url"] == "https://v/new" and new["unique_id"] != old["unique_id"]
    # The plan holds one file for the slot: the new video, the old one displaced by it (its file is retired by downloads).
    assert rows[old["unique_id"]]["plan_status"] in ("replaced", "redundant", "candidate")


async def test_a_waiting_download_is_not_turned_to_another_video_when_the_address_of_its_row_changes(rig):
    rig.downloader.gate = asyncio.Event()
    rig.world.rows = [rig.row("a", 1, link="https://v/a"), rig.row("b", 2, link="https://v/old")]
    await rig.run()
    await rig.until(lambda: sorted(rig.tasks().values()) == ["downloading", "pending"], "one download under way, one waiting")

    rig.world.rows = [rig.row("a", 1, link="https://v/a"), rig.row("b", 2, link="https://v/new")]
    await rig.run()
    rig.downloader.gate.set()
    await rig.until(lambda: sorted(each["source_url"] for each in rig.query(
        "SELECT source_url FROM media_items WHERE status='completed' AND plan_status='in_plan_single'")) == [
            "https://v/a", "https://v/new"], "both videos of the plan fetched")

    assert "https://v/old" not in rig.downloader.started
    assert (rig.media / "Серия 02.mp4").read_bytes() == b"https://v/new"


async def test_a_download_of_a_video_the_plan_no_longer_wants_stops_and_the_new_video_takes_its_name(rig):
    rig.downloader.gate = asyncio.Event()
    rig.world.rows = [rig.row("a", 1, link="https://v/old")]
    await rig.run()
    await rig.until(lambda: list(rig.tasks().values()) == ["downloading"], "the download under way")

    rig.world.rows = [rig.row("a", 1, link="https://v/new")]
    await rig.run()
    rig.downloader.gate.set()
    await rig.until(lambda: any(each["source_url"] == "https://v/new" and each["status"] == "completed"
                                for each in rig.query("SELECT source_url, status FROM media_items")), "the new video fetched")
    rows = {each["source_url"]: each for each in rig.query("SELECT source_url, status, final_filename FROM media_items")}
    # The old video is no longer in the plan: its download was stopped, so the row is not fetched and holds no file.
    assert rows["https://v/old"]["status"] == "pending" and rows["https://v/old"]["final_filename"] is None
    assert rows["https://v/new"]["status"] == "completed"
    assert (rig.media / "Серия 01.mp4").read_bytes() == b"https://v/new"
    assert sorted(each.name for each in rig.media.iterdir()) == ["Серия 01.mp4"]


async def test_a_better_version_of_another_video_never_takes_the_row_of_the_better_version_of_the_first(rig):
    names = {}
    for link, height in [("https://v/A", 720), ("https://v/A", 1080), ("https://v/B", 720), ("https://v/B", 1080)]:
        rig.world.rows = [rig.row("k", 1, link=link, height=height)]
        await rig.run(download=False)
        [winner] = rig.query("SELECT unique_id FROM media_items WHERE plan_status='in_plan_single'")
        assert winner["unique_id"] not in names, (link, height, names)
        names[winner["unique_id"]] = f"{link[-1]}-{height}.mp4"
        rig.execute("UPDATE media_items SET status='completed', final_filename=? WHERE unique_id=?",
                    (names[winner["unique_id"]], winner["unique_id"]))
    held = {each["unique_id"]: each["final_filename"] for each in rig.query("SELECT unique_id, final_filename FROM media_items")}
    assert held == names


async def test_a_replacement_that_cannot_be_fetched_leaves_the_file_of_the_video_it_was_to_replace(rig):
    rig.world.rows = [rig.row("a", 1, link="https://v/old")]
    await rig.run()
    await rig.until(lambda: rig.query("SELECT status FROM media_items")[0]["status"] == "completed", "the first video fetched")
    [old] = rig.query("SELECT unique_id, final_filename FROM media_items")

    rig.downloader.fail_urls.add("https://v/new")
    rig.world.rows = [rig.row("a", 1, link="https://v/new")]
    await rig.run()
    await rig.until(lambda: any(each["status"] == "error" for each in rig.query("SELECT status FROM media_items")),
                    "the replacement failed")
    assert (rig.media / old["final_filename"]).read_bytes() == b"https://v/old"
    assert rig.query("SELECT final_filename FROM media_items WHERE unique_id=?", (old["unique_id"],))[0]["final_filename"]
    assert sorted(each.name for each in rig.media.iterdir()) == [old["final_filename"]]  # no leftover of the attempt


async def test_a_replacement_that_is_fetched_takes_the_place_of_the_old_file_and_the_old_row_lets_it_go(rig):
    rig.world.rows = [rig.row("a", 1, link="https://v/old")]
    await rig.run()
    await rig.until(lambda: rig.query("SELECT status FROM media_items")[0]["status"] == "completed", "the first video fetched")
    [old] = rig.query("SELECT unique_id, final_filename FROM media_items")

    rig.world.rows = [rig.row("a", 1, link="https://v/new")]
    await rig.run()
    await rig.until(lambda: any(each["source_url"] == "https://v/new" and each["status"] == "completed"
                                for each in rig.query("SELECT source_url, status FROM media_items")), "the new video fetched")
    assert (rig.media / old["final_filename"]).read_bytes() == b"https://v/new"
    assert rig.query("SELECT final_filename FROM media_items WHERE unique_id=?", (old["unique_id"],))[0]["final_filename"] is None
    assert sorted(each.name for each in rig.media.iterdir()) == [old["final_filename"]]


async def test_the_command_to_synchronize_is_kept_until_downloads_has_written_the_work_down(rig):
    rig.world.format_gate, rig.world.format_asked = asyncio.Event(), asyncio.Event()
    rig.world.rows = [rig.row("a", 1, link="https://v/1")]
    run = asyncio.create_task(rig.run())
    await asyncio.wait_for(rig.world.format_asked.wait(), STUCK_S)

    # Downloads has the work and has not recorded it: the synchronization has not ended, and its command is on the database.
    assert not run.done()
    assert [json.loads(each["results_data"]).get("video_sync") for each in rig.query("SELECT results_data FROM scan_tasks")] == [True]
    assert rig.tasks() == {}

    rig.world.format_gate.set()
    await asyncio.wait_for(run, STUCK_S * 2)
    assert list(rig.tasks()) and rig.query("SELECT id FROM scan_tasks") == []


async def test_a_synchronization_whose_work_downloads_cannot_take_ends_as_a_failure_with_its_command_kept(rig):
    rig.execute("UPDATE series SET save_path='relative/path' WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, link="https://v/1")]
    with pytest.raises(Exception):
        await rig.run()
    assert [each["status"] for each in rig.query("SELECT status FROM scan_tasks")] == ["error"]
    assert rig.tasks() == {}


async def test_an_episode_the_user_switches_off_loses_the_download_under_way_and_the_next_one_takes_its_place(rig):
    rig.downloader.gate = asyncio.Event()
    rig.world.rows = [rig.row("a", 1, link="https://v/1"), rig.row("b", 2, link="https://v/2")]
    await rig.run()
    await rig.until(lambda: sorted(rig.tasks().values()) == ["downloading", "pending"], "one download under way, one waiting")
    running = next(key for key, status in rig.tasks().items() if status == "downloading")
    other = next(key for key in rig.tasks() if key != running)
    episode = rig.query("SELECT episode_start FROM media_items WHERE unique_id=?", (running,))[0]["episode_start"]

    await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episode": episode, "off": True},
                            timeout=STUCK_S)
    await rig.until(lambda: running not in rig.tasks(), "the download of the switched off episode stopped")
    assert rig.query("SELECT status, final_filename FROM media_items WHERE unique_id=?", (running,)) == [
        {"status": "pending", "final_filename": None}]

    rig.downloader.gate.set()
    await rig.until(lambda: rig.query("SELECT status FROM media_items WHERE unique_id=?", (other,))[0]["status"] == "completed",
                    "the other episode fetched")
    assert sorted(each.name for each in rig.media.iterdir()) == [f"Серия {2 if episode == 1 else 1:02d}.mp4"]


async def test_an_episode_the_user_switches_off_loses_its_whole_file_and_the_other_files_stay(rig):
    rig.world.rows = [rig.row("a", 1, link="https://v/1"), rig.row("b", 2, link="https://v/2")]
    await rig.run()
    await rig.until(lambda: [each["status"] for each in rig.query("SELECT status FROM media_items")] == ["completed"] * 2,
                    "both episodes fetched")
    assert sorted(each.name for each in rig.media.iterdir()) == ["Серия 01.mp4", "Серия 02.mp4"]

    await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episode": 1, "off": True},
                            timeout=STUCK_S)
    await rig.until(lambda: not (rig.media / "Серия 01.mp4").exists(), "the file of the switched off episode is gone")
    assert sorted(each.name for each in rig.media.iterdir()) == ["Серия 02.mp4"]
    assert rig.query("SELECT status, final_filename FROM media_items WHERE episode_start=1") == [
        {"status": "pending", "final_filename": None}]
    assert rig.query("SELECT status FROM media_items WHERE episode_start=2") == [{"status": "completed"}]
