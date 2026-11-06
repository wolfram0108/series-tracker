"""The mechanism row - task - file of downloads under every operation of the plan, at every step of the work, with every
failure of the world. The invariants are in `model_support.check`; a scenario is one point of the product
phase x operation x failure (and a few pairs of operations), explored on the real catalog, scan and downloads."""
import asyncio
import os
import time

import pytest

from core import BusRequestError, storage
from core.db import Database
from modules.downloads import DownloadsModule
from modules.downloads.repository import DownloadsRepository
from model_support import Harness, check
from scan_support import STUCK_S, migrated_db

A1, A2, B1, B2, C1 = ("https://v/a1", "https://v/a2", "https://v/b1", "https://v/b2", "https://v/c1")


@pytest.fixture(scope="module")
def template(tmp_path_factory):
    return migrated_db(tmp_path_factory.mktemp("template") / "template.db")


# --- the steps of the work the plan may change in the middle of --------------------------------------------------------

async def phase_0(h: Harness) -> None:
    """The first video is being fetched, the second waits for a place."""
    h.downloader.hold(A1)
    h.downloader.hold(B1)
    await h.scan()
    await h.until(lambda: sorted(task["status"] for task in h.tasks()) == ["downloading", "pending"], "phase 0")


async def phase_1(h: Harness) -> None:
    """The first video is whole in its work file and waits to be put under its name."""
    h.publish_gate.clear()
    h.downloader.hold(B1)
    await h.scan()
    await asyncio.wait_for(h.at_gate.wait(), STUCK_S)  # the work file is whole and waits at the gate: no event says it
    await h.until(lambda: any(name.startswith(".") for name in h.files()), "phase 1")


async def phase_2(h: Harness) -> None:
    """The first video is in the library, the second is being fetched."""
    h.downloader.hold(B1)
    await h.scan()
    await h.until(lambda: [(i["row_key"], i["status"]) for i in sorted(h.items(), key=lambda i: i["episode_start"])] == [
        ("a", "completed"), ("b", "downloading")], "phase 2")


async def phase_3(h: Harness) -> None:
    """Both videos are in the library."""
    await h.scan()
    await h.until(lambda: [i["status"] for i in h.items()] == ["completed", "completed"], "phase 3")
    await h.quiesce()


PHASES = {"p0": phase_0, "p1": phase_1, "p2": phase_2, "p3": phase_3}


# --- what the plan does ---------------------------------------------------------------------------------------------------

async def b_address(h):
    h.rows["b"] = (1, 2, B2)
    await h.scan()


async def b_back(h):
    h.rows["b"] = (1, 2, B1)
    await h.scan()


async def a_address(h):
    h.rows["a"] = (1, 1, A2)
    await h.scan()


async def a_back(h):
    h.rows["a"] = (1, 1, A1)
    await h.scan()


async def renumber_b(h):
    h.rows["b"] = (1, 3, B1)
    h.rows["c"] = (1, 2, C1)
    await h.scan()


async def swap(h):
    h.rows["a"] = (1, 2, A1)
    h.rows["b"] = (1, 1, B1)
    await h.scan()


async def drop_b(h):
    del h.rows["b"]
    await h.scan()


async def off_a(h):
    await h.switch(1, 1, True)


async def season_off(h):
    await h.switch(1, None, True)


async def on_a(h):
    await h.switch(1, 1, False)


async def season_on(h):
    await h.switch(1, None, False)


async def both_addresses(h):
    h.rows["a"] = (1, 1, A2)
    h.rows["b"] = (1, 2, B2)
    await h.scan()


async def renumber_b_new_address(h):
    h.rows["b"] = (1, 3, B2)
    h.rows["c"] = (1, 2, C1)
    await h.scan()


OPERATIONS = {"b_address": [b_address], "a_address": [a_address], "renumber_b": [renumber_b], "swap": [swap],
              "drop_b": [drop_b], "off_a": [off_a], "season_off": [season_off], "both_addresses": [both_addresses],
              "renumber_b_address": [renumber_b_new_address],
              "b_address_back": [b_address, b_back], "a_address_back": [a_address, a_back], "off_on": [off_a, on_a],
              "season_off_on": [season_off, season_on], "drop_back": [drop_b, b_back]}
SEQUENCES = {"b_address_back", "a_address_back", "off_on", "season_off_on", "drop_back"}  # returns to where they began


# --- what befalls the work ------------------------------------------------------------------------------------------------

def fail_none(h, monkeypatch):
    pass


def fail_replace_timeout(h, monkeypatch):
    """The replacement of the file does not answer in time and ends later."""
    real, used = storage.call, [False]

    def slow(src, dst):
        time.sleep(0.15)
        os.replace(src, dst)

    async def call(fn, *args, **kwargs):
        if fn is os.replace and h.armed and not used[0]:
            used[0] = True
            return await real(slow, *args, timeout=0.02, **kwargs)
        return await real(fn, *args, **kwargs)

    monkeypatch.setattr(storage, "call", call)


def fail_record_write(h, monkeypatch):
    """The write that records a finished file fails once."""
    real, used = Database.execute_many, [False]

    async def execute_many(self, statements):
        statements = list(statements)
        if h.armed and not used[0] and any("DELETE FROM download_tasks" in query for query, _ in statements) and any(
                "final_filename" in query for query, _ in statements):
            used[0] = True
            raise OSError("disk I/O error")
        return await real(self, statements)

    monkeypatch.setattr(Database, "execute_many", execute_many)


def fail_removal(h, monkeypatch):
    """The first removal of a work file is refused."""
    real, used = storage.remove_file, [False]

    def remove_file(path):
        if h.armed and not used[0] and os.path.basename(path).startswith("."):
            used[0] = True
            return False
        return real(path)

    monkeypatch.setattr(storage, "remove_file", remove_file)


def fail_release(h, monkeypatch):
    """The write that lets a row go of a file whose file has gone fails once: the process stops between the two."""
    real, used = DownloadsRepository.release_file, [False]

    async def release_file(self, unique_id):
        if h.armed and not used[0]:
            used[0] = True
            raise OSError("disk I/O error")
        return await real(self, unique_id)

    monkeypatch.setattr(DownloadsRepository, "release_file", release_file)


FAILURES = {"none": fail_none, "replace_timeout": fail_replace_timeout, "record_write": fail_record_write,
            "removal": fail_removal, "release": fail_release, "restart": fail_none}


SCENARIOS = [(phase, op, fail) for phase in PHASES for op in OPERATIONS for fail in FAILURES
             if op not in SEQUENCES or fail in ("none", "restart", "replace_timeout")]


@pytest.mark.parametrize("phase, operation, failure", SCENARIOS, ids=[f"{p}-{o}-{f}" for p, o, f in SCENARIOS])
async def test_the_mechanism_holds_its_invariants(phase, operation, failure, template, tmp_path, monkeypatch):
    h = Harness(tmp_path, template)
    h.rows = {"a": (1, 1, A1), "b": (1, 2, B1)}
    real = DownloadsModule._publish

    async def gated(self, *args, **kwargs):
        h.at_gate.set()
        await h.publish_gate.wait()
        return await real(self, *args, **kwargs)

    monkeypatch.setattr(DownloadsModule, "_publish", gated)
    FAILURES[failure](h, monkeypatch)
    await h.up()
    try:
        await PHASES[phase](h)
        h.armed = True
        for step in OPERATIONS[operation]:
            await step(h)
        if failure == "restart":
            await h.restart()
        h.downloader.let_go()
        h.publish_gate.set()
        await h.quiesce()
        before = check(h, converged=False)
        for attempt in range(2):
            try:
                await h.scan()
                break
            except BusRequestError as exc:
                # The disk changes in a sync alone (I-59), so a write the world fails once fails that sync; the user
                # runs it again, and the next one finishes what the failed one left.
                # A write that does not answer in time (the replacement of a container) fails the sync the same way.
                if attempt or not any(word in str(exc) for word in ("OSError", "StorageTimeout")):
                    raise
                await h.quiesce()
        await h.quiesce()
        after = check(h, converged=True)
        assert not before and not after, {"before the next pass": before, "after the next pass": after}
    finally:
        h.downloader.let_go()
        h.publish_gate.set()
        await h.down()
