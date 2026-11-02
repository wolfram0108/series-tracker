"""A task of downloads is a record of a process, and what the process leaves on the disk is its record's: the video it
fetches is the video its row has when it begins, the file takes its name in a step that can be finished after a stop, and
a task that goes takes its work file with it, whatever the way it goes."""
import asyncio
import os
import sqlite3

import pytest

from core import Bus, Runner
from core.retry import Budget
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.downloads import module as downloads_module
from test_downloads_module import (FakeDownloader, FakeNeighbours, Probe, _add_item, _items, _tasks, _wait,  # noqa: F401
                                   env_paths, system)

PLAN_IN = "in_plan_single"


def _task(db_path, uid, status, save_path, *, url="u"):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, video_url, save_path, status, task_type) "
                     "VALUES (?, 2, ?, ?, ?, 'vk_video')", (uid, url, save_path, status))
        conn.commit()


def _work(media_dir, uid, name="Серия 01.mp4"):
    return os.path.join(media_dir, f".{uid}.{name}")


def _write(path, data=b"x"):
    with open(path, "wb") as out:
        out.write(data)


class Boot:
    """The modules of one start of the process over a database that outlives it."""

    def __init__(self, db_path) -> None:
        self.db_path = db_path
        self.runner = None

    async def up(self):
        bus = Bus()
        self.fake, self.dl, db = FakeNeighbours(bus), FakeDownloader(), Database(self.db_path)
        self.probe = Probe(bus)
        self.runner = Runner(bus, [self.fake, CatalogModule(bus, db), DownloadsModule(bus, db, downloader=self.dl), self.probe])
        await self.runner.start()
        return self

    async def down(self):
        await self.runner.stop()


@pytest.fixture
async def boot(env_paths):
    db_path, media_dir = env_paths
    started = Boot(db_path)
    yield started, db_path, media_dir
    if started.runner is not None:
        await started.down()


# --- the video a task fetches is the video of its row when it begins ---------------------------------------------------

@pytest.mark.asyncio
async def test_a_waiting_task_fetches_the_address_its_row_has_when_it_begins_and_marks_the_row_in_the_same_step(system):
    """The scan may write a new address into a row while the task that was made for the old one waits."""
    _, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1, source_url="https://v/new")
    _task(db_path, "uid1", "pending", os.path.join(media_dir, "Серия 01.mp4"), url="https://v/old")
    dl.gate = asyncio.Event()

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: bool(dl.started))
    assert dl.started == ["https://v/new"]
    assert _items(db_path)["uid1"]["status"] == "downloading"  # the guard of the scan holds the address from here
    assert [each["video_url"] for each in _tasks(db_path)] == ["https://v/new"]
    dl.gate.set()
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")


# --- the file takes its name in a step that is finished after a stop ---------------------------------------------------

def _two_videos_for_one_name(db_path, media_dir):
    """The video of the row `old` lies under the name; the plan wants the row `new` to take it."""
    final = os.path.join(media_dir, "Серия 01.mp4")
    _write(final, b"old")
    _add_item(db_path, "old", 1, status="completed", filename="Серия 01.mp4", plan="replaced", superseded_by="new")
    _add_item(db_path, "new", 1, source_url="https://vk.com/video-1_77")
    return final


@pytest.mark.asyncio
async def test_a_failure_of_the_database_after_the_file_took_its_name_leaves_a_task_that_is_finished_not_an_error(system, monkeypatch):
    _, _, dl, probe, db_path, media_dir = system
    monkeypatch.setattr(downloads_module, "PUBLISH_BUDGET", Budget(attempts=2, pauses=(0.0,), limit_s=5.0))
    final = _two_videos_for_one_name(db_path, media_dir)
    broken = {"on": True}
    real = Database.execute_many

    async def execute_many(self, statements):
        statements = list(statements)
        if broken["on"] and any("status='completed'" in query for query, _ in statements):
            raise sqlite3.OperationalError("disk I/O error")
        return await real(self, statements)

    monkeypatch.setattr(Database, "execute_many", execute_many)

    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: open(final, "rb").read() == b"video")  # the file took its name
    assert await _wait(lambda: [each["status"] for each in _tasks(db_path)] == ["publishing"])
    assert _items(db_path)["new"]["status"] != "error"

    broken["on"] = False
    probe.publish_event("downloads.dispatch", {"series_id": 2})
    assert await _wait(lambda: _items(db_path)["new"]["status"] == "completed")
    items = _items(db_path)
    assert items["new"]["final_filename"] == "Серия 01.mp4"
    assert items["old"]["final_filename"] is None  # a row does not hold a name whose file is another video
    assert _tasks(db_path) == []
    assert open(final, "rb").read() == b"video"


@pytest.mark.asyncio
async def test_a_stop_after_the_file_took_its_name_is_finished_when_the_process_starts(boot):
    started, db_path, media_dir = boot
    final = _two_videos_for_one_name(db_path, media_dir)
    _write(final, b"new")  # the replacement was done, the database was not written
    _task(db_path, "new", "publishing", final, url="https://vk.com/video-1_77")

    await started.up()

    assert await _wait(lambda: _items(db_path)["new"]["status"] == "completed")
    items = _items(db_path)
    assert items["new"]["final_filename"] == "Серия 01.mp4" and items["old"]["final_filename"] is None
    assert _tasks(db_path) == [] and started.dl.started == []  # nothing was fetched again
    assert open(final, "rb").read() == b"new"


@pytest.mark.asyncio
async def test_a_stop_before_the_file_took_its_name_is_finished_when_the_process_starts(boot):
    started, db_path, media_dir = boot
    final = _two_videos_for_one_name(db_path, media_dir)
    _write(_work(media_dir, "new"), b"new")  # whole, under the name of the work
    _task(db_path, "new", "publishing", final, url="https://vk.com/video-1_77")

    await started.up()

    assert await _wait(lambda: _items(db_path)["new"]["status"] == "completed")
    assert open(final, "rb").read() == b"new" and not os.path.exists(_work(media_dir, "new"))
    assert _items(db_path)["old"]["final_filename"] is None and _tasks(db_path) == []


@pytest.mark.asyncio
async def test_a_publication_with_no_file_anywhere_is_fetched_again(boot):
    started, db_path, media_dir = boot
    final = os.path.join(media_dir, "Серия 01.mp4")
    _add_item(db_path, "new", 1, source_url="https://vk.com/video-1_77")
    _task(db_path, "new", "publishing", final, url="https://vk.com/video-1_77")

    await started.up()

    assert await _wait(lambda: _items(db_path)["new"]["status"] == "completed")
    assert started.dl.started == ["https://vk.com/video-1_77"]


# --- a task that goes takes its work file with it -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_task_that_waited_after_a_stop_goes_with_its_work_file_when_its_row_leaves_the_plan(system):
    """The work file of a download that was whole before the stop belongs to the task that is put back in the queue."""
    _, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1, plan="candidate")
    work = _work(media_dir, "uid1")
    _write(work)
    _task(db_path, "uid1", "pending", os.path.join(media_dir, "Серия 01.mp4"))

    probe.publish_event("scan.plan.updated", {"series_id": 2})

    assert await _wait(lambda: _tasks(db_path) == [])
    assert not os.path.exists(work)


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["pending", "error"])
async def test_the_queue_that_is_cleared_loses_the_work_files_of_the_tasks_it_loses(system, state):
    _, _, _, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1, plan=PLAN_IN)
    work = _work(media_dir, "uid1")
    _write(work)
    _task(db_path, "uid1", state, os.path.join(media_dir, "Серия 01.mp4"))

    reply = await probe.request("downloads.queue.clear", {}, timeout=10)

    assert reply["deleted"] == 1 and _tasks(db_path) == []
    assert not os.path.exists(work)


@pytest.mark.asyncio
async def test_a_failed_task_is_replaced_by_a_scan_and_its_work_file_goes_with_it(system):
    _, _, dl, probe, db_path, media_dir = system
    _add_item(db_path, "uid1", 1, status="error")
    work = _work(media_dir, "uid1")
    _write(work, b"stale")
    _task(db_path, "uid1", "error", os.path.join(media_dir, "Серия 01.mp4"))
    dl.gate = asyncio.Event()

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: bool(dl.started))
    assert not os.path.exists(work)  # what the failed run left is not taken for the start of the next one
    dl.gate.set()
    assert await _wait(lambda: _items(db_path)["uid1"]["status"] == "completed")


@pytest.mark.asyncio
async def test_a_work_file_no_task_owns_is_removed_when_the_process_starts_and_nothing_else_is(boot):
    started, db_path, media_dir = boot
    _add_item(db_path, "uid1", 1, status="completed", filename="Серия 01.mp4")
    _add_item(db_path, "uid2", 2, plan=PLAN_IN)
    _write(os.path.join(media_dir, "Серия 01.mp4"))
    orphan = _work(media_dir, "uid1")
    _write(orphan)
    owned = _work(media_dir, "uid2", "Серия 02.mp4")
    _write(owned)
    _task(db_path, "uid2", "error", os.path.join(media_dir, "Серия 02.mp4"))
    strangers = [os.path.join(media_dir, ".hidden"), os.path.join(media_dir, ".nobody.Серия 03.mp4")]
    for path in strangers:
        _write(path)

    await started.up()

    assert await _wait(lambda: not os.path.exists(orphan))
    assert os.path.exists(owned) and os.path.exists(os.path.join(media_dir, "Серия 01.mp4"))
    assert all(os.path.exists(path) for path in strangers)  # a file that is not a work file of a row of the series is not touched
