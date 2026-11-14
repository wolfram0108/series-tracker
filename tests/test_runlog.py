"""The dump of a run (modules/runlog): every envelope and every line of the log between the opening and the closing of a
run of a series, in order, in a file of its own; nothing outside a run; old dumps go past the number kept. A read of a
page outside the run of a series is a run of its own."""
import asyncio
import gzip
import json
import logging
import os

from core import Bus, Runner
from core.contracts.release_table import TABLE_TRACE
from core.contracts.work import RUN_CHANGED
from core.envelope import Envelope
from modules.runlog import RunlogModule


def lines(path: str) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as dump:
        return [json.loads(line) for line in dump if line.strip()]


def dumps(folder, series_id: int) -> list[str]:
    where = os.path.join(folder, str(series_id))
    return sorted(os.path.join(where, name) for name in os.listdir(where)) if os.path.isdir(where) else []


def run_changed(series_id: int, active: bool) -> Envelope:
    return Envelope(topic=RUN_CHANGED, kind="event", payload={"series_id": series_id, "active": active, "seq": 1})


async def test_a_run_is_dumped_whole_in_order_and_nothing_outside_it(tmp_path):
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path))
    runner = Runner(bus, [module])
    await runner.start()
    try:
        bus.publish(Envelope(topic="before.run", kind="event", payload={"n": 0}))
        bus.publish(run_changed(5, True))
        bus.publish(Envelope(topic="downloads.fetch", kind="command", payload={"url": "https://site/e1", "key": (1, 2)}))
        logging.getLogger("planner").info("на петле %d", 1)
        await asyncio.get_running_loop().run_in_executor(None, lambda: logging.getLogger("conform").error("из потока"))
        bus.publish(Envelope(topic="conform.submit", kind="query", payload={"job": "a"}, reply_to="reply.planner"))
        bus.publish(run_changed(5, False))
        bus.publish(Envelope(topic="after.run", kind="event", payload={}))
        logging.getLogger("planner").info("после прогона")
        await module.written()
    finally:
        await runner.stop()
    (path,) = dumps(tmp_path, 5)
    got = lines(path)
    assert [(each["e"], each.get("topic") or each.get("message")) for each in got] == [
        ("open", None), ("bus", RUN_CHANGED), ("bus", "downloads.fetch"), ("log", "на петле 1"), ("log", "из потока"),
        ("bus", "conform.submit"), ("bus", RUN_CHANGED), ("close", None)]
    assert got[2]["payload"] == {"url": "https://site/e1", "key": [1, 2]} and got[2]["kind"] == "command"
    assert got[5]["reply_to"] == "reply.planner" and got[4]["group"] == "conform" and got[4]["level"] == "ERROR"
    assert got[-1]["stopped"] is False


async def test_a_secret_that_goes_round_the_bus_in_a_run_is_in_its_dump_as_a_mask(tmp_path):
    """The password of a service the administrator saves or asks to see, a secret setting read: all of it passes the bus while a run is
    open, and none of it is written into the dump."""
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path))
    runner = Runner(bus, [module])
    await runner.start()
    try:
        bus.publish(run_changed(5, True))
        bus.publish(Envelope(topic="trackerauth.credentials.set", kind="query", payload={"service": "kinozal", "password": "p4ss"}))
        bus.publish(Envelope(topic="trackerauth.secret.get", kind="reply", payload={"secret": "s3cret"}))
        bus.publish(Envelope(topic="settings.value.get", kind="reply", payload={"key": "tmdb_token", "value": "t0ken"}))
        bus.publish(run_changed(5, False))
        await module.written()
    finally:
        await runner.stop()
    (path,) = dumps(tmp_path, 5)
    with gzip.open(path, "rt", encoding="utf-8") as dump:
        raw = dump.read()
    assert "p4ss" not in raw and "s3cret" not in raw and "t0ken" not in raw
    got = {each["topic"]: each["payload"] for each in lines(path) if each["e"] == "bus"}
    assert got["trackerauth.credentials.set"] == {"service": "kinozal", "password": "***"} and got["settings.value.get"]["value"] == "***"


async def test_a_run_the_process_stops_in_is_closed_as_stopped_and_old_dumps_go(tmp_path):
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path), keep=2)
    runner = Runner(bus, [module])
    await runner.start()
    try:
        for _ in range(3):
            bus.publish(run_changed(9, True))
            bus.publish(run_changed(9, False))
            await module.written()
        bus.publish(run_changed(9, True))
        bus.publish(Envelope(topic="x", kind="event", payload={}))
    finally:
        await runner.stop()
    kept = dumps(tmp_path, 9)
    assert len(kept) == 2
    last = lines(kept[-1])
    assert [each["e"] for each in last] == ["open", "bus", "bus", "close"] and last[-1]["stopped"] is True


async def test_without_a_folder_nothing_is_written_and_the_bus_is_not_looked_at(tmp_path):
    bus = Bus()
    module = RunlogModule(bus, None)
    runner = Runner(bus, [module])
    await runner.start()
    try:
        bus.publish(run_changed(1, True))
        await module.written()
        assert module.runs == {} and not bus._taps
    finally:
        await runner.stop()


def traced(url: str, kind: str, **more) -> Envelope:
    return Envelope(topic=TABLE_TRACE, kind="event", payload={"url": url, "site": "fake", "kind": kind, **more})


async def test_a_read_of_a_page_outside_a_sync_is_dumped_whole_in_a_file_of_its_page(tmp_path):
    """A source added in a window, «Перечитать»: the read is a run of its own — every envelope and every line of the
    log from its beginning to its end, the end with what the read came to. Nothing outside it."""
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path))
    runner = Runner(bus, [module])
    await runner.start()
    url = "app://juzi/164312"
    try:
        bus.publish(Envelope(topic="before.read", kind="event", payload={}))
        bus.publish(traced(url, "begin", fresh=True))
        bus.publish(Envelope(topic="sources.table.progress", kind="event", payload={"url": url, "phase": "players"}))
        logging.getLogger("sources").info("плеер line:x, %s: попытка 1 — playable, статус 200, — (0.4 с)", url)
        bus.publish(traced(url, "end", ok=True, reason=None, seconds=1.5, rows=499))
        bus.publish(Envelope(topic="after.read", kind="event", payload={}))
        await module.written()
    finally:
        await runner.stop()
    folder = os.path.join(tmp_path, "reads", "juzi_164312")
    (name,) = os.listdir(folder)
    got = lines(os.path.join(folder, name))
    assert [(each["e"], each.get("topic") or each.get("message", "")[:13]) for each in got] == [
        ("open", ""), ("bus", TABLE_TRACE), ("bus", "sources.table.progress"), ("log", "плеер line:x,"),
        ("bus", TABLE_TRACE), ("close", "")]
    assert got[0]["url"] == url and got[-1] == {"t": got[-1]["t"], "e": "close", "url": url, "stopped": False}
    assert got[4]["payload"]["rows"] == 499


async def test_a_read_inside_the_sync_of_a_series_is_in_the_dump_of_that_sync_and_has_none_of_its_own(tmp_path):
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path))
    runner = Runner(bus, [module])
    await runner.start()
    try:
        bus.publish(run_changed(5, True))
        bus.publish(traced("https://site.test/p/1", "begin", fresh=False))
        bus.publish(traced("https://site.test/p/1", "end", ok=True, reason=None, seconds=1.0, rows=3))
        bus.publish(run_changed(5, False))
        await module.written()
    finally:
        await runner.stop()
    (path,) = dumps(tmp_path, 5)
    assert [each.get("topic") for each in lines(path) if each["e"] == "bus"] == [RUN_CHANGED, TABLE_TRACE, TABLE_TRACE, RUN_CHANGED]
    assert not os.path.exists(os.path.join(tmp_path, "reads"))
