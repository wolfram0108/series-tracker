"""Scan on a series: the synchronization by signals, its command, its run and the switch of an output.

Catalog and scan are real, the other neighbours are the fake `World` of tests/scan_support.py. What a test waits for is
a token or an event that a neighbour puts out, never time; the one number (STUCK_S) only ends a test that is stuck, and
the one test of a safeguard sets its own.
"""
import asyncio
import json
import sqlite3
from dataclasses import dataclass

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.contracts import RUN_CHANGED, TableState
from core.db import Database
from core.envelope import Envelope
from core.reasons import reason
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
                series_id: int | None = None, source_ids: tuple[int, ...] = (), types: tuple[str, ...] = ()) -> Rig:
    db = Database(db_path)
    if series_id is None:
        created = await create_series(db, name="Т", name_en="T", site="x", save_path="/media/t", sources=[
            source(url=url, source_type=types[i] if types else "web_video", parser_profile_id=profile)
            for i, url in enumerate(urls)])
        series_id, source_ids = created.series_id, created.source_ids
    bus = Bus()
    world = World(bus)
    scan = ScanModule(bus, db, scheduler=False, table_wait_s=table_wait_s)
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


async def test_the_scope_of_a_synchronization_is_written_with_its_command_and_goes_to_the_planner(rig):
    """A sync of a season or of an episode reads the tables of the sources as the sync of the series does; the planner is told
    the scope, and a restart finds it in the command."""
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    run = rig.start_run(season=1, episode=3)
    await rig.world.asked.get()
    assert [json.loads(each["results_data"])["scope"] for each in tasks_of(rig)] == [{"season": 1, "episode": 3}]
    rig.world.finish(URL1)
    await asyncio.wait_for(run, STUCK_S)
    await rig.run(season=2)
    await rig.run()
    assert rig.world.payloads["planner.sync"] == [
        {"series_id": rig.series_id, "download": True, "season": 1, "episode": 3},
        {"series_id": rig.series_id, "download": True, "season": 2},
        {"series_id": rig.series_id, "download": True}]


async def test_a_command_found_at_a_restart_goes_on_as_the_sync_of_its_scope_not_of_the_series(rig):
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True, "scope": {"season": 1, "episode": 2}})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        await no_task_left(again)
        assert again.world.payloads["planner.sync"] == [
            {"series_id": again.series_id, "download": True, "season": 1, "episode": 2}]
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


# --- a failed step is the signal of failure, seen in the state of the scan --------------------------------------

async def test_the_scheduled_pass_skips_a_series_a_provider_that_is_not_up_reads_and_asks_nobody(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    rig.world.blocked[URL1] = reason("provider_not_connected", state="connecting")
    await rig.scan._scan_all()
    assert rig.world.order == [] and rig.query("SELECT * FROM scan_tasks") == []


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
    assert [each.topic for each in left if each.reply_to] == []


# --- what the user switched off ---------------------------------------------------------------------------------------------


async def switch(rig: "Rig", season: int, episodes: list[int] | int | None, switched: bool) -> dict:
    episodes = [episodes] if isinstance(episodes, int) else episodes
    return await rig.probe.request("scan.output.switch", {
        "series_id": rig.series_id, "season": season, "episodes": episodes, "off": switched}, timeout=STUCK_S)


OTHER = ("none", "worse", "better")


# --- a sync is one run: the series is busy until every work of it ends, and works by the snapshot (stage 11) ----------


def runs_of(events: list[dict]) -> list[bool]:
    return [each["active"] for each in events]


def work_state(rig: "Rig", owner: str, active: bool, busy: list[list] | None = None, failure: str | None = None) -> None:
    rig.world.tell_work(rig.series_id, owner, active, busy, failure)


async def test_a_run_whose_executor_broke_ends_as_failed_with_its_reason_not_as_a_success(rig):
    """The work of the executor ended with a failure: the run is not a success — its command stays as the error of the
    series with the reason the executor told, and the series shows the error until the next sync."""
    rig.world.work = {"planner": True}
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run()
    assert runs_of(drain(runs)) == [True]
    work_state(rig, "planner", False, failure=reason("planner_sync_failed", cause="RuntimeError: boom"))
    assert runs_of([await next_event(runs)]) == [False]
    [task] = tasks_of(rig)
    assert task["status"] == "error"
    assert json.loads(json.loads(task["results_data"])["error"])["code"] == "planner_sync_failed"


# --- an admission lasts until the change is answered, not until its caller stops waiting (N-12, N-14) -------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("stops", ["after_the_grant", "before_the_grant"])
async def test_a_caller_that_stops_waiting_for_its_grant_gives_it_back_and_the_series_is_free(rig, monkeypatch, stops):
    """Н-23: the caller is cancelled while the grant is on its way back (it was made), or before scan has read the request
    (the end reaches scan first): either way no number is left open, and a sync opens."""
    from modules.gateway.routing import admitted

    publish, grants, editing = rig.bus.publish, [], None
    held = asyncio.Event()

    def watched(env):
        if stops == "before_the_grant" and env.topic == "scan.edit.begin" and env.kind == "command":
            # The request waits in front of scan; the caller is gone first, and its end goes out before the request is read.
            grants.append(env)
            editing.cancel()
            held.set()
            return 1
        result = publish(env)
        if env.topic == "scan.edit.begin" and env.kind == "command":
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
