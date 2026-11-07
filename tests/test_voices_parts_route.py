"""An episode of two parts, from the plan to two tracks, through the real downloads, voices and conform bridge.

The neighbours that need a site and a layout are fakes (the catalog, the composition, the scan, the stream resolver and the
settings); the downloads of the voice-overs, the planning and the placing of the tracks, and the bridge to the alignment
service are the real modules. The service is a scenario: it takes jobs, names them, and ends them when the test says. The
reference of each part is a file of its own, and a track must lie beside the reference of its part."""
import asyncio
import itertools
import os
import sqlite3

import pytest

from conform_fakes import FakeClient
from core import BaseModule, Bus, Runner
from core.contracts import CompositionReply, CompositionRow, EpisodeSettings, Reference, ReferenceMark, RowState
from core.db import Database
from modules.conform import ConformModule
from modules.downloads import DownloadsModule
from modules.settings import SettingsModule
from modules.voices import VoicesModule
from series_factory import insert_series
from settle import wait_until
from test_downloads_module import FakeDownloader
from test_voices import Neighbours, restarted, touch  # noqa: F401

PARTS = (1, 2)
LABEL = "Dub"


class PartsWorld(Neighbours):
    """The neighbours of an episode of two parts: the references and the voice-over have a row for each part."""

    def register(self):
        self.handle("catalog.series.get", self.on_series)
        self.handle("composition.get", self.on_layout)
        self.handle("scan.output.get", self.on_output)
        self.handle("settings.value.get", self.on_setting)
        self.handle("settings.values.by_prefix", self.on_settings_by_prefix)
        self.handle("sources.video.stream", self.on_stream)

    async def on_stream(self, env):
        return {"handled": True, "stream_url": env.payload["url"], "headers": {}}

    def rows(self):
        found = []
        for part in self.parts:
            for player, label in (("cvh", "Ref"), ("kodik", LABEL)):
                found.append(CompositionRow(
                    series_id=7, source_id=1, row_key=f"{player}:{label}:{part}", season=1, episode_start=1, episode_end=1,
                    part=part, voice_group=label, voice_label=label, player=player, heights=[720], state=RowState.PLAYABLE,
                    link=f"https://site/{label}-p{part}"))
        return found

    def references_list(self):
        return [Reference(season=1, episode=1, part=part, mark=ReferenceMark.BY_LEVEL, source_id=1,
                          row_key=f"cvh:Ref:{part}", player="cvh", height=1080) for part in self.parts]


class Service(FakeClient):
    """The alignment service as a scenario: jobs get names, and end when the test says; the tracks it writes are real files."""

    def __init__(self, local: str, remote: str) -> None:
        super().__init__([("conform.snapshot", {"items": []})])
        self.local, self.remote = local, remote
        self.numbers = itertools.count(1)
        self.inbox: asyncio.Queue = asyncio.Queue()

    async def events(self):
        self.connections += 1
        yield self.scripts.pop(0)[0] if self.scripts else ("conform.snapshot", {"items": list(self.known.values())})
        while True:
            yield await self.inbox.get()

    async def enqueue(self, body):
        self.enqueued.append(body)
        job_id = f"job-{next(self.numbers)}"
        self.known[job_id] = {"id": job_id, "label": body.get("label"), "status": "queued", "body": body}
        return {"id": job_id, "status": "queued"}

    def end(self, job_id: str) -> None:
        job = self.known[job_id]
        body, results = job["body"], []
        for remote in body["dubs"]:
            name = os.path.basename(remote)
            out = os.path.join(body["out_dir"], os.path.splitext(name)[0] + ".flac")
            local = self.local + out[len(self.remote):]
            touch(local, b"flac")
            results.append({"dub": name, "ok": True, "skipped": False, "out_path": out, "suspect": False, "critical": [],
                            "warnings": [], "audio_resid_ms": 12.0, "error": None})
        job.update(status="done", results=results)
        self.inbox.put_nowait(("conform.changed", {"items": [job]}))


class Route:
    def __init__(self, tmp_path, db_path) -> None:
        self.tmp, self.db_path = tmp_path, db_path
        self.library = tmp_path / "library"
        self.work = tmp_path / "work"
        self.service = Service(str(self.work), "/share/work")

    async def build(self) -> "Route":
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
            insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", save_path=str(self.library),
                          url="https://site/title.html", source_type="web_video", parser_profile_id=1)
            for part in PARTS:
                name = f"Series s01e01 part{part}.mp4"
                conn.execute(
                    "INSERT INTO media_items (series_id, unique_id, source_title, season, episode_start, part, plan_status, "
                    "status, is_ignored_by_user, source_url, publication_date, slicing_status, is_available, final_filename) "
                    "VALUES (7, ?, ?, 1, 1, ?, 'in_plan_single', 'completed', 0, ?, '2026-01-01 00:00:00', 'none', 1, ?)",
                    (f"ref{part}", f"Часть {part}", part, f"https://site/Ref-p{part}", name))
                touch(str(self.library / name), f"reference {part}".encode())
            conn.commit()
        self.bus = Bus()
        db = Database(self.db_path)
        self.world = PartsWorld(self.bus, [], "", [], library=str(self.library), conform=False)
        self.world.parts = list(PARTS)
        self.world.settings["conform.work_dir"] = str(self.work)
        self.world.settings["conform.work_dir_remote"] = "/share/work"
        self.probe = Probe(self.bus)
        self.voices = VoicesModule(self.bus, db)
        self.runner = Runner(self.bus, [self.world, SettingsModule(self.bus, db), DownloadsModule(self.bus, db, downloader=FakeDownloader()),
                                        self.voices, ConformModule(self.bus, self.service, backoff_start=0.05), self.probe])
        await self.runner.start()
        return self

    async def rows(self):
        return sorted(await self.voices.repo.for_series(7), key=lambda row: row["part"])

    async def until(self, condition, what):
        # The condition is looked at again after each step of the bus and each end of a task of the modules.
        assert await wait_until(condition, self.bus, *self.runner.modules, seconds=10.0), \
            (what, await self.rows(), self.service.enqueued)

    async def aligning(self):
        rows = await self.rows()
        return len(rows) == len(self.world.parts) and all(row["status"] == "aligning" and row["job_id"] for row in rows)

    def reference_of(self, part: int) -> str:
        return str(self.library / f"Series s01e01 part{part}.mp4")

    def track_of(self, part: int) -> str:
        return os.path.splitext(self.reference_of(part))[0] + f".{LABEL}.ru.flac"


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def route(tmp_path, db_path):
    made = await Route(tmp_path, db_path).build()
    yield made
    await made.runner.stop()


async def into_alignment(route: Route) -> None:
    route.probe.send_command("voices.dispatch", {"series_id": 7})

    async def aligning():
        return await route.aligning()

    await route.until(aligning, "both parts reach the service")


@pytest.mark.asyncio
async def test_two_parts_of_one_voice_over_go_to_the_service_each_with_its_own_reference_and_get_each_a_track_beside_it(route):
    await into_alignment(route)
    rows = await route.rows()
    assert [row["part"] for row in rows] == [1, 2] and len({row["job_id"] for row in rows}) == 2  # a job for each part
    assert len(route.service.enqueued) == 2
    assert sorted(os.path.basename(body["dubs"][0]) for body in route.service.enqueued) == ["7_s01e01p1_Dub_kodik_site.mp4", "7_s01e01p2_Dub_kodik_site.mp4"]
    for body in route.service.enqueued:
        part = 1 if "p1" in os.path.basename(body["dubs"][0]) else 2
        # The reference the service is given is the video of that part, not of the other.
        assert open(body["ref"].replace("/share/work", str(route.work)), "rb").read() == f"reference {part}".encode()

    for job_id in sorted({row["job_id"] for row in rows}):
        route.service.end(job_id)

    async def placed():
        return [row["status"] for row in await route.rows()] == ["placed", "placed"]

    await route.until(placed, "both tracks placed")
    for part in PARTS:
        assert os.path.isfile(route.track_of(part)), part
    assert [row["output_path"] for row in await route.rows()] == [route.track_of(1), route.track_of(2)]


@pytest.mark.asyncio
async def test_a_part_whose_job_ends_while_the_process_is_down_is_placed_by_the_process_that_comes_up(route, db_path):
    await into_alignment(route)
    rows = await route.rows()
    await route.voices.stop()
    route.service.end(rows[0]["job_id"])  # the job of part 1 ends while no process of voices is up
    again = VoicesModule(route.bus, Database(db_path))
    route.voices = again
    await restarted(again)
    try:
        route.probe.publish_event("conform.daemon.up", {})

        async def first_placed():
            return [row["status"] for row in await route.rows()] == ["placed", "aligning"]

        await route.until(first_placed, "part 1 placed, part 2 still aligning")
        assert os.path.isfile(route.track_of(1)) and not os.path.exists(route.track_of(2))
        route.service.end(rows[1]["job_id"])

        async def both_placed():
            return [row["status"] for row in await route.rows()] == ["placed", "placed"]

        await route.until(both_placed, "part 2 placed")
        assert os.path.isfile(route.track_of(2)) and os.path.isfile(route.track_of(1))
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_part_that_leaves_the_plan_loses_its_work_and_its_track_and_the_other_part_keeps_both(route):
    await into_alignment(route)
    rows = await route.rows()
    route.service.end(rows[0]["job_id"])

    async def first_placed():
        return [row["status"] for row in await route.rows()] == ["placed", "aligning"]

    await route.until(first_placed, "part 1 placed")

    # Part 2 is no longer a part of the episode: its row is gone from the layout and its video from the library.
    route.world.parts = [1]
    os.remove(route.reference_of(2))
    route.probe.send_command("voices.settle", {"series_id": 7})  # the user's sync (I-59)

    async def only_first():
        return [(row["part"], row["status"]) for row in await route.rows()] == [(1, "placed")]

    await route.until(only_first, "part 2 left")
    assert os.path.isfile(route.track_of(1))

    async def cancelled():
        return rows[1]["job_id"] in route.service.cancelled

    await route.until(cancelled, "the job of part 2 was cancelled")
