"""The dump of a run (modules/runlog): every envelope and every line of the log between the opening and the closing of a
run of a series, in order, in a file of its own; nothing outside a run; old dumps go past the number kept."""
import asyncio
import gzip
import json
import logging
import os

from core import Bus, Runner
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
