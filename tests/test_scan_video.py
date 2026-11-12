"""Scan on a series of kind `video`: the synchronization by signals, its command, its run and the switch of an output.

Catalog and scan are real, the other neighbours are the fake `World` of tests/scan_support.py. What a test waits for is
a token or an event that a neighbour puts out, never time; the one number (STUCK_S) only ends a test that is stuck, and
the one test of a safeguard sets its own.
"""
import asyncio
import json
import sqlite3
from dataclasses import dataclass

import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from core.contracts import RUN_CHANGED, TableState
from core.db import Database
from core.envelope import Envelope
from core.reasons import parse
from modules.catalog import CatalogModule
from modules.scan import ScanModule
from scan_support import STUCK_S, Page, World, migrated_db
from series_factory import create_series, source
from settle import wait_until

URL1 = "https://fake.test/p/1"
URL2 = "https://fake.test/p/2"


class Probe(BaseModule):
    name = "probe"


@dataclass
class Rig:
    bus: Bus
    world: World
    scan: ScanModule
    probe: Probe
    runner: Runner
    db_path: str
    series_id: int
    source_ids: tuple[int, ...]

    async def run(self, **payload) -> dict:
        return await self.probe.request("scan.series.run", {"series_id": self.series_id, **payload}, timeout=STUCK_S * 2)

    def start_run(self, **payload) -> asyncio.Task:
        return asyncio.create_task(self.run(**payload))

    def query(self, sql: str, params=()) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(each) for each in conn.execute(sql, params)]


    def sql(self, statement: str, params=()) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(statement, params)
            conn.commit()


def drain(sub) -> list[dict]:
    found = []
    while not sub.queue.empty():
        found.append(sub.queue.get_nowait().payload)
    return found


async def next_event(sub) -> dict:
    return (await asyncio.wait_for(sub.queue.get(), STUCK_S)).payload


@pytest.fixture
def db_path(tmp_path):
    return migrated_db(tmp_path / "scan.db")


async def build(db_path: str, urls=(URL1,), *, table_wait_s: float = STUCK_S, profile: int | None = 1,
                series_id: int | None = None, source_ids: tuple[int, ...] = ()) -> Rig:
    db = Database(db_path)
    if series_id is None:
        created = await create_series(db, name="Т", name_en="T", site="x", save_path="/media/t", sources=[
            source(url=url, source_type="web_video", parser_profile_id=profile) for url in urls])
        series_id, source_ids = created.series_id, created.source_ids
    bus = Bus()
    world = World(bus)
    scan = ScanModule(bus, db, scheduler_tick=None, table_wait_s=table_wait_s)
    probe = Probe(bus)
    runner = Runner(bus, [world, CatalogModule(bus, db), scan, probe])
    await runner.start()
    return Rig(bus, world, scan, probe, runner, db_path, series_id, source_ids)


@pytest.fixture
async def rig(db_path):
    made = await build(db_path)
    yield made
    await made.runner.stop()


@pytest.fixture
async def duo(db_path):
    made = await build(db_path, (URL1, URL2))
    yield made
    await made.runner.stop()


# --- the steps are tied by signals (M-24 without a network) ----------------------------------------------------

async def test_the_steps_of_a_synchronization_follow_one_another_by_signals(rig):
    await rig.run()
    await rig.world.dispatched.get()
    # The planner is the last step of the pass; its work is asked once it has the series.
    assert rig.world.order == [
        f"library.relocation.apply {rig.series_id}", f"sources.table.get {URL1}", "composition.recompute",
        f"planner.sync {rig.series_id}", f"planner.work.get {rig.series_id}"]


async def test_a_synchronization_without_download_brings_the_disk_to_the_plan_and_fetches_nothing(rig):
    """The disk follows the plan in a sync alone (И-59): the planner is told without download, so it applies what needs
    no work and fetches nothing."""
    await rig.run(download=False)
    await rig.world.dispatched.get()
    order = rig.world.order
    assert order[order.index("composition.recompute"):] == [
        "composition.recompute", f"planner.sync {rig.series_id}", f"planner.work.get {rig.series_id}"]
    assert rig.world.payloads["planner.sync"] == [{"series_id": rig.series_id, "download": False}]


async def test_a_synchronization_waits_for_the_verdict_of_a_page_that_is_being_read(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    run = rig.start_run()
    assert await rig.world.asked.get() == URL1
    rig.world.finish(URL1)
    result = await asyncio.wait_for(run, STUCK_S)
    order = rig.world.order
    assert order.index(f"table.ready {URL1}") < order.index("composition.recompute")
    assert result["sources"] == [{"source_id": rig.source_ids[0], "url": URL1, "state": "ready", "reason": None}]


async def test_the_stop_of_a_series_stops_a_read_of_its_page_that_no_synchronization_waits_for(rig):
    """A read taken up again at the start of the process belongs to no pass: «Остановить» of the series stops it too."""
    rig.world.pages[URL1] = Page(TableState.READING, held=True)
    stopped = await rig.probe.request("scan.run.cancel", {"series_id": rig.series_id}, timeout=STUCK_S)
    assert stopped == {"cancelled": True} and f"table.cancel {URL1}" in rig.world.order
    again = await rig.probe.request("scan.run.cancel", {"series_id": rig.series_id}, timeout=STUCK_S)
    assert again == {"cancelled": False}


async def test_a_synchronization_stopped_while_its_page_is_read_stops_the_read_and_ends_with_nothing_done(rig):
    """The user stops the sync while the pass waits for the read of its page: the pass stops, the read of the page is
    stopped too, the planner is told to stop, the run closes, and no command is left to take the sync up again."""
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    runs = rig.bus.subscribe(RUN_CHANGED)
    run = rig.start_run()
    await rig.world.asked.get()
    stopped = await rig.probe.request("scan.run.cancel", {"series_id": rig.series_id}, timeout=STUCK_S)
    assert stopped == {"cancelled": True}
    assert (await asyncio.wait_for(run, STUCK_S))["cancelled"] is True
    order = rig.world.order
    assert f"table.cancel {URL1}" in order and f"planner.sync.cancel {rig.series_id}" in order
    assert "composition.recompute" not in order and not any(step.startswith("planner.sync ") for step in order)
    assert tasks_of(rig) == []
    assert [each["active"] for each in drain(runs)] == [True, False]
    again = await rig.probe.request("scan.run.cancel", {"series_id": rig.series_id}, timeout=STUCK_S)
    assert again == {"cancelled": False}  # nothing goes any more


# --- the command of a synchronization is the database's (T-28) ---------------------------------------------------------

def tasks_of(rig: "Rig") -> list[dict]:
    return rig.query("SELECT status, results_data FROM scan_tasks WHERE series_id=?", (rig.series_id,))


async def no_task_left(rig: "Rig") -> None:
    """The command is gone from the database: it goes when the scan ends, a moment after the last signal it sent."""
    assert await wait_until(lambda: not tasks_of(rig), rig.bus, rig.scan), ("the command stayed", tasks_of(rig))


async def test_the_command_of_a_synchronization_is_written_before_its_table_is_waited_for_and_gone_when_it_ends(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    run = rig.start_run(download=False)
    await rig.world.asked.get()
    assert [(each["status"], json.loads(each["results_data"])) for each in tasks_of(rig)] == [
        ("processing", {"video_sync": True, "download": False, "snapshot": {"profiles": {}, "voices": {}}})]
    rig.world.finish(URL1)
    await asyncio.wait_for(run, STUCK_S)
    assert tasks_of(rig) == []


async def test_a_synchronization_that_fails_leaves_its_command_as_the_error_of_the_series_and_one_record_only(rig):
    rig.world.recompute_error = "boom"
    with pytest.raises(BusRequestError):
        await rig.run()
    assert [(each["status"], json.loads(each["results_data"]).get("video_sync")) for each in tasks_of(rig)] == [("error", True)]
    rig.world.recompute_error = None
    await rig.run()
    assert tasks_of(rig) == []


async def test_a_restart_in_the_middle_of_a_synchronization_runs_it_again_from_its_command_when_the_system_has_started(rig):
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        assert again.world.order == []  # nothing is asked while the modules start
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        assert await asyncio.wait_for(again.world.dispatched.get(), STUCK_S)
        assert f"planner.sync {again.series_id}" in again.world.order
        await no_task_left(again)
    finally:
        await again.runner.stop()


async def test_a_command_found_at_a_restart_without_the_wish_to_download_does_not_download(rig):
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": False})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        await no_task_left(again)
        assert [each["download"] for each in again.world.payloads["planner.sync"]] == [False]
    finally:
        await again.runner.stop()


async def test_a_command_found_at_a_restart_for_a_series_already_being_scanned_is_dropped(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    run = rig.start_run()
    await rig.world.asked.get()
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True})))
        conn.commit()
    dropped = asyncio.Event()
    delete = rig.scan.repo.delete_task

    async def delete_and_tell(task_id: int) -> None:
        await delete(task_id)
        dropped.set()  # the restarted command is deleted by a task of scan that no bus step follows

    rig.scan.repo.delete_task = delete_and_tell
    rig.bus.publish(Envelope(topic="system.started", kind="event"))
    await asyncio.wait_for(dropped.wait(), STUCK_S)
    assert len(tasks_of(rig)) == 1
    rig.world.finish(URL1)
    await asyncio.wait_for(run, STUCK_S)
    assert tasks_of(rig) == []


async def test_a_verdict_that_beats_the_reply_to_the_request_is_not_lost(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False, event_first=True)
    result = await rig.run()
    assert result["sources"][0]["state"] == "ready"


async def test_a_page_read_within_its_term_is_not_waited_for(rig):
    assert (await rig.run())["sources"][0]["state"] == "ready"
    assert rig.world.order.count(f"sources.table.get {URL1}") == 1


async def test_every_source_is_waited_for_at_once(duo):
    for url in (URL1, URL2):
        duo.world.pages[url] = Page(TableState.READING, held=False)
    run = duo.start_run()
    assert {await duo.world.asked.get(), await duo.world.asked.get()} == {URL1, URL2}
    duo.world.finish(URL2)
    duo.world.finish(URL1)
    result = await asyncio.wait_for(run, STUCK_S)
    assert [each["state"] for each in result["sources"]] == ["ready", "ready"]


async def test_a_failed_read_with_an_earlier_table_goes_on_with_that_table_and_says_why(rig):
    """S-10: the last complete table stays in force; the reason is in the result of the synchronization."""
    rig.world.pages[URL1] = Page(TableState.READING, held=True)
    run = rig.start_run()
    await rig.world.asked.get()
    why = json.dumps({"code": "page_unreachable"})
    rig.world.fail(URL1, why)
    result = await asyncio.wait_for(run, STUCK_S)
    assert result["sources"] == [{"source_id": rig.source_ids[0], "url": URL1, "state": "stale", "reason": why}]
    assert "composition.recompute" in rig.world.order


async def test_a_failure_answered_at_once_is_taken_as_the_verdict_and_is_not_waited_for(rig):
    rig.world.pages[URL1] = Page(TableState.FAILED, held=True, why='{"code":"site_not_found"}')
    result = await rig.run()
    assert result["sources"][0]["state"] == "stale" and parse(result["sources"][0]["reason"])["code"] == "site_not_found"


async def test_a_source_with_no_table_at_all_is_skipped_and_the_others_go_on(duo):
    duo.world.pages[URL1] = Page(TableState.READING, held=False)
    run = duo.start_run()
    await duo.world.asked.get()
    await duo.world.asked.get()
    why = json.dumps({"code": "page_unreachable"})
    duo.world.fail(URL1, why)
    result = await asyncio.wait_for(run, STUCK_S)
    assert [(each["state"], each["reason"]) for each in result["sources"]] == [("skipped", why), ("ready", None)]
    assert duo.world.payloads["planner.sync"] == [{"series_id": duo.series_id, "download": True}]


async def test_the_safeguard_of_the_wait_fails_the_source_and_never_counts_as_ready(db_path):
    made = await build(db_path, table_wait_s=0.2)
    try:
        made.world.pages[URL1] = Page(TableState.READING, held=True)
        result = await made.run()
        source = result["sources"][0]
        assert source["state"] == "stale"
        assert parse(source["reason"]) == {"code": "table_wait_timeout", "params": {"url": URL1}}
    finally:
        await made.runner.stop()


async def test_a_page_that_never_answered_and_has_no_table_is_skipped_by_the_safeguard(db_path):
    made = await build(db_path, table_wait_s=0.2)
    try:
        made.world.pages[URL1] = Page(TableState.READING, held=False)
        result = await made.run()
        assert result["sources"][0]["state"] == "skipped"
        assert parse(result["sources"][0]["reason"])["code"] == "table_wait_timeout"
    finally:
        await made.runner.stop()


# --- a failed step is the signal of failure, seen in the state of the scan --------------------------------------

async def test_a_failed_step_fails_the_scan_with_its_reason_and_the_next_scan_clears_it(rig):
    rig.world.recompute_error = rig.world.failure()
    with pytest.raises(BusRequestError) as failed:
        await rig.run()
    assert parse(str(failed.value).split(": ", 1)[1])["code"] == "scan_step_failed"
    carrier = rig.query("SELECT status, results_data FROM scan_tasks WHERE series_id=?", (rig.series_id,))
    assert [each["status"] for each in carrier] == ["error"]
    assert "scan_step_failed" in carrier[0]["results_data"]
    assert not any(each.startswith("planner.") for each in rig.world.order)
    rig.world.recompute_error = None
    await rig.run()
    assert rig.query("SELECT * FROM scan_tasks WHERE series_id=?", (rig.series_id,)) == []


async def test_the_folder_a_save_planned_is_moved_first_and_the_sync_works_in_it(rig):
    """The disk changes in a sync alone (И-59): the move a save planned is its first step."""
    rig.world.planned_path = "/media/moved"
    await rig.run(download=False)
    assert rig.world.order[0] == f"library.relocation.apply {rig.series_id}"
    assert rig.query("SELECT save_path FROM series WHERE id=?", (rig.series_id,)) == [{"save_path": "/media/moved"}]


async def test_a_move_that_fails_fails_the_sync_before_anything_else_is_done(rig):
    rig.world.move_error = "перемещение между разными дисками не поддерживается"
    with pytest.raises(BusRequestError, match="разными дисками"):
        await rig.run()
    assert rig.world.order == [f"library.relocation.apply {rig.series_id}"]
    carrier = rig.query("SELECT status FROM scan_tasks WHERE series_id=?", (rig.series_id,))
    assert [each["status"] for each in carrier] == ["error"]


async def test_a_series_whose_sources_have_no_profile_is_refused(db_path):
    made = await build(db_path, profile=None)
    try:
        with pytest.raises(BusRequestError) as failed:
            await made.run()
        assert parse(str(failed.value).split(": ", 1)[1])["code"] == "scan_no_profile"
        assert made.world.order == []
    finally:
        await made.runner.stop()


async def test_a_scan_of_a_series_that_is_being_scanned_is_refused(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    run = rig.start_run()
    await rig.world.asked.get()
    with pytest.raises(BusRequestError, match="not_allowed"):
        await rig.run()
    rig.world.finish(URL1)
    await asyncio.wait_for(run, STUCK_S)


async def test_without_a_download_the_planner_is_told_not_to_download(rig):
    """Without download the planner is told so and fetches nothing; with download the sync asks for it."""
    await rig.run(download=False)
    assert rig.world.payloads["planner.sync"] == [{"series_id": rig.series_id, "download": False}]
    await rig.run()
    assert rig.world.payloads["planner.sync"][1] == {"series_id": rig.series_id, "download": True}


async def test_the_scheduled_pass_over_all_series_hands_the_sync_to_the_planner(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    await rig.probe.request("scan.all.start", {}, timeout=STUCK_S)
    assert await asyncio.wait_for(rig.world.dispatched.get(), STUCK_S) == "planner.sync"
    assert rig.world.payloads["planner.sync"] == [{"series_id": rig.series_id, "download": True}]


async def test_a_restart_runs_again_a_scan_whose_command_carries_a_choice_of_an_earlier_version(rig):
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True, "inventory": True})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        assert await asyncio.wait_for(again.world.dispatched.get(), STUCK_S)
        await no_task_left(again)
    finally:
        await again.runner.stop()


async def test_a_pass_over_all_series_that_the_stop_of_the_module_cancels_asks_nobody_on_its_way_out(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    planner = rig.bus.subscribe("planner.sync")  # the pass is under way when the module is stopped
    await rig.probe.request("scan.all.start", {}, timeout=STUCK_S)
    await next_event(planner)
    spy = rig.bus.subscribe("#")
    await asyncio.wait_for(rig.scan.stop(), STUCK_S)  # a stop that asks a neighbour waits for the fuse of the answer
    left = []
    while not spy.queue.empty():
        left.append(spy.queue.get_nowait())
    assert [each.topic for each in left if each.kind == "query"] == []


# --- what the user switched off ---------------------------------------------------------------------------------------------


async def switch(rig: "Rig", season: int, episodes: list[int] | int | None, switched: bool) -> dict:
    episodes = [episodes] if isinstance(episodes, int) else episodes
    return await rig.probe.request("scan.output.switch", {
        "series_id": rig.series_id, "season": season, "episodes": episodes, "off": switched}, timeout=STUCK_S)


def memory_of(rig: "Rig") -> tuple[str, str]:
    [row] = rig.query("SELECT ignored_seasons, ignored_episodes FROM series WHERE id=?", (rig.series_id,))
    return row["ignored_seasons"], row["ignored_episodes"]


def writes_of(sub) -> list[dict]:
    """The fields the switch sent to the catalog, in the order it sent them."""
    return [each["fields"] for each in drain(sub)]


async def test_an_episode_switched_off_by_the_user_is_remembered_by_number_and_switched_back_on(rig):
    """The switch sends the memory of the series to the catalog by number; switching on sends it back without the number."""
    writes = rig.bus.subscribe("catalog.series.update")
    assert await switch(rig, 1, 2, True) == {"success": True}
    assert await switch(rig, 1, 2, False) == {"success": True}
    assert writes_of(writes) == [
        {"ignored_seasons": "[]", "ignored_episodes": '{"1": [2]}'},
        {"ignored_seasons": "[]", "ignored_episodes": "{}"}]
    assert memory_of(rig) == ("[]", "{}")


async def test_many_episodes_are_switched_off_and_on_by_one_change_of_the_memory(rig):
    """A selection of episodes goes as one command: the memory of the series changes once, with all of them."""
    writes = rig.bus.subscribe("catalog.series.update")
    assert await switch(rig, 1, [2, 3, 7], True) == {"success": True}
    assert await switch(rig, 1, [3, 7], False) == {"success": True}
    assert writes_of(writes) == [
        {"ignored_seasons": "[]", "ignored_episodes": '{"1": [2, 3, 7]}'},
        {"ignored_seasons": "[]", "ignored_episodes": '{"1": [2]}'}]
    assert memory_of(rig) == ("[]", '{"1": [2]}')


async def test_an_episode_switched_off_in_advance_comes_back_when_the_user_switches_it_on(rig):
    """The memory holds an episode switched off before any sync; switching it on drops its number from the memory."""
    rig.sql("UPDATE series SET ignored_episodes='{\"1\": [2]}' WHERE id=?", (rig.series_id,))
    writes = rig.bus.subscribe("catalog.series.update")
    assert await switch(rig, 1, 2, False) == {"success": True}
    assert writes_of(writes) == [{"ignored_seasons": "[]", "ignored_episodes": "{}"}]
    assert memory_of(rig) == ("[]", "{}")


async def test_a_season_is_switched_off_by_the_list_of_the_series_and_switched_on_with_all_of_it(rig):
    """A season goes into the list of the series whole; switching it on takes it out and drops its episodes' numbers."""
    assert await switch(rig, 1, 2, True) == {"success": True}
    writes = rig.bus.subscribe("catalog.series.update")
    assert await switch(rig, 1, None, True) == {"success": True}
    assert await switch(rig, 1, None, False) == {"success": True}
    assert writes_of(writes) == [
        {"ignored_seasons": "[1]", "ignored_episodes": '{"1": [2]}'},
        {"ignored_seasons": "[]", "ignored_episodes": "{}"}]
    assert memory_of(rig) == ("[]", "{}")


async def test_a_series_the_catalog_does_not_have_cannot_be_switched(rig):
    # The refusal is the catalog's own, relayed by the scan with its kind: the caller sees "not found", not a failure.
    with pytest.raises(BusRequestError, match=r"^LookupError: "):
        await rig.probe.request("scan.output.switch", {"series_id": 999, "season": 1, "off": True}, timeout=STUCK_S)


async def test_no_change_of_a_series_the_catalog_does_not_have_is_admitted(rig):
    """Every change of a series by the user goes through its admission: a series that is not there is refused there, with
    the catalog's "not found", and nothing is held for it."""
    with pytest.raises(BusRequestError, match=r"^LookupError: "):
        await rig.probe.request("scan.edit.begin", {"series_id": 999, "token": "t"}, timeout=STUCK_S)
    assert 999 not in rig.scan._edits


AFTER = ("playable", "rule", "source", "no_number", "missing")
OTHER = ("none", "worse", "better")


# --- a sync is one run: the series is busy until every work of it ends, and works by the snapshot (stage 11) ----------


def runs_of(events: list[dict]) -> list[bool]:
    return [each["active"] for each in events]


def work_state(rig: "Rig", owner: str, active: bool, busy: list[list] | None = None) -> None:
    rig.world.tell_work(rig.series_id, owner, active, busy)


async def test_a_sync_is_one_run_and_the_series_is_busy_until_every_work_it_gave_out_has_ended(rig):
    rig.world.work = {"planner": True}
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run()
    assert runs_of(drain(runs)) == [True]
    assert [each["results_data"] for each in rig.query("SELECT results_data FROM scan_tasks")] != []
    with pytest.raises(BusRequestError, match="not_allowed"):
        await switch(rig, 1, 1, True)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await rig.run()  # a second sync of a busy series is refused too

    work_state(rig, "planner", False)
    assert runs_of([await next_event(runs)]) == [False]
    assert rig.query("SELECT id FROM scan_tasks") == []
    assert await switch(rig, 1, 1, True) == {"success": True}  # free again


async def test_a_run_whose_executors_have_no_work_ends_with_its_pass(rig):
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run(download=False)
    assert runs_of(drain(runs)) == [True, False] and rig.query("SELECT id FROM scan_tasks") == []


async def test_a_run_found_open_after_a_restart_waits_for_its_works_again(db_path):
    first = await build(db_path)
    first.world.work = {"planner": True}
    await first.run()
    series_id, source_ids = first.series_id, first.source_ids
    await first.runner.stop()

    again = await build(db_path, series_id=series_id, source_ids=source_ids)
    try:
        again.world.work = {"planner": True}
        runs = again.bus.subscribe(RUN_CHANGED)
        again.probe.publish_event("system.started", {})
        assert runs_of([await next_event(runs)]) == [True]
        with pytest.raises(BusRequestError, match="not_allowed"):
            await switch(again, 1, 1, True)
        assert "composition.recompute" not in again.world.order  # the pass is not done again: only the works are waited for
        work_state(again, "planner", False)
        assert runs_of([await next_event(runs)]) == [False]
        assert again.query("SELECT id FROM scan_tasks") == []
    finally:
        await again.runner.stop()


async def test_a_run_is_not_closed_while_its_executor_has_not_answered_however_its_events_come(rig):
    """Н-4: the planner's «done» comes while its answer to the request is held: the run stays open until the answer is
    in, and the event, counted later, is what closes it."""
    rig.world.work = {"planner": True}
    gate = rig.world.work_gates["planner"] = asyncio.Event()
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run()
    assert await wait_until(lambda: f"planner.work.get {rig.series_id}" in rig.world.order, rig.bus, *rig.runner.modules)
    assert runs_of(drain(runs)) == [True]
    work_state(rig, "planner", False)
    # The event is taken in (the bus cannot go quiet: the answer is held), and still nothing closes.
    assert await wait_until(lambda: rig.scan._working[rig.series_id].seen.get("planner") == rig.world.seq,
                            rig.bus, *rig.runner.modules)
    assert drain(runs) == [] and not running.done()
    gate.set()
    await running
    assert runs_of([await next_event(runs)]) == [False]


async def test_an_answer_counted_before_an_event_does_not_undo_it(rig):
    """A reply and an event of one executor that cross: the state counted later is in force, so «working» counted before
    «done» does not leave the run open for ever."""
    rig.world.work = {"planner": True}
    gate = rig.world.work_gates["planner"] = asyncio.Event()
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run()
    assert await wait_until(lambda: f"planner.work.get {rig.series_id}" in rig.world.order, rig.bus, *rig.runner.modules)
    work_state(rig, "planner", False)  # counted after the reply that is on its way
    gate.set()
    await running
    await rig.bus.settled()
    assert runs_of(drain(runs)) == [True, False]
    assert rig.query("SELECT id FROM scan_tasks") == []


async def test_a_change_admitted_before_a_sync_holds_its_opening_and_none_is_admitted_after(rig):
    """Н-8: the check of a change and its write are not apart from the opening of a run: a sync waits for a change admitted
    before it, and a change asked once the sync is starting is refused."""
    token = (await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S))["token"]
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run(download=False)
    # The sync is started and holds at its opening (the bus does not go quiet: its request is still being answered).
    assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
    assert rig.world.order == [] and drain(runs) == []  # the sync waits for the change
    with pytest.raises(BusRequestError, match="not_allowed"):
        await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S)
    rig.probe.send_command("scan.edit.end", {"series_id": rig.series_id, "token": token})
    await running
    assert runs_of(drain(runs)) == [True, False]
    again = await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S)
    assert again["ok"] is True and again["token"] != token
    rig.probe.send_command("scan.edit.end", {"series_id": rig.series_id, "token": again["token"]})


async def test_a_run_works_by_the_configuration_it_took_when_it_started(rig):
    """Н-5: the rules of the profiles and the settings of the voice-overs are taken once, when the sync starts, kept with
    its command and given to every step that would read them; a change saved meanwhile counts from the next run."""
    rig.world.profiles_export = {1: [{"id": 1, "name": "серия", "conditions": []}]}
    rig.world.voices_export = {"voices.groups": '{"ПВА": ["ПВА ШОУ"]}'}
    rig.world.work = {"planner": True}
    await rig.run()
    stored = json.loads(rig.query("SELECT results_data FROM scan_tasks")[0]["results_data"])["snapshot"]
    assert stored == {"profiles": {"1": [{"id": 1, "name": "серия", "conditions": []}]},
                      "voices": {"voices.groups": '{"ПВА": ["ПВА ШОУ"]}'}}
    rig.world.profiles_export = {1: [{"id": 1, "name": "другое", "conditions": []}]}  # saved during the run
    rig.world.voices_export = {}
    [recount] = rig.world.payloads["composition.recompute"]
    assert recount["snapshot"] == stored
    work_state(rig, "planner", False)
    await rig.bus.settled()
    rig.world.work["planner"] = True
    await rig.run()
    again = json.loads(rig.query("SELECT results_data FROM scan_tasks")[0]["results_data"])["snapshot"]
    assert again["profiles"] == {"1": [{"id": 1, "name": "другое", "conditions": []}]} and again["voices"] == {}


@pytest.mark.asyncio
async def test_the_run_is_told_with_its_snapshot_and_the_open_runs_are_given_with_theirs(rig):
    """Н-11: an executor works by the snapshot in every pass of the run, and one that starts again takes the open runs and
    their snapshots from the commands, before its works."""
    rig.world.voices_export = {"voices.source_priority": '["cvh", "kodik"]'}
    rig.world.work = {"planner": True}
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run()
    told = []
    while not runs.queue.empty():
        told.append(runs.queue.get_nowait().payload)
    assert any(each["active"] and (each.get("snapshot") or {}).get("voices") == rig.world.voices_export for each in told)

    open_runs = (await rig.probe.request("scan.runs.get", {}, timeout=5))["runs"]
    assert open_runs == [{"series_id": rig.series_id,
                          "snapshot": {"profiles": open_runs[0]["snapshot"]["profiles"],
                                       "voices": rig.world.voices_export}}]
    # The announcements are numbered in order, and the reading is as of the last of them.
    assert [each["seq"] for each in told] == sorted({each["seq"] for each in told}) and told[0]["seq"] > 0
    assert (await rig.probe.request("scan.runs.get", {}, timeout=5))["as_of"] == told[-1]["seq"]
    work_state(rig, "planner", False)
    await rig.bus.settled()
    closed = (await rig.probe.request("scan.runs.get", {}, timeout=5))
    assert closed["runs"] == [] and closed["as_of"] > told[-1]["seq"]


# --- an admission lasts until the change is answered, not until its caller stops waiting (N-12, N-14) -------------------

class SlowWriter(BaseModule):
    """A module whose write is held until the test lets it go, and which notes whether a run was open when it wrote."""
    name = "review_writer"

    def __init__(self, bus, scan):
        self.entered, self.release, self.written = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.run_open_at_write: list[bool] = []
        self._scan = scan
        super().__init__(bus)

    def register(self):
        self.handle("review.write", self.write)

    async def write(self, env):
        self.entered.set()
        await self.release.wait()
        self.run_open_at_write.append(bool(self._scan._runs))
        self.written.set()


@pytest.mark.asyncio
@pytest.mark.parametrize("stops", ["fuse", "dropped"])
async def test_a_change_whose_caller_stops_waiting_still_holds_off_the_sync_until_it_is_written(rig, stops):
    """The fuse of the gateway ran out, or the HTTP request was dropped: the write is still in the hands of its handler, so
    the admission holds until it is answered, and the sync opens after it."""
    from modules.gateway.routing import admitted

    writer = SlowWriter(rig.bus, rig.scan)
    await writer.start()
    try:
        async def edit():
            async with admitted(rig.probe, rig.series_id):
                await rig.probe.request("review.write", {}, timeout=0.05 if stops == "fuse" else 30)

        editing = asyncio.create_task(edit())
        await asyncio.wait_for(writer.entered.wait(), 5)
        if stops == "dropped":
            editing.cancel()
        with pytest.raises((asyncio.TimeoutError, asyncio.CancelledError)):
            await editing
        assert rig.scan._edits.get(rig.series_id)  # held: the write has not been answered
        running = asyncio.create_task(rig.run())
        assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
        assert rig.series_id not in rig.scan._runs  # the sync waits for the change

        writer.release.set()
        await asyncio.wait_for(writer.written.wait(), 5)
        await asyncio.wait_for(running, 10)
        assert writer.run_open_at_write == [False]
    finally:
        writer.release.set()
        await writer.stop()


@pytest.mark.asyncio
async def test_a_change_admitted_before_a_sync_is_not_refused_by_the_sync_that_waits_for_it(rig):
    from modules.gateway.routing import admitted

    async with admitted(rig.probe, rig.series_id):
        running = rig.start_run(download=False)
        assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
        await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episodes": [1], "off": True},
                                timeout=5)
    await asyncio.wait_for(running, 10)

    # A change that was not admitted before the sync is refused as before, whatever number it names.
    rig.scan._running.add(rig.series_id)
    try:
        with pytest.raises(BusRequestError, match="NotAllowed"):
            await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episodes": [1],
                                                           "off": False}, timeout=5)
    finally:
        rig.scan._running.discard(rig.series_id)


@pytest.mark.asyncio
async def test_a_card_written_by_the_real_catalog_after_the_fuse_is_written_before_the_sync_opens(rig, monkeypatch):
    from modules.catalog import CatalogModule
    from modules.gateway.routing import admitted

    catalog = next(module for module in rig.runner.modules if isinstance(module, CatalogModule))
    update = catalog.repo.update_series
    entered, release = asyncio.Event(), asyncio.Event()
    run_open_at_commit: list[bool] = []

    async def held_update(series_id, fields):
        entered.set()
        await release.wait()
        run_open_at_commit.append(series_id in rig.scan._runs)
        return await update(series_id, fields)

    monkeypatch.setattr(catalog.repo, "update_series", held_update)

    async def edit():
        async with admitted(rig.probe, rig.series_id):
            await rig.probe.request("catalog.series.update", {"series_id": rig.series_id, "fields": {"name": "LATE"}},
                                    timeout=0.05)

    editing = asyncio.create_task(edit())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises(asyncio.TimeoutError):
            await editing
        running = asyncio.create_task(rig.run())
        assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
        release.set()
        await asyncio.wait_for(running, 10)
        assert run_open_at_commit == [False]
        assert rig.query("SELECT name FROM series WHERE id=?", (rig.series_id,))[0]["name"] == "LATE"
    finally:
        release.set()


@pytest.mark.asyncio
async def test_a_write_nested_under_the_handler_of_an_admitted_request_holds_the_admission_until_it_is_written(
        rig, monkeypatch):
    """Н-18: the switch of an output asks the catalog to write; the scan stops waiting for it (its fuse) and answers the
    gateway with an error, but the write is still going, so the admission holds and the sync opens after it."""
    from modules.catalog import CatalogModule
    from modules.gateway.routing import admitted

    catalog = next(module for module in rig.runner.modules if isinstance(module, CatalogModule))
    update, request = catalog.repo.update_series, rig.scan.request
    entered, release, committed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    run_open_at_commit: list[bool] = []

    async def held_update(series_id, fields):
        entered.set()
        await release.wait()
        run_open_at_commit.append(series_id in rig.scan._runs)
        result = await update(series_id, fields)
        committed.set()
        return result

    async def short_fuse(topic, payload=None, **kwargs):
        if topic == "catalog.series.update":
            kwargs["timeout"] = 0.05
        return await request(topic, payload, **kwargs)

    monkeypatch.setattr(catalog.repo, "update_series", held_update)
    monkeypatch.setattr(rig.scan, "request", short_fuse)
    rig.world.work = {"planner": True}

    async def edit():
        async with admitted(rig.probe, rig.series_id):
            await rig.probe.request("scan.output.switch", {"series_id": rig.series_id, "season": 1, "episodes": [1],
                                                           "off": True}, timeout=0.2)

    editing = asyncio.create_task(edit())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        with pytest.raises((asyncio.TimeoutError, BusRequestError)):
            await editing
        assert rig.scan._edits.get(rig.series_id)  # held: the nested write has not been answered
        running = asyncio.create_task(rig.run())
        assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
        assert rig.series_id not in rig.scan._runs
        release.set()
        await asyncio.wait_for(committed.wait(), 5)
        await asyncio.wait_for(running, 10)
        assert run_open_at_commit == [False]
    finally:
        release.set()


@pytest.mark.asyncio
async def test_an_answer_that_comes_while_its_cancelled_caller_is_still_registered_is_counted_once(rig):
    """Н-19: the answer is queued, the caller is cancelled before its consumer runs, then the answer is read: it is counted
    there, so the admission ends."""
    from core import BaseModule
    from core.envelope import Envelope
    from modules.gateway.routing import admitted

    entered, release = asyncio.Event(), asyncio.Event()
    asked: list[Envelope] = []

    class Slow(BaseModule):
        name = "review_slow"

        def register(self):
            self.handle("review.slow", self.query)

        async def query(self, env):
            asked.append(env)
            entered.set()
            await release.wait()

    slow = Slow(rig.bus)
    await slow.start()

    async def edit():
        async with admitted(rig.probe, rig.series_id):
            await rig.probe.request("review.slow", {}, timeout=5)

    editing = asyncio.create_task(edit())
    try:
        await asyncio.wait_for(entered.wait(), 5)
        env = asked[0]
        rig.bus.publish(Envelope(topic=env.reply_to, kind="reply", correlation_id=env.correlation_id, payload={}))
        editing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await editing
        release.set()
        assert await wait_until(lambda: not rig.scan._edits.get(rig.series_id), rig.bus, *rig.runner.modules)
        assert not rig.probe._admitted
    finally:
        release.set()
        await slow.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("stops", ["after_the_grant", "before_the_grant"])
async def test_a_caller_that_stops_waiting_for_its_grant_gives_it_back_and_the_series_is_free(rig, monkeypatch, stops):
    """Н-23: the caller is cancelled while the grant is on its way back (it was made), or before scan has read the request
    (the end reaches scan first): either way no number is left open, and a sync opens."""
    from modules.gateway.routing import admitted

    publish, grants, editing = rig.bus.publish, [], None
    held = asyncio.Event()

    def watched(env):
        if stops == "before_the_grant" and env.topic == "scan.edit.begin" and env.kind == "query":
            # The request waits in front of scan; the caller is gone first, and its end goes out before the request is read.
            grants.append(env)
            editing.cancel()
            held.set()
            return 1
        result = publish(env)
        if env.topic == "scan.edit.begin" and env.kind == "query":
            grants.append(env)
        elif stops == "after_the_grant" and env.kind == "reply" and grants and env.correlation_id == grants[0].correlation_id:
            editing.cancel()
        return result

    monkeypatch.setattr(rig.bus, "publish", watched)
    entered = []

    async def edit():
        async with admitted(rig.probe, rig.series_id):
            entered.append(True)

    editing = asyncio.create_task(edit())
    with pytest.raises(asyncio.CancelledError):
        await editing
    monkeypatch.setattr(rig.bus, "publish", publish)
    if stops == "before_the_grant":
        await held.wait()
        await rig.bus.settled()  # the end is in
        publish(grants[0])  # now the request is read: it opens nothing
    await rig.bus.settled()

    assert entered == [] and not rig.scan._edits.get(rig.series_id) and not rig.scan._ended_early.get(rig.series_id)
    await asyncio.wait_for(rig.run(download=False), 10)


@pytest.mark.asyncio
async def test_a_change_that_does_not_end_is_told_in_the_log_and_the_sync_still_waits_for_it(rig, monkeypatch, caplog):
    """K-10: the admission is not released (a late write would land in the sync), but the wait is not silent."""
    import modules.scan.module as scan_module

    monkeypatch.setattr(scan_module, "EDIT_STUCK_S", 0.05)
    token = (await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S))["token"]
    with caplog.at_level("ERROR"):
        running = rig.start_run(download=False)

        def told():
            return any("ждёт правку" in record.getMessage() for record in caplog.records)
        assert await wait_until(told, rig.bus, *rig.runner.modules, every=0.05)
    assert not running.done() and rig.series_id not in rig.scan._runs  # still waiting for the change
    rig.probe.send_command("scan.edit.end", {"series_id": rig.series_id, "token": token})
    await asyncio.wait_for(running, 10)
