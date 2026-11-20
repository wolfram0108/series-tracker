"""The dump of a run (modules/runlog): every envelope and every line of the log between the opening and the closing of a
run of a series, in order, in a file of its own; nothing outside a run; old dumps go past the number kept. A read of a
page outside the run of a series is a run of its own."""
import gzip
import json
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


async def test_all_the_dumps_keep_within_the_budget_the_oldest_go_first_and_the_one_being_written_stays(tmp_path):
    """A sync of hours is a dump of a gigabyte: the dumps of every series and page together keep within the budget, the
    oldest going first, at the start and when a run opens; the dump being written is never removed."""
    old = tmp_path / "40"
    old.mkdir()
    for at, name in enumerate(("20260101T000000Z-1.jsonl.gz", "20260102T000000Z-1.jsonl.gz")):
        path = old / name
        path.write_bytes(b"x" * 600)
        os.utime(path, (1_000_000 + at, 1_000_000 + at))
    bus = Bus()
    module = RunlogModule(bus, str(tmp_path), budget=1000)
    runner = Runner(bus, [module])
    await runner.start()
    try:
        assert sorted(each.name for each in old.iterdir()) == ["20260102T000000Z-1.jsonl.gz"]
        bus.publish(run_changed(9, True))
        await module.written()
        assert sorted(each.name for each in old.iterdir()) == ["20260102T000000Z-1.jsonl.gz"] and len(dumps(tmp_path, 9)) == 1
        module.budget = 10
        bus.publish(run_changed(9, False))
        await module.written()
        bus.publish(run_changed(9, True))
        await module.written()
        # The 600 bytes go; of the dumps of 9 the one being written stays, the closed one goes.
        assert list(old.iterdir()) == [] and len(dumps(tmp_path, 9)) == 1
    finally:
        await runner.stop()
