"""A model of the mechanism row of the plan - task - file of downloads, explored on the real catalog, scan and downloads.

The world is two episodes of one series. What the plan does to the mechanism (a video changes its address, an episode is
renumbered, a video goes, an episode or a season is switched off, an address comes back) and what befalls the work
(a stop at any step, a replacement whose outcome is not known, a write that fails, a removal that is refused) are
operations. A scenario is a few of them in an order; after it the state must satisfy the invariants of `check`. The
invariants say what the mechanism guarantees whatever the operations were; a violation names the invariant and the rows.

L1 a name a row holds is a file with the video of that row; L2 a name is held by one row; L3 no whole file lies in the
library that no row holds; L4 no work file lies without a task; L5 a row is ready exactly when it holds a name; L6 no
task is left half-way; L7 the file of a row the configuration took out is gone; L8 (converged) every row the plan wants
has its file, under the name its number gives, with its video.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

from core import Bus, Runner
from core.db import Database
from core.envelope import Envelope
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.scan import ScanModule
from scan_support import STUCK_S, row
from series_factory import create_series, source
from settle import settle, wait_until
from test_downloads_module import FakeDownloader
from test_stage10_boundaries import URL, Neighbours, Probe

LIVE = ("pending", "downloading", "processing", "publishing")
IN_PLAN = ("in_plan_single", "in_plan_compilation")


class ScriptedDownloader(FakeDownloader):
    """Writes the address it was asked for into the file, so that whose video lies where can be told; an address can be held."""

    def __init__(self) -> None:
        super().__init__()
        self.held: dict[str, asyncio.Event] = {}

    def hold(self, url: str) -> None:
        self.held[url] = asyncio.Event()

    def let_go(self, url: str | None = None) -> None:
        for each in [url] if url else list(self.held):
            if each in self.held:
                self.held[each].set()

    async def __call__(self, url, path, on_progress, **kw):
        event = self.held.get(url)
        if event is not None:
            await event.wait()
        done = await super().__call__(url, path, on_progress, **kw)
        if done[0]:
            with open(path, "wb") as out:
                out.write(url.encode())
        return done


class Renaming(Neighbours):
    """The renaming of the system as downloads sees it: before a pass dispatches, the ready files are given the names their
    numbers give now, and the new names are written to the rows."""

    def __init__(self, bus, harness: "Harness") -> None:
        self.harness = harness
        super().__init__(bus)

    async def on_reprocess(self, env) -> dict:
        for unique_id, name in self.harness.rename_ready_files():
            self.send_command("downloads.item.set_filename", {"unique_id": unique_id, "filename": name})
        return {"ok": True}


class Harness:
    """The stack of one scenario over a database and a library that outlive a stop of the process."""

    def __init__(self, tmp: Path, template: str) -> None:
        self.tmp = tmp
        self.db_path = str(tmp / "model.db")
        shutil.copy(template, self.db_path)
        self.media = tmp / "media"
        self.rows: dict[str, tuple[int, int, str]] = {}  # row key -> (season, episode, address)
        self.series_id = self.source_id = 0
        self.publish_gate = asyncio.Event()
        self.publish_gate.set()
        self.downloader = ScriptedDownloader()
        self.armed = False  # the failures of a scenario happen after its step is reached, not on the way to it

    async def up(self) -> None:
        db = self.db = Database(self.db_path)
        if not self.series_id:
            created = await create_series(db, name="Т", name_en="T", site="x", save_path=str(self.media), sources=[
                source(url=URL, source_type="web_video", parser_profile_id=1)])
            self.series_id, self.source_id = created.series_id, created.source_ids[0]
        self.bus = Bus()
        self.world = Renaming(self.bus, self)
        self.world.settings_values["max_parallel_downloads"] = "1"
        self.world.sources = [(self.source_id, URL)]
        self.push_rows()
        self.probe = Probe(self.bus)
        self.downloads = DownloadsModule(self.bus, db, downloader=self.downloader)
        self.runner = Runner(self.bus, [self.world, CatalogModule(self.bus, db),
                                        ScanModule(self.bus, db, scheduler_tick=None, table_wait_s=STUCK_S),
                                        self.downloads, self.probe])
        await self.runner.start()
        self.bus.publish(Envelope(topic="system.started", kind="event"))  # what run.py says when every module is up

    async def down(self) -> None:
        await self.runner.stop()
        await self.db.drained()  # a write a stopped worker began lands before the next instance looks at the rows

    async def restart(self) -> None:
        await self.down()
        await self.up()

    def rename_ready_files(self) -> list[tuple[str, str]]:
        """Two steps, so that two files may swap names; returns (row, new name) of the files moved."""
        moves = [(item["unique_id"], item["final_filename"], name_of(item)) for item in self.items()
                 if item["status"] == "completed" and item["final_filename"] and item["final_filename"] != name_of(item)
                 and (self.media / item["final_filename"]).is_file() and item["plan_status"] in IN_PLAN]
        for _, old, _ in moves:
            os.replace(self.media / old, self.media / f"~{old}")
        for _, old, new in moves:
            os.replace(self.media / f"~{old}", self.media / new)
        return [(uid, new) for uid, _, new in moves]

    def push_rows(self) -> None:
        self.world.rows = [row(self.series_id, self.source_id, key, season, episode, link=link)
                           for key, (season, episode, link) in self.rows.items()]

    async def scan(self) -> None:
        self.push_rows()
        await self.probe.request("scan.series.run", {"series_id": self.series_id}, timeout=STUCK_S * 2)

    async def switch(self, season: int, episode: int | None, off: bool) -> None:
        payload = {"series_id": self.series_id, "season": season, "off": off}
        await self.probe.request("scan.output.switch", payload | ({} if episode is None else {"episode": episode}),
                                 timeout=STUCK_S)

    # --- what is there -----------------------------------------------------------------------------------------

    def query(self, sql: str, params=()) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(each) for each in conn.execute(sql, params)]

    def items(self) -> list[dict]:
        return self.query("SELECT * FROM media_items WHERE series_id=?", (self.series_id,))

    def tasks(self) -> list[dict]:
        return self.query("SELECT * FROM download_tasks WHERE series_id=?", (self.series_id,))

    def files(self) -> dict[str, bytes]:
        found = {}
        for directory, _, names in os.walk(self.media):
            for name in names:
                path = Path(directory) / name
                found[str(path.relative_to(self.media))] = path.read_bytes()
        return found

    async def until(self, condition, what: str) -> None:
        # The condition is looked at again after each step of the bus and each end of a task of the modules.
        if not await wait_until(condition, self.bus, *self.runner.modules, seconds=STUCK_S * 2):
            raise AssertionError((what, self.tasks(), [(i["unique_id"], i["status"], i["final_filename"]) for i in self.items()],
                                  sorted(self.files())))

    def waits_for_name(self, task: dict, items: list[dict]) -> bool:
        """A whole file that waits for the name a row the plan wants holds until renaming gives that file its new name."""
        if task["status"] != "publishing" or not task["save_path"]:
            return False
        name = os.path.relpath(task["save_path"], self.media).replace("\\", "/")
        return any(item["final_filename"] == name and item["unique_id"] != task["task_key"] and item["plan_status"] in IN_PLAN
                   and not item["is_ignored_by_user"] for item in items)

    async def quiesce(self) -> None:
        """Nothing is under way: no task is alive but those that wait for a name, the bus has taken apart every envelope
        and the modules run nothing in the background."""
        def calm() -> bool:
            items = self.items()
            return not [task for task in self.tasks() if task["status"] in LIVE and not self.waits_for_name(task, items)] \
                and not self.downloader.concurrent

        await wait_until(calm, self.bus, *self.runner.modules, seconds=STUCK_S * 2)
        await settle(self.bus, *self.runner.modules, seconds=STUCK_S * 2)


def video_of(item: dict) -> bytes:
    return (item.get("download_url") or item["source_url"]).encode()


def name_of(item: dict) -> str:
    return f"Серия {item['episode_start']:02d}.mp4"


def check(h: Harness, *, converged: bool) -> list[str]:
    """The violations of the invariants in the state of the harness; none means the mechanism is whole."""
    items, tasks, files = h.items(), h.tasks(), h.files()
    found: list[str] = []
    held: dict[str, str] = {}
    label = lambda item: f"{item['row_key']}#{item['episode_start']}[{item['status']}/{item['plan_status']}]"  # noqa: E731
    for item in items:
        name = item["final_filename"]
        if (item["status"] == "completed") != bool(name):
            found.append(f"L5 {label(item)} is {item['status']} and holds {name!r}")
        if not name:
            continue
        if name in held:
            found.append(f"L2 {name!r} is held by {held[name]} and {label(item)}")
        held[name] = label(item)
        if name not in files:
            found.append(f"L1 {label(item)} holds {name!r}, which is not there")
        elif files[name] != video_of(item):
            found.append(f"L1 {label(item)} holds {name!r}, which has {files[name]!r}, not {video_of(item)!r}")
        if item["is_ignored_by_user"] or item["plan_status"] == "removed":
            found.append(f"L7 {label(item)} was taken out and still holds {name!r}")
    live_keys = {task["task_key"] for task in tasks if task["status"] in LIVE}
    for name in files:
        if name.startswith("."):
            if not any(name.startswith(f".{key}.") for key in live_keys):
                found.append(f"L4 work file {name!r} has no task")
        elif name not in held:
            found.append(f"L3 {name!r} lies in the library and no row holds it")
    for task in tasks:
        if task["status"] in LIVE and not h.waits_for_name(task, items):
            found.append(f"L6 task of {task['task_key']} is left {task['status']}")
        elif task["status"] == "error":
            found.append(f"L6 task of {task['task_key']} is left in error: {task.get('error_message')}")
    if converged:
        for item in items:
            wanted = item["plan_status"] in IN_PLAN and not item["is_ignored_by_user"]
            if not wanted:
                continue
            if not item["final_filename"]:
                found.append(f"L8 {label(item)} is wanted and holds no file")
            elif item["final_filename"] != name_of(item):
                found.append(f"L8 {label(item)} is wanted and holds {item['final_filename']!r}, not {name_of(item)!r}")
    return found
