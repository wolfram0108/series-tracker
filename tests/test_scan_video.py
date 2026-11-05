"""Scan on a series of kind `video`: the synchronization by signals, the references, the plan and the events.

Catalog and scan are real, the other neighbours are the fake `World` of tests/scan_support.py. What a test waits for is
a token or an event that a neighbour puts out, never time; the one number (STUCK_S) only ends a test that is stuck, and
the one test of a safeguard sets its own.
"""
import asyncio
import json
import sqlite3
from dataclasses import dataclass

import pytest

from core import BaseModule, Bus, BusRequestError, Runner, ids
from core.contracts import (REFERENCE_CHOICE, VARIANT_ALLOW, Level, Reference, ReferenceChanged, ReferenceChoice,
                            ReferenceMark, RowState, SeriesSource, TableState, variant_target)
from core.db import Database
from core.envelope import Envelope
from core.reasons import parse, reason
from modules.catalog import CatalogModule
from modules.scan import ScanModule
from modules.scan.candidates import NO_DATE
from scan_support import STUCK_S, Page, World, episode_settings, lever, migrated_db, row
from series_factory import create_series, source
from settle import wait_until

URL1 = "https://fake.test/p/1"
URL2 = "https://fake.test/p/2"
MARK = ReferenceMark


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

    async def output(self) -> list[Reference]:
        reply = await self.probe.request("scan.output.get", {"series_id": self.series_id}, timeout=STUCK_S)
        return [Reference.model_validate(each) for each in reply]

    def query(self, sql: str, params=()) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(each) for each in conn.execute(sql, params)]

    def media(self) -> dict[str, dict]:
        """The rows of the plan by row key; a key with two rows (a better version) keeps the later one."""
        return {each["row_key"] or each["unique_id"]: each for each in self.query(
            "SELECT * FROM media_items WHERE series_id=? ORDER BY id", (self.series_id,))}

    def mark(self, unique_id: str, **columns) -> None:
        sets = ", ".join(f"{name}=?" for name in columns)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"UPDATE media_items SET {sets} WHERE unique_id=?", (*columns.values(), unique_id))
            conn.commit()

    def downloaded(self, row_key: str, filename: str = "e01.mp4") -> str:
        unique_id = self.media()[row_key]["unique_id"]
        self.mark(unique_id, status="completed", final_filename=filename)
        return unique_id

    def sql(self, statement: str, params=()) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(statement, params)
            conn.commit()

    def row(self, key: str, season, start, end=None, *, source: int = 0, **fields):
        return row(self.series_id, self.source_ids[source], key, season, start, end, **fields)


def drain(sub) -> list[dict]:
    found = []
    while not sub.queue.empty():
        found.append(sub.queue.get_nowait().payload)
    return found


async def next_event(sub) -> dict:
    return (await asyncio.wait_for(sub.queue.get(), STUCK_S)).payload


def change(rig: "Rig", key: str, season: int, episode: int, *, mark: ReferenceMark = MARK.BY_LEVEL, part=None,
           player: str = "vk", height: int | None = 1080, voice: str = "Ruchime", source: int = 0) -> ReferenceChanged:
    """The event the reference of the row `key` gives: the whole reference and the series."""
    return ReferenceChanged(series_id=rig.series_id, season=season, episode=episode, part=part, mark=mark,
                            source_id=rig.source_ids[source], row_key=key, player=player, voice_group=voice,
                            height=height)


def gone(rig: "Rig", season: int, episode: int, part=None) -> ReferenceChanged:
    """The event of a slot that left the output: no reference, and the reason of an episode with none."""
    return ReferenceChanged(series_id=rig.series_id, season=season, episode=episode, part=part, mark=MARK.NONE,
                            reason=reason("reference_none"))


def off(rig: "Rig", season: int, episode: int, part=None) -> ReferenceChanged:
    """The event of a slot the user switched off: nothing taken, and the reason that says so."""
    return ReferenceChanged(series_id=rig.series_id, season=season, episode=episode, part=part, mark=MARK.OFF,
                            reason=reason("reference_off"))


def changes_of(events: list[dict]) -> list[ReferenceChanged]:
    return [ReferenceChanged.model_validate(each) for each in events]


async def next_change(sub) -> ReferenceChanged:
    return ReferenceChanged.model_validate(await next_event(sub))


def marks(events: list[dict]) -> list[tuple[int, int, str]]:
    return sorted((each["season"], each["episode"], each["mark"]) for each in events)


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
    world.sources = list(zip(source_ids, urls))
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
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    await rig.world.dispatched.get()
    await rig.world.dispatched.get()
    assert rig.world.order == [
        f"library.relocation.apply {rig.series_id}", "downloads.fs.sync", f"sources.table.get {URL1}", "composition.recompute",
        "composition.get", "voices.settings.get", "renaming.reprocess", f"downloads.dispatch {rig.series_id}",
        f"voices.dispatch {rig.series_id}", f"downloads.work.get {rig.series_id}", f"voices.work.get {rig.series_id}"]


async def test_a_synchronization_without_download_brings_the_disk_to_the_plan_and_fetches_nothing(rig):
    """The disk follows the plan in a sync alone (И-59): the names after the plan of the same pass, then the files and the
    voice-overs; without download nothing is dispatched to fetch."""
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run(download=False)
    await rig.world.dispatched.get()
    await rig.world.dispatched.get()
    order = rig.world.order
    assert order[order.index("voices.settings.get"):] == [
        "voices.settings.get", "renaming.reprocess", f"downloads.settle {rig.series_id}", f"voices.settle {rig.series_id}",
        f"downloads.work.get {rig.series_id}", f"voices.work.get {rig.series_id}"]
    assert not any(each.startswith(("downloads.dispatch", "voices.dispatch")) for each in order)


async def test_a_recount_on_an_event_changes_the_plan_alone(rig):
    """A rule saved, an episode switched off: the plan is counted again, nothing is renamed, settled or dispatched."""
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    await rig.world.dispatched.get()
    await rig.world.dispatched.get()
    rig.world.order.clear()
    rig.world.rows = [rig.row("a", 1, 2)]
    updates = rig.bus.subscribe("scan.plan.updated")
    rig.probe.publish_event("composition.changed", {"series_id": rig.series_id, "rev": 2, "episodes": []})
    await next_event(updates)
    assert "composition.get" in rig.world.order
    assert not any(each.startswith(("renaming", "downloads.dispatch", "downloads.settle", "voices.dispatch", "voices.settle"))
                   for each in rig.world.order)


async def test_a_synchronization_waits_for_the_verdict_of_a_page_that_is_being_read(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    rig.world.rows = [rig.row("a", 1, 1)]
    run = rig.start_run()
    assert await rig.world.asked.get() == URL1
    rig.world.finish(URL1)
    result = await asyncio.wait_for(run, STUCK_S)
    order = rig.world.order
    assert order.index(f"table.ready {URL1}") < order.index("composition.recompute")
    assert result["sources"] == [{"source_id": rig.source_ids[0], "url": URL1, "state": "ready", "reason": None}]


# --- the command of a synchronization is the database's (T-28) ---------------------------------------------------------

def tasks_of(rig: "Rig") -> list[dict]:
    return rig.query("SELECT status, results_data FROM scan_tasks WHERE series_id=?", (rig.series_id,))


async def no_task_left(rig: "Rig") -> None:
    """The command is gone from the database: it goes when the scan ends, a moment after the last signal it sent."""
    assert await wait_until(lambda: not tasks_of(rig), rig.bus, rig.scan), ("the command stayed", tasks_of(rig))


async def test_the_command_of_a_synchronization_is_written_before_its_table_is_waited_for_and_gone_when_it_ends(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    rig.world.rows = [rig.row("a", 1, 1)]
    run = rig.start_run(download=False)
    await rig.world.asked.get()
    assert [(each["status"], json.loads(each["results_data"])) for each in tasks_of(rig)] == [
        ("processing", {"video_sync": True, "download": False, "inventory": False,
                        "snapshot": {"profiles": {}, "voices": {}}})]
    rig.world.finish(URL1)
    await asyncio.wait_for(run, STUCK_S)
    assert tasks_of(rig) == []


async def test_a_synchronization_that_fails_leaves_its_command_as_the_error_of_the_series_and_one_record_only(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.recompute_error = "boom"
    with pytest.raises(BusRequestError):
        await rig.run()
    assert [(each["status"], json.loads(each["results_data"]).get("video_sync")) for each in tasks_of(rig)] == [("error", True)]
    rig.world.recompute_error = None
    await rig.run()
    assert tasks_of(rig) == []


async def test_a_restart_in_the_middle_of_a_synchronization_runs_it_again_from_its_command_when_the_system_has_started(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        assert again.world.order == []  # nothing is asked while the modules start
        again.world.rows = [again.row("a", 1, 1)]
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        assert await asyncio.wait_for(again.world.dispatched.get(), STUCK_S)
        assert await asyncio.wait_for(again.world.dispatched.get(), STUCK_S)
        assert f"downloads.dispatch {again.series_id}" in again.world.order
        await no_task_left(again)
        assert [each.mark for each in await again.output()] == [MARK.BY_LEVEL]
    finally:
        await again.runner.stop()


async def test_a_command_found_at_a_restart_without_the_wish_to_download_does_not_download(rig):
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": False})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        again.world.rows = [again.row("a", 1, 1)]
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        await no_task_left(again)
        assert "downloads.dispatch" not in " ".join(again.world.order)
    finally:
        await again.runner.stop()


async def test_a_command_found_at_a_restart_for_a_series_already_being_scanned_is_dropped(rig):
    rig.world.pages[URL1] = Page(TableState.READING, held=False)
    rig.world.rows = [rig.row("a", 1, 1)]
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
    rig.world.rows = [rig.row("a", 1, 1)]
    result = await rig.run()
    assert result["sources"][0]["state"] == "ready"


async def test_a_page_read_within_its_term_is_not_waited_for(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    assert (await rig.run())["sources"][0]["state"] == "ready"
    assert rig.world.order.count(f"sources.table.get {URL1}") == 1


async def test_every_source_is_waited_for_at_once(duo):
    for url in (URL1, URL2):
        duo.world.pages[url] = Page(TableState.READING, held=False)
    duo.world.rows = [duo.row("a", 1, 1), duo.row("b", 1, 2, source=1)]
    run = duo.start_run()
    assert {await duo.world.asked.get(), await duo.world.asked.get()} == {URL1, URL2}
    duo.world.finish(URL2)
    duo.world.finish(URL1)
    result = await asyncio.wait_for(run, STUCK_S)
    assert [each["state"] for each in result["sources"]] == ["ready", "ready"]


async def test_a_failed_read_with_an_earlier_table_goes_on_with_that_table_and_says_why(rig):
    """S-10: the last complete table stays in force; the reason is in the result of the synchronization."""
    rig.world.pages[URL1] = Page(TableState.READING, held=True)
    rig.world.rows = [rig.row("a", 1, 1)]
    run = rig.start_run()
    await rig.world.asked.get()
    why = json.dumps({"code": "page_unreachable"})
    rig.world.fail(URL1, why)
    result = await asyncio.wait_for(run, STUCK_S)
    assert result["sources"] == [{"source_id": rig.source_ids[0], "url": URL1, "state": "stale", "reason": why}]
    assert "composition.recompute" in rig.world.order and (await rig.output())[0].mark == MARK.BY_LEVEL


async def test_a_failure_answered_at_once_is_taken_as_the_verdict_and_is_not_waited_for(rig):
    rig.world.pages[URL1] = Page(TableState.FAILED, held=True, why='{"code":"site_not_found"}')
    rig.world.rows = [rig.row("a", 1, 1)]
    result = await rig.run()
    assert result["sources"][0]["state"] == "stale" and parse(result["sources"][0]["reason"])["code"] == "site_not_found"


async def test_a_source_with_no_table_at_all_is_skipped_and_the_others_go_on(duo):
    duo.world.pages[URL1] = Page(TableState.READING, held=False)
    duo.world.rows = [duo.row("b", 1, 2, source=1)]
    run = duo.start_run()
    await duo.world.asked.get()
    await duo.world.asked.get()
    why = json.dumps({"code": "page_unreachable"})
    duo.world.fail(URL1, why)
    result = await asyncio.wait_for(run, STUCK_S)
    assert [(each["state"], each["reason"]) for each in result["sources"]] == [("skipped", why), ("ready", None)]
    assert [each.episode for each in await duo.output()] == [2]


async def test_the_safeguard_of_the_wait_fails_the_source_and_never_counts_as_ready(db_path):
    made = await build(db_path, table_wait_s=0.2)
    try:
        made.world.pages[URL1] = Page(TableState.READING, held=True)
        made.world.rows = [made.row("a", 1, 1)]
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
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.recompute_error = rig.world.failure()
    with pytest.raises(BusRequestError) as failed:
        await rig.run()
    assert parse(str(failed.value).split(": ", 1)[1])["code"] == "scan_step_failed"
    carrier = rig.query("SELECT status, results_data FROM scan_tasks WHERE series_id=?", (rig.series_id,))
    assert [each["status"] for each in carrier] == ["error"]
    assert "scan_step_failed" in carrier[0]["results_data"]
    assert "composition.get" not in rig.world.order
    rig.world.recompute_error = None
    await rig.run()
    assert rig.query("SELECT * FROM scan_tasks WHERE series_id=?", (rig.series_id,)) == []


async def test_the_folder_a_save_planned_is_moved_first_and_the_sync_works_in_it(rig):
    """The disk changes in a sync alone (И-59): the move a save planned is its first step."""
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.planned_path = "/media/moved"
    await rig.run(download=False)
    assert rig.world.order[0] == f"library.relocation.apply {rig.series_id}"
    assert rig.query("SELECT save_path FROM series WHERE id=?", (rig.series_id,)) == [{"save_path": "/media/moved"}]


async def test_a_move_that_fails_fails_the_sync_before_anything_else_is_done(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
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


async def test_without_a_download_nothing_is_dispatched(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    downloads, voices = rig.bus.subscribe("downloads.dispatch"), rig.bus.subscribe("voices.dispatch")
    await rig.run(download=False)
    assert downloads.queue.empty() and voices.queue.empty()
    await rig.run()
    assert (await next_event(downloads)) == {"series_id": rig.series_id}
    assert (await next_event(voices)) == {"series_id": rig.series_id, "settings": {}}  # the settings the run took


async def test_the_scheduled_pass_over_all_series_asks_for_voice_overs_too(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, 1)]
    voices = rig.bus.subscribe("voices.dispatch")
    await rig.probe.request("scan.all.start", {}, timeout=STUCK_S)
    assert await next_event(voices) == {"series_id": rig.series_id, "settings": {}}


async def test_a_scan_the_user_asks_for_reads_a_channel_in_the_mode_of_its_source(rig):
    rig.world.rows = [rig.row("a", 1, 1)]

    await rig.run()

    assert rig.world.options == [{"vk_search_mode": "search"}]


async def test_the_scheduled_pass_reads_a_channel_whole_because_the_quick_search_may_miss_a_new_upload(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, 1)]
    voices = rig.bus.subscribe("voices.dispatch")

    await rig.probe.request("scan.all.start", {}, timeout=STUCK_S)
    await next_event(voices)

    assert rig.world.options == [{"vk_search_mode": "get_all"}]
    assert [json.loads(each["results_data"]).get("inventory") for each in tasks_of(rig)] in ([], [True])


async def test_a_restart_runs_a_scan_of_the_schedule_again_as_a_scan_of_the_schedule(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    with sqlite3.connect(rig.db_path) as conn:
        conn.execute("INSERT INTO scan_tasks (series_id, status, task_data, results_data) VALUES (?, 'processing', '[]', ?)",
                     (rig.series_id, json.dumps({"video_sync": True, "download": True, "inventory": True})))
        conn.commit()
    again = await build(rig.db_path, series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        again.world.rows = [again.row("a", 1, 1)]
        again.bus.publish(Envelope(topic="system.started", kind="event"))
        assert await asyncio.wait_for(again.world.dispatched.get(), STUCK_S)
        assert again.world.options == [{"vk_search_mode": "get_all"}]
        await no_task_left(again)
    finally:
        await again.runner.stop()


def test_a_source_reads_a_channel_whole_only_when_the_scan_asks_for_the_inventory():
    channel = SeriesSource(id=1, series_id=1, url="https://vkvideo.ru/@chan|x", source_type="vk_video")
    assert channel.read_options() == {"vk_search_mode": "search"}
    assert channel.read_options(inventory=True) == {"vk_search_mode": "get_all"}


async def test_a_pass_over_all_series_that_the_stop_of_the_module_cancels_asks_nobody_on_its_way_out(rig):
    rig.sql("UPDATE series SET auto_scan_enabled=1 WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, 1)]
    voices = rig.bus.subscribe("voices.dispatch")  # nobody answers it: the pass waits for the receiver of the work
    await rig.probe.request("scan.all.start", {}, timeout=STUCK_S)
    await next_event(voices)
    spy = rig.bus.subscribe("#")
    await asyncio.wait_for(rig.scan.stop(), STUCK_S)  # a stop that asks a neighbour waits for the fuse of the answer
    left = []
    while not spy.queue.empty():
        left.append(spy.queue.get_nowait())
    assert [each.topic for each in left if each.kind == "query"] == []


# --- the references and the plan -----------------------------------------------------------------------------------

async def test_a_synchronization_chooses_the_reference_of_every_episode_and_makes_the_plan(rig):
    sid, src = rig.series_id, rig.source_ids[0]
    rig.world.rows = [
        rig.row("a", 1, 1, player="vk", height=720), rig.row("b", 1, 1, player="vibix", height=1080),
        rig.row("c", 1, 2, 3, height=2160), rig.row("d", 1, 4, state=RowState.ABSENT, height=1080)]
    changes, plan = rig.bus.subscribe("scan.reference.changed"), rig.bus.subscribe("scan.plan.updated")
    result = await rig.run()

    assert (result["kind"], result["changed"], result["in_plan"]) == ("video", True, 1)
    assert result["references"] == {"by_level": 1, "own": 0, "other_player": 0, "none": 1, "unfit": 2, "off": 0}
    shown = await rig.output()
    assert [(each.season, each.episode, each.mark) for each in shown] == [
        (1, 1, MARK.BY_LEVEL), (1, 2, MARK.UNFIT), (1, 3, MARK.UNFIT), (1, 4, MARK.NONE)]
    first = shown[0]
    assert (first.source_id, first.row_key, first.player, first.height) == (src, "b", "vibix", 1080)
    assert parse(shown[1].reason)["code"] == "reference_unfit" and parse(shown[3].reason)["code"] == "reference_none"
    rows = list(rig.media().values())
    assert len(rows) == 1  # a compilation and a row nobody plays have no row of the plan
    plan_row = rows[0]
    assert (plan_row["unique_id"], plan_row["source_id"], plan_row["row_key"]) == (ids.row_unique_id(sid, src, "b"), src, "b")
    assert (plan_row["plan_status"], plan_row["status"], plan_row["resolution"], plan_row["superseded_by"]) == (
        "in_plan_single", "pending", 1080, None)
    assert (plan_row["season"], plan_row["episode_start"], plan_row["episode_end"], plan_row["part"]) == (1, 1, None, None)
    assert plan_row["source_url"] == "https://v.test/vibix/b" and plan_row["download_url"] is None
    assert marks(drain(changes)) == [(1, 1, "by_level"), (1, 2, "unfit"), (1, 3, "unfit"), (1, 4, "none")]
    assert drain(plan) == [{"series_id": sid}]


async def test_the_same_layout_counted_again_changes_nothing_and_tells_nothing(rig):
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2, 3)]
    await rig.run()
    changes, rows = rig.bus.subscribe("scan.reference.changed"), rig.query("SELECT * FROM media_items")
    again = await rig.run()
    assert again["changed"] is False
    assert drain(changes) == [] and rig.query("SELECT * FROM media_items") == rows


async def test_the_dates_of_the_rows_are_stored_in_the_form_of_the_database_whatever_the_site_gave(rig):
    rig.world.rows = [
        rig.row("day", 1, 1, published="2026-05-09"), rig.row("vk", 1, 2, published="2026-03-17T20:13:16Z"),
        rig.row("none", 1, 3, published=None)]
    await rig.run()
    media = rig.media()
    assert [media[key]["publication_date"] for key in ("day", "vk", "none")] == [
        "2026-05-09 12:00:00", "2026-03-17 20:13:16", NO_DATE]


async def test_the_parts_of_an_episode_are_both_in_the_plan_and_both_in_the_output(rig):
    rig.world.rows = [rig.row("p1", 1, 8, part=1), rig.row("p2", 1, 8, part=2), rig.row("whole", 1, 9)]
    changes = rig.bus.subscribe("scan.reference.changed")
    await rig.run()
    assert changes_of(drain(changes)) == [change(rig, "p1", 1, 8, part=1), change(rig, "p2", 1, 8, part=2),
                                          change(rig, "whole", 1, 9)]
    assert [(each.episode, each.part, each.mark) for each in await rig.output()] == [
        (8, 1, MARK.BY_LEVEL), (8, 2, MARK.BY_LEVEL), (9, None, MARK.BY_LEVEL)]
    assert {key: each["plan_status"] for key, each in rig.media().items()} == {
        "p1": "in_plan_single", "p2": "in_plan_single", "whole": "in_plan_single"}


async def test_the_best_row_among_several_sources_is_the_reference(duo):
    duo.world.rows = [duo.row("low", 1, 1, height=720), duo.row("high", 1, 1, height=1080, player="kodik", source=1)]
    await duo.run()
    assert [(each.source_id, each.row_key) for each in await duo.output()] == [(duo.source_ids[1], "high")]


# --- the reference is replaced (M-23, S-5) -----------------------------------------------------------------------

async def test_a_better_candidate_replaces_the_reference_and_a_row_not_fetched_is_dropped(rig):
    rig.world.rows = [rig.row("low", 1, 1, player="vk", height=720)]
    await rig.run()
    changes = rig.bus.subscribe("scan.reference.changed")
    rig.world.rows = [rig.row("low", 1, 1, player="vk", height=720), rig.row("high", 1, 1, player="kodik", height=1080)]
    await rig.run()
    assert list(rig.media()) == ["high"] and rig.media()["high"]["plan_status"] == "in_plan_single"
    assert marks(drain(changes)) == [(1, 1, "by_level")]
    assert (await rig.output())[0].row_key == "high"


async def test_a_downloaded_reference_that_is_replaced_stays_as_a_row_the_winner_displaced(rig):
    rig.world.rows = [rig.row("low", 1, 1, player="vk", height=720)]
    await rig.run()
    old = rig.downloaded("low")
    rig.world.rows = [rig.row("low", 1, 1, player="vk", height=720), rig.row("high", 1, 1, player="kodik", height=1080)]
    await rig.run()
    media = rig.media()
    assert set(media) == {"low", "high"}
    assert (media["low"]["plan_status"], media["low"]["superseded_by"], media["low"]["final_filename"]) == (
        "replaced", media["high"]["unique_id"], "e01.mp4")
    assert media["high"]["plan_status"] == "in_plan_single" and media["low"]["unique_id"] == old


async def test_a_row_with_a_task_in_the_queue_of_downloads_is_kept_until_the_task_is_gone(rig):
    rig.world.rows = [rig.row("low", 1, 1, player="vk", height=720)]
    await rig.run()
    rig.world.queue_tasks = [{"task_key": rig.media()["low"]["unique_id"], "series_id": rig.series_id}]
    rig.world.rows = [rig.row("high", 1, 1, player="kodik", height=1080)]
    await rig.run()
    assert set(rig.media()) == {"low", "high"} and rig.media()["low"]["plan_status"] == "replaced"
    rig.world.queue_tasks = []
    await rig.run()
    assert list(rig.media()) == ["high"]


async def test_when_the_queue_of_downloads_cannot_be_told_no_row_is_deleted(rig):
    rig.world.rows = [rig.row("low", 1, 1, height=720)]
    await rig.run()
    rig.world.queue_error = True
    rig.world.rows = [rig.row("high", 1, 1, player="kodik", height=1080)]
    await rig.run()
    assert set(rig.media()) == {"low", "high"}


async def test_a_better_version_of_a_downloaded_candidate_gets_a_row_of_its_own(rig):
    rig.world.rows = [rig.row("k", 1, 1, height=480)]
    await rig.run()
    old = rig.downloaded("k")
    rig.world.rows = [rig.row("k", 1, 1, height=1080)]
    await rig.run()
    await rig.run()
    rows = {each["unique_id"]: each for each in rig.query("SELECT * FROM media_items")}
    better = ids.upgrade_row_unique_id(rig.series_id, rig.source_ids[0], "k", 1080, rig.world.rows[0].link)
    assert set(rows) == {old, better}
    assert rows[old]["resolution"] == 480 and rows[old]["status"] == "completed"
    assert rows[better]["resolution"] == 1080 and rows[better]["plan_status"] == "in_plan_single"
    assert rows[old]["superseded_by"] == better


async def test_what_an_earlier_version_left_on_disk_keeps_its_row_when_it_is_the_candidate_again(rig):
    """The row of a version before the stage has an id of its own; the address of the candidate finds it."""
    rig.sql("INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, plan_status, status, "
            "is_ignored_by_user, source_url, publication_date, slicing_status, is_available, resolution, "
            "final_filename) VALUES (?, 'legacy', 'Т 1 серия', 1, 1, 'in_plan_single', 'completed', 0, "
            "'https://v.test/vk/a', '2026-10-20 12:00:00', 'none', 1, 1080, 'e01.mp4')", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, 1, height=1080)]
    await rig.run()
    rows = rig.query("SELECT unique_id, source_id, row_key, plan_status FROM media_items")
    assert rows == [{"unique_id": "legacy", "source_id": rig.source_ids[0], "row_key": "a",
                     "plan_status": "in_plan_single"}]


async def test_rows_of_an_earlier_version_that_nothing_stands_for_are_dropped_or_displaced(rig):
    for unique_id, url, status, filename, episode in (
            ("lost", "https://old/1", "pending", None, 3), ("fetched", "https://old/2", "completed", "e04.mp4", 4)):
        rig.sql("INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, plan_status, "
                "status, is_ignored_by_user, source_url, publication_date, slicing_status, is_available, resolution, "
                "final_filename) VALUES (?, ?, 'x', 1, ?, 'in_plan_single', ?, 0, ?, '2026-10-20 12:00:00', 'none', 1, "
                "720, ?)", (rig.series_id, unique_id, episode, status, url, filename))
    rig.world.rows = [rig.row("a", 1, 1), rig.row("four", 1, 4)]
    await rig.run()
    rows = {each["unique_id"]: each for each in rig.query("SELECT * FROM media_items")}
    assert "lost" not in rows
    assert (rows["fetched"]["plan_status"], rows["fetched"]["superseded_by"]) == (
        "replaced", ids.row_unique_id(rig.series_id, rig.source_ids[0], "four"))


async def test_a_reference_of_a_lower_resolution_displaces_a_downloaded_higher_one_but_leaves_its_file(rig):
    """The reference is what the table offers now; the better file that is already fetched is displaced, not deleted.
    The table still has the row of the better one and says it cannot be played: a row it does not bring is held."""
    rig.world.rows = [rig.row("big", 1, 1, height=2160)]
    await rig.run()
    rig.downloaded("big")
    rig.world.rows = [rig.row("big", 1, 1, height=2160, state=RowState.ABSENT),
                      rig.row("small", 1, 1, height=1080, player="kodik")]
    await rig.run()
    media = rig.media()
    assert media["small"]["plan_status"] == "in_plan_single"
    assert (media["big"]["plan_status"], media["big"]["superseded_by"], media["big"]["final_filename"]) == (
        "replaced", media["small"]["unique_id"], "e01.mp4")


async def test_two_rows_left_in_the_plan_by_earlier_scans_collapse_to_the_winner(rig):
    for unique_id, url, resolution in (("low", "https://old/1", 360), ("high", "https://v.test/vk/a", 1080)):
        rig.sql("INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, plan_status, "
                "status, is_ignored_by_user, source_url, publication_date, slicing_status, is_available, resolution, "
                "final_filename) VALUES (?, ?, 'x', 1, 1, 'in_plan_single', 'completed', 0, ?, '2026-10-20 12:00:00', "
                "'none', 1, ?, ?)", (rig.series_id, unique_id, url, resolution, f"{unique_id}.mp4"))
    rig.world.rows = [rig.row("a", 1, 1, height=1080)]
    await rig.run()
    rows = {each["unique_id"]: each for each in rig.query("SELECT * FROM media_items")}
    assert rows["high"]["plan_status"] == "in_plan_single"
    assert (rows["low"]["plan_status"], rows["low"]["superseded_by"]) == ("replaced", "high")


async def test_a_reference_whose_download_failed_keeps_its_place_to_be_tried_again(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    rig.mark(rig.media()["a"]["unique_id"], status="error")
    await rig.run()
    assert (rig.media()["a"]["status"], rig.media()["a"]["plan_status"]) == ("error", "in_plan_single")


async def test_a_reference_whose_page_gave_no_answer_this_time_keeps_its_place_and_its_file(rig):
    """T-5: an unsure answer is not a "no": the reference is not swapped for a worse one and swapped back."""
    rig.world.rows = [rig.row("a", 1, 1, player="vk", height=1080), rig.row("b", 1, 1, player="kodik", height=720)]
    await rig.run()
    first = rig.downloaded("a")
    changes = rig.bus.subscribe("scan.reference.changed")
    rig.world.rows = [rig.row("a", 1, 1, player="vk", height=None, state=RowState.NOT_OBTAINED),
                      rig.row("b", 1, 1, player="kodik", height=720)]
    result = await rig.run()
    kept = (await rig.output())[0]
    assert (kept.row_key, kept.mark, kept.height) == ("a", MARK.BY_LEVEL, 1080)
    assert result["changed"] is False and drain(changes) == []
    media = rig.media()
    assert list(media) == ["a"] and media["a"]["unique_id"] == first and media["a"]["plan_status"] == "in_plan_single"
    assert media["a"]["resolution"] == 1080 and media["a"]["final_filename"] == "e01.mp4"
    # The row is gone for good: the ordinary choice takes the other one.
    rig.world.rows = [rig.row("a", 1, 1, player="vk", height=None, state=RowState.ABSENT),
                      rig.row("b", 1, 1, player="kodik", height=720)]
    await rig.run()
    assert (await rig.output())[0].row_key == "b"
    assert changes_of(drain(changes)) == [change(rig, "b", 1, 1, player="kodik", height=720)]


async def test_the_tag_of_the_file_is_the_group_of_the_voice_over_else_its_name_as_it_came(rig):
    rig.world.rows = [rig.row("g", 1, 1, voice="AniLibria"),
                      rig.row("n", 1, 2, voice="Неизвестная озвучка 1", name="Red Head Sound"),
                      rig.row("u", 1, 3, voice="Неизвестная озвучка 2", name="")]
    await rig.run()
    media = rig.media()
    assert [media[key]["voiceover_tag"] for key in ("g", "n", "u")] == ["AniLibria", "Red Head Sound", None]


# --- the pick of the user; what vanishes (S-7, S-11, S-14) --------------------------------------------------------------

async def test_the_pick_of_a_voice_over_that_has_gone_leaves_the_episode_without_a_reference(rig):
    """S-7 (M-22): nothing stands in for the picked voice-over, and the row of the plan it left is dropped."""
    rig.world.rows = [rig.row("a", 1, 1, voice="Ruchime")]
    await rig.run()
    gone = ReferenceChoice(group="AniLibria", source_id=rig.source_ids[0], player="kodik").model_dump()
    rig.world.settings = [episode_settings(1, 1, lever(REFERENCE_CHOICE, gone, Level.EPISODE))]
    await rig.run()
    reference = (await rig.output())[0]
    assert reference.mark == MARK.NONE and reference.source_id is None
    assert parse(reference.reason) == {"code": "reference_voice_missing", "params": {
        "voice": "AniLibria", "season": 1, "episode": 1}}
    assert rig.media() == {}


async def test_the_row_that_was_the_reference_vanishes_from_the_table(rig):
    """S-11: the reference is counted again, the next candidate stands in, and when there is none the episode says so."""
    rig.world.rows = [rig.row("best", 1, 1, height=1080), rig.row("next", 1, 1, height=720, player="kodik")]
    await rig.run()
    changes = rig.bus.subscribe("scan.reference.changed")
    rig.world.rows = [rig.row("next", 1, 1, height=720, player="kodik")]
    await rig.run()
    assert [each.row_key for each in await rig.output()] == ["next"] and list(rig.media()) == ["next"]
    rig.world.rows = []
    await rig.run()
    assert await rig.output() == [] and rig.media() == {}
    assert marks(drain(changes)) == [(1, 1, "by_level"), (1, 1, "none")]


async def test_a_reset_of_the_setting_of_an_episode_recounts_its_reference_and_downloads_nothing(rig):
    """S-14: the episode goes back to its season; the reference is counted again from the event, without a download."""
    own = ReferenceChoice(group="AniLibria", source_id=rig.source_ids[0], player="kodik").model_dump()
    rig.world.rows = [rig.row("auto", 1, 1, height=1080), rig.row("own", 1, 1, height=480, player="kodik",
                                                                 voice="AniLibria")]
    rig.world.settings = [episode_settings(1, 1, lever(REFERENCE_CHOICE, own, Level.EPISODE))]
    await rig.run()
    assert (await rig.output())[0].mark == MARK.OWN and list(rig.media()) == ["own"]
    changes, plan = rig.bus.subscribe("scan.reference.changed"), rig.bus.subscribe("scan.plan.updated")
    downloads, voices = rig.bus.subscribe("downloads.dispatch"), rig.bus.subscribe("voices.dispatch")
    rig.world.settings = []
    rig.world.changed(rig.series_id, (1, 1))
    assert await next_change(changes) == change(rig, "auto", 1, 1)
    assert drain(plan) == [{"series_id": rig.series_id}]
    assert list(rig.media()) == ["auto"]
    assert downloads.queue.empty() and voices.queue.empty()


async def test_a_change_that_only_touches_the_summaries_is_not_acted_on(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    changes = rig.bus.subscribe("scan.reference.changed")
    asked = rig.world.order.count("composition.get")
    rig.world.changed(rig.series_id, summary_only=True)
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2)]
    rig.world.changed(rig.series_id, (1, 2))
    assert await next_change(changes) == change(rig, "b", 1, 2)
    assert rig.world.order.count("composition.get") == asked + 1


async def test_a_new_order_of_players_recounts_the_references_of_video_series(rig):
    rig.world.rows = [rig.row("vk", 1, 1, player="vk", height=1080), rig.row("kodik", 1, 1, player="kodik", height=1080)]
    await rig.run()
    assert (await rig.output())[0].row_key == "vk"
    changes = rig.bus.subscribe("scan.reference.changed")
    rig.world.priority = ["kodik", "vk"]
    rig.world.publish_event("voices.settings.changed", {})
    assert await next_change(changes) == change(rig, "kodik", 1, 1, player="kodik")
    assert (await rig.output())[0].row_key == "kodik"


async def test_a_player_left_out_of_the_choice_is_not_the_reference(rig):
    rig.world.rows = [rig.row("vk", 1, 1, player="vk", height=2160), rig.row("kodik", 1, 1, player="kodik", height=720)]
    rig.world.settings = [episode_settings(1, 1, lever(
        VARIANT_ALLOW, False, Level.SERIES, variant_target("Ruchime", rig.source_ids[0], "vk")))]
    await rig.run()
    assert (await rig.output())[0].row_key == "kodik"


# --- what the user switched off ---------------------------------------------------------------------------------------------

async def test_an_episode_switched_off_in_advance_is_created_switched_off_and_is_neither_planned_nor_taken(rig):
    rig.sql("UPDATE series SET ignored_episodes='{\"1\": [2, 3]}' WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row(f"e{n}", 1, n) for n in (1, 2, 3)]
    changes = rig.bus.subscribe("scan.reference.changed")
    await rig.run()
    media = rig.media()
    assert media["e1"]["is_ignored_by_user"] == 0 and media["e1"]["plan_status"] == "in_plan_single"
    for key in ("e2", "e3"):
        assert media[key]["is_ignored_by_user"] == 1 and media[key]["plan_status"] == "candidate"
    assert [(each.episode, each.mark) for each in await rig.output()] == [(1, MARK.BY_LEVEL), (2, MARK.OFF), (3, MARK.OFF)]
    assert marks(drain(changes)) == [(1, 1, "by_level"), (1, 2, "off"), (1, 3, "off")]


async def test_a_season_switched_off_is_neither_planned_nor_taken(rig):
    rig.sql("UPDATE series SET ignored_seasons='[2]' WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("s1", 1, 1), rig.row("s2", 2, 5)]
    await rig.run()
    assert {key: each["plan_status"] for key, each in rig.media().items()} == {
        "s1": "in_plan_single", "s2": "removed"}  # a season the user switched off is named as taken out by the configuration
    assert [(each.season, each.episode, each.mark) for each in await rig.output()] == [
        (1, 1, MARK.BY_LEVEL), (2, 5, MARK.OFF)]


async def test_a_row_of_the_plan_the_user_switches_off_turns_its_slot_off_and_returns_with_it(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    unique_id = rig.media()["a"]["unique_id"]
    changes = rig.bus.subscribe("scan.reference.changed")
    await rig.probe.request("scan.item.set_ignored", {"unique_id": unique_id, "is_ignored": True}, timeout=STUCK_S)
    assert changes_of(drain(changes)) == [off(rig, 1, 1)]
    assert [(each.mark, each.source_id) for each in await rig.output()] == [(MARK.OFF, None)]
    await rig.probe.request("scan.item.set_ignored", {"unique_id": unique_id, "is_ignored": False}, timeout=STUCK_S)
    assert changes_of(drain(changes)) == [change(rig, "a", 1, 1)]
    assert [each.mark for each in await rig.output()] == [MARK.BY_LEVEL]


async def switch(rig: "Rig", season: int, episode: int | None, switched: bool) -> dict:
    return await rig.probe.request("scan.output.switch", {
        "series_id": rig.series_id, "season": season, "episode": episode, "off": switched}, timeout=STUCK_S)


def memory_of(rig: "Rig") -> tuple[str, str]:
    [row] = rig.query("SELECT ignored_seasons, ignored_episodes FROM series WHERE id=?", (rig.series_id,))
    return row["ignored_seasons"], row["ignored_episodes"]


async def test_an_episode_switched_off_by_the_user_is_remembered_by_number_and_its_rows_are_switched_off(rig):
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2)]
    await rig.run()
    changes = rig.bus.subscribe("scan.reference.changed")
    assert await switch(rig, 1, 2, True) == {"success": True}
    assert changes_of(drain(changes)) == [off(rig, 1, 2)]
    assert [each.mark for each in await rig.output()] == [MARK.BY_LEVEL, MARK.OFF]
    assert memory_of(rig) == ("[]", '{"1": [2]}')
    assert rig.media()["b"]["is_ignored_by_user"] == 1 and rig.media()["a"]["is_ignored_by_user"] == 0
    assert await switch(rig, 1, 2, False) == {"success": True}
    assert changes_of(drain(changes)) == [change(rig, "b", 1, 2)]
    assert memory_of(rig) == ("[]", "{}") and rig.media()["b"]["is_ignored_by_user"] == 0


async def test_an_episode_switched_off_in_advance_comes_back_when_the_user_switches_it_on(rig):
    rig.sql("UPDATE series SET ignored_episodes='{\"1\": [2]}' WHERE id=?", (rig.series_id,))
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2)]
    await rig.run()
    assert [each.mark for each in await rig.output()] == [MARK.BY_LEVEL, MARK.OFF]
    await switch(rig, 1, 2, False)
    assert [each.mark for each in await rig.output()] == [MARK.BY_LEVEL, MARK.BY_LEVEL]
    assert memory_of(rig)[1] == "{}" and rig.media()["b"]["is_ignored_by_user"] == 0


async def test_a_season_is_switched_off_by_the_list_of_the_series_and_switched_on_with_all_of_it(rig):
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2), rig.row("c", 2, 1)]
    await rig.run()
    await switch(rig, 1, 2, True)
    changes = rig.bus.subscribe("scan.reference.changed")
    await switch(rig, 1, None, True)
    assert marks(drain(changes)) == [(1, 1, "off")]
    assert memory_of(rig)[0] == "[1]"
    assert [(each.season, each.mark) for each in await rig.output()] == [(1, MARK.OFF), (1, MARK.OFF), (2, MARK.BY_LEVEL)]
    await switch(rig, 1, None, False)
    assert marks(drain(changes)) == [(1, 1, "by_level"), (1, 2, "by_level")]
    assert memory_of(rig) == ("[]", "{}")
    assert {key: each["is_ignored_by_user"] for key, each in rig.media().items()} == {"a": 0, "b": 0, "c": 0}


async def test_switching_off_takes_the_rows_out_of_the_plan_and_switching_on_puts_them_back(rig):
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2), rig.row("c", 2, 1)]
    await rig.run()
    updates = rig.bus.subscribe("scan.plan.updated")
    plan = lambda: {key: each["plan_status"] for key, each in rig.media().items()}  # noqa: E731
    assert plan() == {"a": "in_plan_single", "b": "in_plan_single", "c": "in_plan_single"}
    await switch(rig, 1, 2, True)
    assert plan() == {"a": "in_plan_single", "b": "candidate", "c": "in_plan_single"} and drain(updates)
    await switch(rig, 1, 2, False)
    assert plan()["b"] == "in_plan_single" and drain(updates)
    await switch(rig, 1, None, True)
    assert plan() == {"a": "removed", "b": "removed", "c": "in_plan_single"}
    await switch(rig, 1, None, False)
    assert set(plan().values()) == {"in_plan_single"}


async def test_a_row_the_user_switches_off_leaves_the_plan_at_once(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    unique_id = rig.media()["a"]["unique_id"]
    await rig.probe.request("scan.item.set_ignored", {"unique_id": unique_id, "is_ignored": True}, timeout=STUCK_S)
    assert rig.media()["a"]["plan_status"] == "candidate"
    await rig.probe.request("scan.item.set_ignored", {"unique_id": unique_id, "is_ignored": False}, timeout=STUCK_S)
    assert rig.media()["a"]["plan_status"] == "in_plan_single"


async def test_a_series_the_catalog_does_not_have_cannot_be_switched(rig):
    with pytest.raises(BusRequestError):
        await rig.probe.request("scan.output.switch", {"series_id": 999, "season": 1, "off": True}, timeout=STUCK_S)


# --- the state is the database's -------------------------------------------------------------------------------------------

async def test_after_a_restart_the_output_is_there_without_asking_the_layout(rig):
    rig.world.rows = [rig.row("a", 1, 1), rig.row("b", 1, 2, 3)]
    await rig.run()
    before = [each.model_dump() for each in await rig.output()]
    again = await build(rig.db_path, (URL1,), series_id=rig.series_id, source_ids=rig.source_ids)
    try:
        assert [each.model_dump() for each in await again.output()] == before
        assert again.world.order == []
    finally:
        await again.runner.stop()


async def test_the_output_of_a_series_nothing_is_counted_for_is_empty(rig):
    assert await rig.output() == []


async def test_a_series_that_is_deleted_takes_its_references_and_plan_with_it(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.run()
    purged = rig.bus.subscribe("series.purged")
    await rig.probe.request("catalog.series.delete", {"series_id": rig.series_id}, timeout=STUCK_S)
    while (await next_event(purged))["owner"] != "scan":
        pass
    assert rig.query("SELECT * FROM scan_references") == [] and rig.query("SELECT * FROM media_items") == []


# --- after a change: a row taken out by the configuration is withdrawn, a row not brought is held (И-58) ----------

from core.contracts import Exclusion  # noqa: E402

AFTER = ("playable", "rule", "source", "no_number", "missing")
OTHER = ("none", "worse", "better")


def _expected_plan(after: str, other: str, downloaded: bool) -> str:
    """The law on its own: where the row of the earlier reference "a" (vk 720p) stands after the pass.

    "out" is any status that is not the plan and not `removed`: a row with no file has nothing to remove."""
    if after in ("missing", "no_number"):
        return "in_plan_single" if downloaded else "out"
    if after == "playable" and other != "better":
        return "in_plan_single"
    if not downloaded:  # a row with no file that stands for nothing is dropped
        return "out"
    if after == "playable":
        return "replaced"
    return "replaced" if other != "none" else "removed"


@pytest.mark.parametrize("downloaded", [True, False])
@pytest.mark.parametrize("other", OTHER)
@pytest.mark.parametrize("after", AFTER)
async def test_the_row_of_a_reference_follows_the_word_of_the_configuration_and_not_a_read_that_did_not_bring_it(
        db_path, after, other, downloaded):
    rig = await build(db_path, (URL1, URL2))
    try:
        rig.world.rows = [rig.row("a", 1, 1, height=720)]
        await rig.run()
        if downloaded:
            rig.downloaded("a")
        rows = []
        if after == "playable":
            rows.append(rig.row("a", 1, 1, height=720))
        elif after in ("rule", "source", "no_number"):
            rows.append(rig.row("a", None, None, height=720, excluded=True).model_copy(update={"exclusion": Exclusion(after)}))
        if other != "none":
            rows.append(rig.row("b", 1, 1, source=1, player="kodik", voice="AniLibria",
                                height=1080 if other == "better" else 720))
        rig.world.rows = rows
        await rig.run()
        media = rig.media()
        status = media["a"]["plan_status"] if "a" in media else None
        expected = _expected_plan(after, other, downloaded)
        if expected == "out":
            assert status not in ("in_plan_single", "removed")
        else:
            assert status == expected
        found = [each for each in await rig.output() if (each.season, each.episode) == (1, 1)]
        if expected == "in_plan_single":
            assert [(each.source_id, each.row_key) for each in found] == [(rig.source_ids[0], "a")]
        elif other != "none":
            assert [each.row_key for each in found] == ["b"] and media["b"]["plan_status"] == "in_plan_single"
        else:
            assert all(each.row_key is None for each in found)
    finally:
        await rig.runner.stop()



# --- a sync is one run: the series is busy until every work of it ends, and works by the snapshot (ЭТАП_11) ----------

from core.contracts import RUN_CHANGED, WORK_STATE  # noqa: E402


def runs_of(events: list[dict]) -> list[bool]:
    return [each["active"] for each in events]


def work_state(rig: "Rig", owner: str, active: bool) -> None:
    rig.world.tell_work(rig.series_id, owner, active)


async def test_a_sync_is_one_run_and_the_series_is_busy_until_every_work_it_gave_out_has_ended(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.work = {"downloads": True, "voices": True}
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run()
    assert runs_of(drain(runs)) == [True]
    assert [each["results_data"] for each in rig.query("SELECT results_data FROM scan_tasks")] != []
    with pytest.raises(BusRequestError, match="not_allowed"):
        await switch(rig, 1, 1, True)
    with pytest.raises(BusRequestError, match="not_allowed"):
        await rig.run()  # a second sync of a busy series is refused too

    work_state(rig, "downloads", False)
    await rig.bus.settled()
    assert drain(runs) == [] and rig.query("SELECT id FROM scan_tasks") != []  # voices still works
    work_state(rig, "voices", False)
    assert runs_of([await next_event(runs)]) == [False]
    assert rig.query("SELECT id FROM scan_tasks") == []
    assert await switch(rig, 1, 1, True) == {"success": True}  # free again


async def test_a_run_whose_executors_have_no_work_ends_with_its_pass(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run(download=False)
    assert runs_of(drain(runs)) == [True, False] and rig.query("SELECT id FROM scan_tasks") == []


async def test_a_recount_an_event_asks_for_while_the_run_is_open_is_done_when_the_run_ends(rig):
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.work = {"downloads": True}
    runs = rig.bus.subscribe(RUN_CHANGED)
    await rig.run()
    drain(runs)
    rig.world.order.clear()
    rig.world.rows = [rig.row("a", 1, 2)]
    rig.probe.publish_event("composition.changed", {"series_id": rig.series_id, "rev": 2, "episodes": []})
    await rig.bus.settled()
    assert "composition.get" not in rig.world.order  # the plan stays the one the sync found
    assert [(each.season, each.episode) for each in await rig.output()] == [(1, 1)]
    updates = rig.bus.subscribe("scan.plan.updated")
    work_state(rig, "downloads", False)
    await next_event(updates)
    assert [(each.season, each.episode) for each in await rig.output()] == [(1, 2)]


async def test_a_run_found_open_after_a_restart_waits_for_its_works_again(db_path):
    first = await build(db_path)
    first.world.rows = [first.row("a", 1, 1)]
    first.world.work = {"voices": True}
    await first.run()
    series_id, source_ids = first.series_id, first.source_ids
    await first.runner.stop()

    again = await build(db_path, series_id=series_id, source_ids=source_ids)
    try:
        again.world.work = {"voices": True}
        runs = again.bus.subscribe(RUN_CHANGED)
        again.probe.publish_event("system.started", {})
        assert runs_of([await next_event(runs)]) == [True]
        with pytest.raises(BusRequestError, match="not_allowed"):
            await switch(again, 1, 1, True)
        assert "composition.get" not in again.world.order  # the pass is not done again: only the works are waited for
        work_state(again, "voices", False)
        assert runs_of([await next_event(runs)]) == [False]
        assert again.query("SELECT id FROM scan_tasks") == []
    finally:
        await again.runner.stop()


async def test_a_run_is_not_closed_while_an_executor_has_not_answered_however_the_events_of_another_come(rig):
    """Н-4: downloads answers «working», voices' answer is on its way; downloads ends meanwhile. The run stays open until
    voices has answered, and its «working» keeps it open."""
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.work = {"downloads": True, "voices": True}
    gate = rig.world.work_gates["voices"] = asyncio.Event()
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run()
    await wait_until(lambda: f"voices.work.get {rig.series_id}" in rig.world.order, rig.bus, *rig.runner.modules)
    work_state(rig, "downloads", False)
    # The event is taken in (the bus cannot go quiet: voices' answer is held), and still nothing closes.
    assert await wait_until(lambda: rig.scan._working[rig.series_id].seen.get("downloads") == rig.world.seq,
                            rig.bus, *rig.runner.modules)
    assert runs_of(drain(runs)) == [True] and rig.world.packs == 0  # voices has not answered: nothing closes
    gate.set()
    await running
    await rig.bus.settled()
    assert drain(runs) == [] and rig.world.packs == 0  # voices works
    work_state(rig, "voices", False)
    assert runs_of([await next_event(runs)]) == [False] and rig.world.packs == 1


async def test_an_answer_counted_before_an_event_does_not_undo_it(rig):
    """A reply and an event of one executor that cross: the state counted later is in force, so «working» counted before
    «done» does not leave the run open for ever."""
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.work = {"voices": True}
    gate = rig.world.work_gates["voices"] = asyncio.Event()
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run()
    await wait_until(lambda: f"voices.work.get {rig.series_id}" in rig.world.order, rig.bus, *rig.runner.modules)
    work_state(rig, "voices", False)  # counted after the reply that is on its way
    gate.set()
    await running
    await rig.bus.settled()
    assert runs_of(drain(runs)) == [True, False]
    assert rig.query("SELECT id FROM scan_tasks") == [] and rig.world.packs == 1


@pytest.mark.parametrize("failure", ["failed", "error"])
async def test_a_packing_that_fails_ends_the_run_as_failed_with_its_reason(rig, failure):
    """Н-6: the last step of the run failing is no success: the series is free, and the error of the series says why
    until the next sync."""
    rig.world.rows = [rig.row("a", 1, 1)]
    if failure == "failed":
        rig.world.pack_reply = {"packed": 2, "failed": 1, "unchanged": 0, "waiting": 0}
    else:
        rig.world.pack_error = "диск недоступен"
    runs = rig.bus.subscribe(RUN_CHANGED)
    flags = rig.bus.subscribe("series.status.contribution")
    await rig.run(download=False)
    assert runs_of(drain(runs)) == [True, False]
    carrier = rig.query("SELECT status, results_data FROM scan_tasks WHERE series_id=?", (rig.series_id,))
    assert [each["status"] for each in carrier] == ["error"]
    code = "sync_pack_failed" if failure == "failed" else "scan_step_failed"
    assert parse(json.loads(carrier[0]["results_data"])["error"])["code"] == code
    assert [each["flags"]["error"] for each in drain(flags) if each["source"] == "scan"][-1] is True
    rig.world.pack_reply, rig.world.pack_error = {"packed": 0, "failed": 0, "unchanged": 0, "waiting": 0}, None
    await rig.run(download=False)
    assert rig.query("SELECT * FROM scan_tasks WHERE series_id=?", (rig.series_id,)) == []


async def test_a_change_admitted_before_a_sync_holds_its_opening_and_none_is_admitted_after(rig):
    """Н-8: the check of a change and its write are not apart from the opening of a run: a sync waits for a change admitted
    before it, and a change asked once the sync is starting is refused."""
    rig.world.rows = [rig.row("a", 1, 1)]
    await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S)
    runs = rig.bus.subscribe(RUN_CHANGED)
    running = rig.start_run(download=False)
    # The sync is started and holds at its opening (the bus does not go quiet: its request is still being answered).
    assert await wait_until(lambda: rig.series_id in rig.scan._running, rig.bus, *rig.runner.modules)
    assert rig.world.order == [] and drain(runs) == []  # the sync waits for the change
    with pytest.raises(BusRequestError, match="not_allowed"):
        await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S)
    rig.probe.send_command("scan.edit.end", {"series_id": rig.series_id})
    await running
    assert runs_of(drain(runs)) == [True, False]
    assert await rig.probe.request("scan.edit.begin", {"series_id": rig.series_id}, timeout=STUCK_S) == {"ok": True}
    rig.probe.send_command("scan.edit.end", {"series_id": rig.series_id})


async def test_a_run_works_by_the_configuration_it_took_when_it_started(rig):
    """Н-5: the rules of the profiles and the settings of the voice-overs are taken once, when the sync starts, kept with
    its command and given to every step that would read them; a change saved meanwhile counts from the next run."""
    rig.world.rows = [rig.row("a", 1, 1)]
    rig.world.profiles_export = {1: [{"id": 1, "name": "серия", "conditions": []}]}
    rig.world.voices_export = {"voices.groups": '{"ПВА": ["ПВА ШОУ"]}'}
    rig.world.work = {"downloads": True}
    await rig.run()
    stored = json.loads(rig.query("SELECT results_data FROM scan_tasks")[0]["results_data"])["snapshot"]
    assert stored == {"profiles": {"1": [{"id": 1, "name": "серия", "conditions": []}]},
                      "voices": {"voices.groups": '{"ПВА": ["ПВА ШОУ"]}'}}
    rig.world.profiles_export = {1: [{"id": 1, "name": "другое", "conditions": []}]}  # saved during the run
    rig.world.voices_export = {}
    [recount] = rig.world.payloads["composition.recompute"]
    assert recount["snapshot"] == stored
    assert rig.world.payloads["voices.settings.get"][-1] == {"values": stored["voices"]}
    assert rig.world.payloads["voices.dispatch"][-1]["settings"] == stored["voices"]
    work_state(rig, "downloads", False)
    await rig.bus.settled()
    rig.world.work["downloads"] = True
    await rig.run()
    again = json.loads(rig.query("SELECT results_data FROM scan_tasks")[0]["results_data"])["snapshot"]
    assert again["profiles"] == {"1": [{"id": 1, "name": "другое", "conditions": []}]} and again["voices"] == {}
