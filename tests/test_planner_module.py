"""The planner of the service (modules/planner) through the models of the simulator: the module itself on a bus, with its
own database, its executor, its journal and its files; around it the models of its neighbours — the composition gives the
rows of a site (`Host`: real files made by ffmpeg, the episode in their content), downloads fetch them with the service's
own downloader, the alignment service is the model on the real protocol (`Daemon`) behind the service's own client. The
checks read the files back: every sound of a file is the episode of its picture, the file carries the table."""
import asyncio
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field

import pytest

from core import BaseModule, Bus, Runner
from core import container as mkv
from core.contracts import (VOICE_TAKE, CompositionReply, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            RowState, group_target)
from core.db import Database
from core.envelope import Envelope
from core.plan.files import container_json, container_of
from modules.downloads import ytdlp
from modules.planner import PlannerModule
from plan_disk import Codec, Daemon, Host
from plan_world import truth_of
from schema_db import migrated
from settle import settle

REF = "AniStar"
SERIES = 7


@dataclass
class Site:
    rows: list[CompositionRow] = field(default_factory=list)
    settings: list[EpisodeSettings] = field(default_factory=list)


class Neighbours(BaseModule):
    """The models of the planner's neighbours over the bus."""

    name = "neighbours"

    def __init__(self, bus, root: str) -> None:
        self.root = root
        self.site = Site()
        self.codec = Codec()
        self.host = Host(os.path.join(root, "site"))
        self.daemon = Daemon(self.codec)
        self.library = os.path.join(root, "library")
        super().__init__(bus)

    def register(self) -> None:
        self.handle("catalog.series.get", self.on_series)
        self.handle("composition.get", self.on_composition)
        self.handle("voices.settings.get", self.on_voices)
        self.handle("conform.workspace", self.on_workspace)
        self.handle("downloads.fetch", self.on_fetch, concurrent=True)
        self.handle("conform.submit", self.on_submit, concurrent=True)
        self.handle("conform.reverse", self.on_reverse, concurrent=True)
        self.handle("conform.job.find", self.on_find)

    async def on_series(self, env: Envelope) -> dict:
        return {"id": SERIES, "name": "Сериал", "kind": "video", "save_path": self.library, "ignored_seasons": "[]",
                "ignored_episodes": "{}", "sources": [
                    {"id": source, "series_id": SERIES, "url": f"https://site/{source}", "source_type": "web_video"}
                    for source in (1, 2, 3, 4)]}

    async def on_composition(self, env: Envelope) -> dict:
        return CompositionReply(series_id=SERIES, rev=1, rows=self.site.rows, settings=self.site.settings).model_dump()

    async def on_voices(self, env: Envelope) -> dict:
        return {"priority": ["vk", "cvh", "kodik"], "player_groups": {}}

    async def on_workspace(self, env: Envelope) -> dict:
        if env.payload.get("dub_folder"):
            return {"out_dir": os.path.join(env.payload["dub_folder"], "out"),
                    "cache_dir": os.path.join(env.payload["dub_folder"], "cache")}
        return {"downloads": os.path.join(self.root, "work", "downloads"), "conform": os.path.join(self.root, "work", "conform")}

    async def on_fetch(self, env: Envelope) -> None:
        job = env.payload
        os.makedirs(os.path.dirname(job["path"]), exist_ok=True)
        done, failure = await ytdlp.download(job["url"], job["path"], _quiet)
        self.publish_event("downloads.fetch.finished", {**job, "ok": done, "error": failure or "", "transient": False})

    async def _finished(self, job: dict) -> None:
        client = self.daemon.client()
        try:
            job = await client.job(job["id"])
        finally:
            await client.close()
        self.publish_event("conform.job.finished", {"job_id": job["id"], "kind": job["kind"], "status": job["status"],
                                                    "error": None, "results": job["results"]})

    async def on_submit(self, env: Envelope) -> dict:
        body = {key: env.payload[key] for key in ("ref", "dubs", "dub_atracks", "out_dir", "label")
                if key in env.payload}
        if "ref_atrack" in env.payload:
            body["ref_atrack"] = env.payload["ref_atrack"]
        os.makedirs(body["out_dir"], exist_ok=True)
        client = self.daemon.client()
        try:
            job = await client.enqueue(body)
        finally:
            await client.close()
        asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self._finished(job)))
        return {"outcome": "accepted", "job_id": job["id"], "retry": False}

    async def on_reverse(self, env: Envelope) -> dict:
        payload = env.payload
        os.makedirs(payload["out_dir"], exist_ok=True)
        body = {"compilation": payload["compilation"], "episodes": [each["path"] for each in payload["episodes"]],
                "dub_atracks": [each["atrack"] for each in payload["episodes"]], "out_dir": payload["out_dir"],
                "extend_edges": True, "label": payload.get("label")}
        client = self.daemon.client()
        try:
            jobs = await client.reverse(body)
        finally:
            await client.close()
        for job in jobs:
            asyncio.get_running_loop().call_soon(lambda job=job: asyncio.ensure_future(self._finished(job)))
        key = os.path.basename(os.path.dirname(payload["episodes"][0]["path"])).rsplit("#", 1)[-1]
        return {"outcome": "accepted", "jobs": {key: jobs[0]["id"]}, "retry": False}

    async def on_find(self, env: Envelope) -> dict:
        return {"reachable": True, "job_id": None}

    # --- the site ------------------------------------------------------------------------------------------------

    def put(self, source_id: int, key: str, episode: int, *, group: str | None, player: str = "vk", height: int = 720,
            end: int | None = None, season: int = 1) -> None:
        truths = [truth_of(season, number) for number in range(episode, (end or episode) + 1)]
        name = f"{source_id}_{hashlib.md5(key.encode()).hexdigest()[:10]}.mp4"
        path = os.path.join(self.root, "site", name)
        if not os.path.exists(path):
            self.codec.make(path, truths)
        self.drop(source_id, key)
        self.site.rows.append(CompositionRow(
            series_id=SERIES, source_id=source_id, row_key=key, season=season, episode_start=episode,
            episode_end=end or episode, voice_group=group, voice_label=group or "", player=player, heights=[height],
            state=RowState.PLAYABLE, link=self.host.url(name), video=key))

    def drop(self, source_id: int, key: str) -> None:
        self.site.rows = [row for row in self.site.rows if (row.source_id, row.row_key) != (source_id, key)]

    def take_off(self, group: str, episodes) -> None:
        self.site.settings = [EpisodeSettings(season=1, episode=number, levers=[
            EffectiveLever(lever=VOICE_TAKE, target=group_target(group), value=False, level=Level.SERIES)])
            for number in episodes]

    def close(self) -> None:
        self.host.close()


async def _quiet(_data) -> None:
    return None


@pytest.fixture
async def world(tmp_path):
    db_path = migrated(tmp_path / "test.db")
    for name in ("site", "library", "work"):
        os.makedirs(tmp_path / name, exist_ok=True)
    bus = Bus()
    neighbours = Neighbours(bus, str(tmp_path))
    planner = PlannerModule(bus, Database(db_path))
    runner = Runner(bus, [neighbours, planner])
    await runner.start()
    yield neighbours, planner, db_path
    await runner.stop()
    neighbours.close()


async def sync(world, download: bool = True) -> dict:
    """One sync; the view it answers is the plan the sync froze, as the window is told when the sync starts (a sync
    without download tells nothing: the view is the plan as the tables give it)."""
    neighbours, planner, _ = world
    told = planner.bus.subscribe("planner.series.changed")
    assert (await planner.request("planner.sync", {"series_id": SERIES, "download": download}))["accepted"]
    await settle(planner.bus, planner, neighbours, seconds=120)
    while not told.queue.empty():
        view = (await told.queue.get()).payload
        if view["running"]:
            return view
    return await planner.request("planner.series.get", {"series_id": SERIES})


def table(db_path: str) -> dict[str, dict]:
    with sqlite3.connect(db_path) as conn:
        return {name: json.loads(data) for name, data in conn.execute(
            "SELECT name, data FROM plan_assets WHERE series_id=?", (SERIES,))}


async def check(world) -> dict[str, dict]:
    """The library is the table; every sound of a file is the episode of its picture; every file carries the table."""
    neighbours, _, db_path = world
    stored = table(db_path)
    files = sorted(name for name in os.listdir(neighbours.library) if not name.startswith("."))
    assert files == sorted(stored), (files, sorted(stored))
    for name in files:
        path = os.path.join(neighbours.library, name)
        held = await mkv.contents(path)
        picture = neighbours.codec.picture(path)
        for track in held.audio:
            assert neighbours.codec.sound(path, track.index) == picture, f"{name}: a sound of another episode"
        assert await mkv.provenance_of(path) == stored[name], f"{name}: its provenance is not the table"
        assert container_json(container_of(stored[name])) == stored[name]
    return stored


def episode(neighbours: Neighbours, number: int, voices: int) -> None:
    neighbours.put(1, f"ref{number}", number, group=REF)
    for each in range(1, voices + 1):
        neighbours.put(2, f"g{each}e{number}", number, group=f"G{each}", player="kodik")


async def test_an_episode_is_downloaded_aligned_and_built_by_the_service(world):
    neighbours, _, _ = world
    episode(neighbours, 1, 2)
    view = await sync(world)
    stored = await check(world)
    assert len(stored) == 1 and [track["snapshot"]["group"] for track in next(iter(stored.values()))["tracks"]] == ["G1", "G2"]
    assert not view["failures"] and {each["kind"] for each in view["episodes"][0]["tracks"]} == {"download"}
    now = await world[1].request("planner.series.get", {"series_id": SERIES})
    assert not now["running"] and {each["kind"] for each in now["episodes"][0]["tracks"]} == {"reuse"}


async def test_a_better_reference_realigns_the_offline_voice_out_of_the_old_file(world):
    neighbours, _, _ = world
    episode(neighbours, 1, 2)
    await sync(world)
    neighbours.drop(2, "g2e1")
    neighbours.put(3, "hd1", 1, group=REF, height=1080)
    view = await sync(world)
    stored = await check(world)
    assert [each["video"]["id"]["row_key"] for each in stored.values()] == ["hd1"]
    assert {each["voice"]: each["kind"] for each in view["episodes"][0]["tracks"]} == {"G1": "download", "G2": "realign"}
    assert next(iter(stored.values()))["tracks"][1]["dub"]["row_key"] == "g2e1"


async def test_a_cut_out_of_a_compilation_by_the_service(world):
    neighbours, _, _ = world
    neighbours.put(1, "single2", 2, group=REF, player="cvh")
    neighbours.put(4, "comp", 1, end=3, group=REF, height=2160)
    await sync(world)
    stored = await check(world)
    assert [each["video"]["cut_of"]["row_key"] for each in stored.values()] == ["comp"]


async def test_delete_of_the_only_video_waits_and_a_voice_taken_off_goes(world):
    neighbours, planner, _ = world
    episode(neighbours, 1, 2)
    await sync(world)
    name = next(iter(table(world[2])))
    for key in ("ref1", "g1e1", "g2e1"):
        neighbours.drop(1 if key == "ref1" else 2, key)
    await planner.request("planner.delete", {"series_id": SERIES, "kind": "video", "name": name})
    view = await sync(world)
    assert list(table(world[2])) == [name] and view["commands"] == [{"kind": "video", "name": name, "dub": None}]
    neighbours.take_off("G1", [1])
    view = await sync(world)
    stored = await check(world)
    assert [track["snapshot"]["group"] for track in next(iter(stored.values()))["tracks"]] == ["G2"]


async def test_a_sync_without_download_does_no_work(world):
    neighbours, _, _ = world
    episode(neighbours, 1, 1)
    view = await sync(world, download=False)
    assert table(world[2]) == {} and not os.listdir(neighbours.library) and view["episodes"]


async def test_a_file_made_before_the_planner_is_read_in_the_order_of_its_tracks_and_not_built_again(world):
    """A file the migration carried over keys its tracks by its own tags and was put in the table in an order of the
    records: the order of the file is the order of the table, so a file that already is its goal stays as it is."""
    neighbours, _, db_path = world
    episode(neighbours, 1, 2)
    await sync(world)
    (name, data), = table(db_path).items()
    path = os.path.join(neighbours.library, name)
    made = os.stat(path).st_mtime_ns
    for track in data["tracks"]:
        dub = track["dub"]
        track["tag"] = f"{dub['source_id']}:{dub['row_key']}:{dub['video']}"  # the key the file's tags hold
    data["tracks"].reverse()
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE plan_assets SET data=? WHERE series_id=? AND name=?", (json.dumps(data), SERIES, name))
    await sync(world)
    assert os.stat(path).st_mtime_ns == made
    assert [track["snapshot"]["group"] for track in table(db_path)[name]["tracks"]] == ["G1", "G2"]
