"""Who owns a path in the library, and what a file does on its way to another name: series that share a folder never hold one
file between them, what a task could not remove stays recorded until it is removed, and the files of a ring of names (a swap
of two episode numbers) reach the names their numbers give, without losing a file or taking one that is not theirs."""
import itertools
import os
import sqlite3

import pytest

from core import storage
from core.envelope import Envelope
from test_downloads_module import (FakeDownloader, FakeNeighbours, Probe, _add_item, _items, _tasks, _wait, _watch,  # noqa: F401
                                   env_paths, system)

PLAN_IN = "in_plan_single"


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as out:
        out.write(data)


def _read(path):
    with open(path, "rb") as source:
        return source.read()


def _rows(db_path, series_id):
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return {row["unique_id"]: dict(row) for row in conn.execute("SELECT * FROM media_items WHERE series_id=?", (series_id,))}


def _task(db_path, uid, status, save_path, *, series_id=2, work_path=None, url="u"):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO download_tasks (task_key, series_id, video_url, save_path, work_path, status, task_type) "
                     "VALUES (?, ?, ?, ?, ?, ?, 'vk_video')", (uid, series_id, url, save_path, work_path, status))
        conn.commit()


def _held_by_another_series(db_path, media_dir, name="Серия 01.mp4", data=b"first"):
    """Series 3 holds the file; series 2 and 3 share the folder."""
    _write(os.path.join(media_dir, name), data)
    _add_item(db_path, "other", 1, status="completed", filename=name, series_id=3)


# --- one file, one holder, across the series that share a folder ------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_file_that_a_row_of_another_series_holds_is_not_adopted(system):
    _, _, _, probe, db_path, media_dir = system
    _held_by_another_series(db_path, media_dir)
    _add_item(db_path, "mine", 1, series_id=2)

    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)

    assert _rows(db_path, 2)["mine"]["status"] == "pending" and _rows(db_path, 2)["mine"]["final_filename"] is None
    assert _rows(db_path, 3)["other"]["final_filename"] == "Серия 01.mp4"
    assert _read(os.path.join(media_dir, "Серия 01.mp4")) == b"first"


@pytest.mark.asyncio
async def test_a_download_waits_for_a_name_that_a_row_of_another_series_holds_and_never_replaces_its_file(system):
    _, _, dl, probe, db_path, media_dir = system
    _held_by_another_series(db_path, media_dir)
    _add_item(db_path, "mine", 1, series_id=2, source_url="https://vk.com/video-1_77")

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: [each["status"] for each in _tasks(db_path)] == ["publishing"])
    assert dl.started == ["https://vk.com/video-1_77"]
    assert _read(os.path.join(media_dir, "Серия 01.mp4")) == b"first"  # the file of the other series is whole
    assert _rows(db_path, 2)["mine"]["final_filename"] is None and _rows(db_path, 3)["other"]["status"] == "completed"


@pytest.mark.asyncio
async def test_the_file_a_row_of_another_series_holds_is_not_removed_when_a_row_that_shares_the_name_is_switched_off(system):
    _, _, _, probe, db_path, media_dir = system
    _held_by_another_series(db_path, media_dir)
    _add_item(db_path, "mine", 1, series_id=2, status="completed", filename="Серия 01.mp4", ignored=1)

    await probe.request("downloads.settle", {"series_id": 2}, timeout=5)  # a sync brings the disk to the plan (И-59)

    assert await _wait(lambda: _rows(db_path, 2)["mine"]["final_filename"] is None)
    assert os.path.isfile(os.path.join(media_dir, "Серия 01.mp4")) and _rows(db_path, 3)["other"]["final_filename"] == "Серия 01.mp4"


# --- what a task could not remove is not forgotten --------------------------------------------------------------------------

@pytest.fixture
def stubborn(monkeypatch):
    """Removal of the files named in `refused` fails, as a store that denies it does."""
    refused: set[str] = set()
    real = storage.remove_file

    def remove(path, *args, **kwargs):
        return False if path in refused else real(path, *args, **kwargs)

    monkeypatch.setattr(storage, "remove_file", remove)
    return refused


@pytest.mark.asyncio
async def test_a_series_deleted_while_its_work_file_cannot_be_removed_leaves_the_task_until_it_can(system, stubborn):
    _, _, _, probe, db_path, media_dir = system
    work = os.path.join(media_dir, ".mine.Серия 01.mp4")
    _write(work, b"whole")
    _add_item(db_path, "mine", 1, series_id=2, plan="candidate")
    _task(db_path, "mine", "error", os.path.join(media_dir, "Серия 01.mp4"), work_path=work)
    stubborn.add(work)

    probe.publish_event("series.deleted", {"series_id": 2})
    await probe.request("downloads.queue.get", {}, timeout=10)

    assert [each["status"] for each in _tasks(db_path)] == ["error"] and os.path.isfile(work)  # the record of what is left
    stubborn.clear()
    reply = await probe.request("downloads.queue.clear", {}, timeout=10)
    assert reply["deleted"] == 1 and _tasks(db_path) == [] and not os.path.exists(work)


@pytest.mark.asyncio
async def test_pieces_left_beside_a_published_video_keep_the_task_until_they_are_removed(system, stubborn):
    _, _, _, probe, db_path, media_dir = system
    final = os.path.join(media_dir, "Серия 01.mp4")
    piece = final + ".part"
    work = os.path.join(media_dir, ".mine.mp4")
    _write(piece)
    _write(work, b"video")
    _add_item(db_path, "mine", 1, series_id=2, source_url="https://vk.com/video-1_77")
    _task(db_path, "mine", "publishing", final, work_path=work, url="https://vk.com/video-1_77")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE download_tasks SET work_size=?, work_ino=? WHERE task_key='mine'", (os.stat(work).st_size, os.stat(work).st_ino))
        conn.commit()
    stubborn.add(piece)

    probe.publish_event("downloads.dispatch", {"series_id": 2})

    assert await _wait(lambda: _rows(db_path, 2)["mine"]["status"] == "completed")
    assert [each["status"] for each in _tasks(db_path)] == ["error"] and os.path.isfile(piece)  # the video is ready, the leftover is recorded
    stubborn.clear()
    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=10)
    assert await _wait(lambda: _tasks(db_path) == [] and not os.path.exists(piece))
    assert _read(final) == b"video"


# --- the files of a ring of names ---------------------------------------------------------------------------------------------

def _ready(db_path, media_dir, episode, name, data):
    _write(os.path.join(media_dir, name), data)
    _add_item(db_path, f"u{episode}", episode, status="completed", filename=name, series_id=2)


async def _rename(probe, names):
    return await probe.request("downloads.items.rename", {"series_id": 2, "names": names}, timeout=20)


@pytest.mark.asyncio
async def test_two_files_that_swap_names_swap_places_and_each_row_holds_the_file_that_is_its_own(system):
    _, _, _, probe, db_path, media_dir = system
    _ready(db_path, media_dir, 1, "Серия 01.mp4", b"first")
    _ready(db_path, media_dir, 2, "Серия 02.mp4", b"second")
    renamed = probe.bus.subscribe("downloads.item.renamed")

    reply = await _rename(probe, {"u1": "Серия 02.mp4", "u2": "Серия 01.mp4"})

    assert reply == {"renamed": 2}
    rows = _rows(db_path, 2)
    assert rows["u1"]["final_filename"] == "Серия 02.mp4" and _read(os.path.join(media_dir, "Серия 02.mp4")) == b"first"
    assert rows["u2"]["final_filename"] == "Серия 01.mp4" and _read(os.path.join(media_dir, "Серия 01.mp4")) == b"second"
    assert rows["u1"]["rename_to"] is None and rows["u2"]["rename_to"] is None
    assert sorted(os.listdir(media_dir)) == ["Серия 01.mp4", "Серия 02.mp4"]  # nothing is left aside
    told = {}
    while not renamed.queue.empty():
        event = renamed.queue.get_nowait().payload
        told[event["unique_id"]] = event["filename"]
    assert told == {"u1": "Серия 02.mp4", "u2": "Серия 01.mp4"}  # the new names are told after the files lie under them


@pytest.mark.asyncio
async def test_three_files_that_pass_each_others_names_in_a_ring_all_arrive(system):
    _, _, _, probe, db_path, media_dir = system
    for episode, data in ((1, b"a"), (2, b"b"), (3, b"c")):
        _ready(db_path, media_dir, episode, f"Серия {episode:02d}.mp4", data)

    reply = await _rename(probe, {"u1": "Серия 02.mp4", "u2": "Серия 03.mp4", "u3": "Серия 01.mp4"})

    assert reply == {"renamed": 3}
    assert [_read(os.path.join(media_dir, f"Серия {number:02d}.mp4")) for number in (1, 2, 3)] == [b"c", b"a", b"b"]
    assert {key: row["final_filename"] for key, row in _rows(db_path, 2).items()} == {
        "u1": "Серия 02.mp4", "u2": "Серия 03.mp4", "u3": "Серия 01.mp4"}


@pytest.mark.asyncio
async def test_files_reach_the_names_of_any_placing_from_any_other_and_none_is_lost(system):
    """Three files and four names, one of them free: every way to give each file its own name, one after another, so that every
    placing follows every other in some order (swaps, rings, shifts along a chain into the free name): each file holds the
    content it held and lies under the name it was given, and nothing is left aside."""
    _, _, _, probe, db_path, media_dir = system
    names = [f"Серия {number:02d}.mp4" for number in (1, 2, 3, 4)]
    contents = {"u1": b"a", "u2": b"b", "u3": b"c"}
    for episode, name in zip((1, 2, 3), names):
        _ready(db_path, media_dir, episode, name, contents[f"u{episode}"])
    placings = list(itertools.permutations(names, 3))
    for placing in placings + placings[::-1]:
        target = dict(zip(("u1", "u2", "u3"), placing))
        await _rename(probe, target)
        assert {key: row["final_filename"] for key, row in _rows(db_path, 2).items()} == target
        assert all(row["rename_to"] is None for row in _rows(db_path, 2).values())
        assert {name: _read(os.path.join(media_dir, name)) for name in os.listdir(media_dir)} == {
            target[key]: data for key, data in contents.items()}


@pytest.mark.asyncio
async def test_a_name_that_a_file_of_nobody_holds_is_not_taken_and_the_file_keeps_its_own(system):
    _, _, _, probe, db_path, media_dir = system
    _ready(db_path, media_dir, 1, "Серия 01.mp4", b"first")
    _write(os.path.join(media_dir, "Серия 05.mp4"), b"stranger")

    reply = await _rename(probe, {"u1": "Серия 05.mp4"})

    assert reply == {"renamed": 0}
    row = _rows(db_path, 2)["u1"]
    assert row["final_filename"] == "Серия 01.mp4" and row["rename_to"] is None
    assert _read(os.path.join(media_dir, "Серия 01.mp4")) == b"first" and _read(os.path.join(media_dir, "Серия 05.mp4")) == b"stranger"


@pytest.mark.asyncio
async def test_a_name_that_a_row_of_another_series_holds_is_not_taken(system):
    _, _, _, probe, db_path, media_dir = system
    _held_by_another_series(db_path, media_dir, name="Серия 05.mp4", data=b"theirs")
    _ready(db_path, media_dir, 1, "Серия 01.mp4", b"mine")

    reply = await _rename(probe, {"u1": "Серия 05.mp4"})

    assert reply == {"renamed": 0}
    assert _rows(db_path, 2)["u1"]["final_filename"] == "Серия 01.mp4" and _read(os.path.join(media_dir, "Серия 05.mp4")) == b"theirs"


@pytest.mark.asyncio
async def test_a_wish_the_plan_withdrew_is_cancelled_by_naming_the_file_as_it_is(system):
    _, _, _, probe, db_path, media_dir = system
    _ready(db_path, media_dir, 1, "Серия 01.mp4", b"first")
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET rename_to='Серия 09.mp4' WHERE unique_id='u1'")
        conn.commit()

    reply = await _rename(probe, {"u1": "Серия 01.mp4"})

    assert reply == {"renamed": 0} and _rows(db_path, 2)["u1"]["rename_to"] is None and os.listdir(media_dir) == ["Серия 01.mp4"]


@pytest.mark.asyncio
async def test_a_file_that_a_stop_left_aside_in_a_ring_is_carried_on_when_the_series_is_looked_at(system):
    """The obligation is in the database, the file is under the name of a work file of the ring: the disk tells what is left."""
    _, _, _, probe, db_path, media_dir = system
    _ready(db_path, media_dir, 2, "Серия 02.mp4", b"second")
    _write(os.path.join(media_dir, ".renaming.u1"), b"first")  # u1 stepped aside before the stop; u2 has not moved yet
    _add_item(db_path, "u1", 1, status="completed", filename="Серия 01.mp4", series_id=2)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET rename_to='Серия 02.mp4' WHERE unique_id='u1'")
        conn.execute("UPDATE media_items SET rename_to='Серия 01.mp4' WHERE unique_id='u2'")
        conn.commit()

    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=20)

    assert _read(os.path.join(media_dir, "Серия 01.mp4")) == b"second" and _read(os.path.join(media_dir, "Серия 02.mp4")) == b"first"
    assert sorted(os.listdir(media_dir)) == ["Серия 01.mp4", "Серия 02.mp4"]
    assert {key: (row["final_filename"], row["rename_to"]) for key, row in _rows(db_path, 2).items()} == {
        "u1": ("Серия 02.mp4", None), "u2": ("Серия 01.mp4", None)}


@pytest.mark.asyncio
async def test_a_file_that_already_lies_under_its_new_name_has_the_name_recorded(system):
    _, _, _, probe, db_path, media_dir = system
    _write(os.path.join(media_dir, "Серия 02.mp4"), b"first")  # moved before the stop, the record was not written
    _add_item(db_path, "u1", 1, status="completed", filename="Серия 01.mp4", series_id=2)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE media_items SET rename_to='Серия 02.mp4' WHERE unique_id='u1'")
        conn.commit()

    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=20)

    row = _rows(db_path, 2)["u1"]
    assert (row["final_filename"], row["rename_to"]) == ("Серия 02.mp4", None) and os.listdir(media_dir) == ["Серия 02.mp4"]
