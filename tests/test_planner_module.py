"""The planner of the service (modules/planner) through the models of the simulator: the module itself on a bus, with its
own database, its executor, its journal and its files; around it the models of its neighbours — the composition gives the
rows of a site (`Host`: real files made by ffmpeg, the episode in their content), downloads fetch them with the service's
own downloader, the alignment service is the model on the real protocol (`Daemon`) behind the service's own client. The
checks read the files back: every sound of a file is the episode of its picture, the file carries the table."""
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field

import pytest

from core import BaseModule, Bus, Runner
from core import container as mkv
from core.contracts import (VOICE_TAKE, CompositionReply, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            RowState, SyncScope, group_target)
from core.db import Database
from core.envelope import Envelope
from core.plan.files import BuildFailed, Library, container_json, container_of
from modules.conform.module import finished_view, job_label
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
        # The service keeps a job under way and never ends it while `holding`; `submitted` tells that one was given.
        self.holding = False
        self.submitted = asyncio.Event()
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
        self.handle("conform.job.get", self.on_job_get)
        self.handle("conform.job.keys", self.on_job_keys)
        self.handle("conform.settings.get", self.on_conform_settings)
        self.handle("conform.cancel", self.on_cancel)
        self.handle("downloads.withdraw", self.on_withdraw)
        self.handle("rules.format_filename", self.on_format_filename)

    async def on_series(self, env: Envelope) -> dict:
        return {"id": SERIES, "name": "Сериал", "save_path": self.library, "ignored_seasons": "[]",
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
        # Told as the conform module tells it: the service's results read by its own view.
        self.publish_event("conform.job.finished", {"job_id": job["id"], "kind": job["kind"], "status": job["status"],
                                                    "error": None, "results": finished_view(job, {})["results"]})

    async def on_submit(self, env: Envelope) -> dict:
        body = {key: env.payload[key] for key in ("ref", "dubs", "dub_atracks", "out_dir", "label")
                if key in env.payload}
        if "ref_atrack" in env.payload:
            body["ref_atrack"] = env.payload["ref_atrack"]
        body["label"] = job_label(body.get("label"), env.payload.get("key"))  # as the conform module labels a job
        os.makedirs(body["out_dir"], exist_ok=True)
        client = self.daemon.client()
        try:
            job = await client.enqueue(body)
        finally:
            await client.close()
        self.submitted.set()
        if not self.holding:
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
        mark = f"#{env.payload['key']}"
        found = next((job["id"] for job in self.daemon.jobs.values() if str(job.get("label") or "").endswith(mark)), None)
        return {"reachable": True, "job_id": found}

    async def on_job_get(self, env: Envelope) -> dict:
        job = self.daemon.jobs.get(env.payload["job_id"])
        if job is None:
            return {"job_id": env.payload["job_id"], "status": "gone", "results": []}
        return {"job_id": job["id"], "status": job["status"], "error": job.get("error"),
                "results": finished_view(job, {})["results"]}

    async def on_job_keys(self, env: Envelope) -> dict:
        return {"reachable": True, "keys": sorted({str(job.get("label") or "").rsplit("#", 1)[-1].strip()
                                                   for job in self.daemon.jobs.values() if "#" in str(job.get("label"))})}

    async def on_conform_settings(self, env: Envelope) -> dict:
        return {"clear_finished": False}

    async def on_cancel(self, env: Envelope) -> dict:
        self.daemon.jobs.pop(env.payload["job_id"], None)
        return {"done": True}

    async def on_format_filename(self, env: Envelope) -> dict:
        # The series of this model has no rule of names: the planner names its files by its own (core/plan name_of).
        raise LookupError("правила имён у сериала нет")

    async def on_withdraw(self, env: Envelope) -> dict:
        # The downloads of this model end as they are asked: none is under way once the sync aligns.
        return {"stopped": 0}

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


async def sync(world, download: bool = True, **scope) -> dict:
    """One sync (of the series, or of the `season` and `episode` named); the view it answers is the plan the sync froze, as the
    window is told when the sync starts (a sync without download tells nothing: the view is the plan as the tables give it)."""
    neighbours, planner, _ = world
    told = planner.bus.subscribe("planner.series.changed")
    assert (await planner.request("planner.sync", {"series_id": SERIES, "download": download, **scope}))["accepted"]
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


async def test_delete_of_an_offline_track_builds_the_file_without_it_sends_no_alignment_and_the_sync_is_traced(world):
    """Д-1 on real files: «Удалить» at a track no source gives any more takes it out of the container; the alignment
    service gets no job. The sync tells its plan with the inputs it was made of and every step with its outcome."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 2)
    await sync(world)
    name = next(iter(table(world[2])))
    neighbours.drop(2, "g2e1")
    dub = next(track["dub"] for track in table(world[2])[name]["tracks"] if track["dub"]["row_key"] == "g2e1")
    await planner.request("planner.delete", {"series_id": SERIES, "kind": "track", "name": name, "dub": dub})
    seen: list[Envelope] = []
    untap = planner.bus.tap(seen.append)
    try:
        await sync(world)
    finally:
        untap()
    stored = await check(world)
    assert len(stored) == 1, "the old file goes, it is not hidden"
    assert [track["snapshot"]["group"] for track in next(iter(stored.values()))["tracks"]] == ["G1"]
    assert not [each for each in seen if each.topic in ("conform.submit", "downloads.fetch")]
    traces = [each.payload for each in seen if each.topic == "planner.sync.trace"]
    json.dumps(traces)  # the dump takes them as they are
    (made,) = [each["data"] for each in traces if each["kind"] == "plan"]
    assert made["inputs"]["@"] == "Inputs" and {row["id"]["row_key"] for row in made["inputs"]["online"]} == {"ref1", "g1e1"}
    assert made["inputs"]["choices"]["delete"] and [each["name"] for each in made["inputs"]["containers"]] == [name]
    ops = {op["id"] for op in made["plan"]["ops"]}
    assert {each["data"]["op"] for each in traces if each["kind"] == "done"} == ops
    assert [each["data"]["applied"] for each in traces if each["kind"] == "apply"] == [True]


async def test_the_packing_is_told_container_by_container_and_what_waits_stays_in_the_picture(world):
    """The cards of the packing (pack_queue_update): every container a sync builds goes through its stages — queued, the
    build by ffmpeg with its progress, the result read back, the journal that puts it in place — with its tracks as numbers
    of the season; a done card goes with the end of the sync. A container whose step did not succeed is not built: its
    card stays failed after the sync, in the store, until the next sync of the series."""
    neighbours, planner, db_path = world
    episode(neighbours, 1, 1)
    told = planner.bus.subscribe("planner.pack.changed")
    await sync(world)
    pictures = []
    while not told.queue.empty():
        pictures.append((await told.queue.get()).payload["tasks"])
    seen = [card["status"] for picture in pictures for card in picture if card["episode"] == "s01e01"]
    assert seen[0] == "queued" and "build" in seen and seen[-1] == "done" and pictures[-1] == []
    assert seen.index("built") < seen.index("apply"), "a built container waits for the journal before it is put in place"
    done = [card for picture in pictures for card in picture if card["status"] == "done"][-1]
    assert [stage["state"] for stage in done["stages"]] == ["done", "done", "done", "done"]
    assert [(track["label"], track["kind"]) for track in done["tracks"]] == [(REF, "own"), ("G1", "new")]
    assert done["figures"]["steps_of"] and done["figures"]["steps_done"] == done["figures"]["steps_of"]
    assert any(card["figures"]["progress"] is not None for picture in pictures for card in picture if card["status"] == "build")
    episode(neighbours, 2, 1)
    neighbours.daemon.answers["Сериал s01e02 G1"] = "empty"  # the job did not come to the pair: the episode waits
    await sync(world)
    last = None
    while not told.queue.empty():
        last = (await told.queue.get()).payload["tasks"]
    assert [(card["episode"], card["status"], card["waited"]) for card in last] == [("s01e02", "failed", True)]
    assert [card.id for card in await planner.repo.pack_cards()] == [last[0]["id"]]



async def test_a_sync_of_an_episode_carries_out_its_group_alone_and_shows_the_series_whole(world):
    """The plan is of the series and is shown whole; what is carried out is the groups that hold the episode — the files of
    the other episodes are not made, not renamed, not removed."""
    neighbours, planner, db_path = world
    episode(neighbours, 1, 1)
    episode(neighbours, 2, 1)
    neighbours.put(1, "ref21", 1, group=REF, season=2)
    neighbours.put(2, "g1e21", 1, group="G1", player="kodik", season=2)
    view = await sync(world, season=1, episode=2)
    assert sorted(each["slot"] for each in view["episodes"]) == [[1, 1], [1, 2], [2, 1]]  # the window sees the series whole
    assert sorted(each["video"]["episode"] for each in table(db_path).values()) == [[1, 2]]
    stored = await check(world)
    assert len(stored) == 1
    # A season: its groups, and nothing of the others.
    await sync(world, season=2)
    assert sorted(each["video"]["episode"] for each in table(db_path).values()) == [[1, 2], [2, 1]]
    # The rest is the next sync of the series, which finds the files made before as they are.
    before = {name: data for name, data in table(db_path).items()}
    await sync(world)
    stored = await check(world)
    assert sorted(each["video"]["episode"] for each in stored.values()) == [[1, 1], [1, 2], [2, 1]]
    assert all(stored[name] == data for name, data in before.items())


async def test_a_sync_of_an_episode_cut_out_of_a_compilation_downloads_the_compilation_and_makes_that_episode_only(world):
    """What the episode needs is carried out whole — the download of the compilation, the cut laid on the picture of its
    single, the build — and the episodes the compilation is cut for otherwise, and one apart from it, wait for their sync."""
    neighbours, planner, db_path = world
    neighbours.put(1, "single1", 1, group=REF, player="cvh")
    neighbours.put(1, "single2", 2, group=REF, player="cvh")
    neighbours.put(4, "comp", 1, end=2, group=REF, height=2160)
    neighbours.put(1, "single4", 4, group=REF, player="cvh")
    await sync(world, season=1, episode=1)
    stored = await check(world)
    assert sorted(each["video"]["episode"] for each in stored.values()) == [[1, 1]]
    assert {each["video"]["cut_of"]["row_key"] for each in stored.values()} == {"comp"}
    await sync(world, season=1, episode=2)
    stored = await check(world)
    assert sorted(each["video"]["episode"] for each in stored.values()) == [[1, 1], [1, 2]]
    assert {each["video"]["cut_of"]["row_key"] for each in stored.values()} == {"comp"}


async def test_the_scope_of_a_sync_is_written_with_its_run_and_an_episode_without_its_season_is_no_scope(world):
    _, planner, _ = world
    await planner.repo.begin_run(SERIES, True, {"season": 1, "episode": 2})
    assert await planner.repo.runs() == [{"series_id": SERIES, "download": 1, "scope": {"season": 1, "episode": 2}}]
    await planner.repo.begin_run(SERIES, True)
    assert (await planner.repo.runs())[0]["scope"] is None
    await planner.repo.end_run(SERIES)
    with pytest.raises(ValueError):
        SyncScope.of({"episode": 2})
    assert SyncScope.of({}) is None and SyncScope.of({"season": 1}).holds([1, 7, 2]) and not SyncScope.of({"season": 1, "episode": 2}).holds([1, 3])


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


async def test_the_card_of_the_series_hears_what_the_sync_is_at_and_its_end(world):
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    told = planner.bus.subscribe("series.status.contribution")
    await sync(world)
    flags = []
    while not told.queue.empty():
        payload = (await told.queue.get()).payload
        assert payload["source"] == "planner" and payload["series_id"] == SERIES
        flags.append(payload["flags"])
    on = {name for each in flags for name, value in each.items() if value}
    assert on == {"downloading", "aligning", "packing", "ready"}
    # The end: nothing under way, a file of the series in the library, no work and no error.
    assert flags[-1] == {"downloading": False, "aligning": False, "packing": False, "ready": True, "waiting": False,
                         "error": False}


async def test_a_sync_the_user_stops_takes_its_jobs_off_the_service_before_the_series_is_free(world):
    """«Остановить синхронизацию»: every work of the run comes off — its job of the service too — and only then the end
    of the work of the series is told (ЭТАП_11: the stop takes off every work of the run, then the series is free)."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.holding = neighbours.daemon.hold = True  # the job stays under way until the stop
    assert (await planner.request("planner.sync", {"series_id": SERIES, "download": True}))["accepted"]
    await asyncio.wait_for(neighbours.submitted.wait(), 60)
    seen = planner.bus.subscribe("#")
    assert (await planner.request("planner.sync.cancel", {"series_id": SERIES}, timeout=60))["cancelled"]
    order = []
    while not seen.queue.empty():
        env = await seen.queue.get()
        if env.topic == "conform.cancel" or (env.topic == "work.series.state" and not env.payload["active"]):
            order.append(env.topic)
    assert neighbours.daemon.jobs == {}
    assert "conform.cancel" in order and order.index("work.series.state") > max(
        number for number, topic in enumerate(order) if topic == "conform.cancel"), order


async def test_an_episode_is_put_in_place_while_another_is_still_aligned_and_a_stop_keeps_it(world):
    """The work is of each episode: episode 1 (no voice-over) is built, put into the library and written to the table
    while the job of the service for episode 2 is still under way. «Остановить синхронизацию» then takes the work of
    episode 2 off; episode 1, applied by then, stays."""
    neighbours, planner, db_path = world
    episode(neighbours, 1, 0)
    episode(neighbours, 2, 1)
    neighbours.holding = neighbours.daemon.hold = True  # the job of episode 2 stays under way until the stop
    counted = planner.bus.subscribe("series.downloaded.changed")
    assert (await planner.request("planner.sync", {"series_id": SERIES, "download": True}))["accepted"]
    await asyncio.wait_for(neighbours.submitted.wait(), 60)
    assert (await asyncio.wait_for(counted.queue.get(), 60)).payload == {"series_id": SERIES, "count": 1}
    stored = await check(world)
    assert [each["video"]["id"]["row_key"] for each in stored.values()] == ["ref1"]
    assert (await planner.request("planner.work.get", {"series_id": SERIES}))["active"]

    assert (await planner.request("planner.sync.cancel", {"series_id": SERIES}, timeout=60))["cancelled"]
    assert neighbours.daemon.jobs == {}
    stored = await check(world)
    assert [each["video"]["id"]["row_key"] for each in stored.values()] == ["ref1"]
    assert not (await planner.request("planner.work.get", {"series_id": SERIES}))["active"]
    # The next sync does what the stop left: episode 2, and nothing of episode 1 again.
    neighbours.holding = neighbours.daemon.hold = False
    await sync(world)
    stored = await check(world)
    assert sorted(each["video"]["id"]["row_key"] for each in stored.values()) == ["ref1", "ref2"]


async def test_a_sync_written_down_is_work_of_the_series_before_the_start_takes_it_up(world):
    """`system.started` reaches the modules in no set order: a run that asks before the planner took its written sync
    up again hears it as work, not as an end."""
    _, planner, _ = world
    await planner.repo.begin_run(SERIES, True)
    state = await planner.request("planner.work.get", {"series_id": SERIES})
    assert state["owner"] == "planner" and state["active"]
    await planner.repo.end_run(SERIES)
    assert not (await planner.request("planner.work.get", {"series_id": SERIES}))["active"]


async def test_the_window_is_told_the_end_of_a_sync_with_the_view_the_tables_give_after_it(world):
    """A window lives by the packets it is pushed: the last one of a sync is the view after it, not the plan it ran."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    told = planner.bus.subscribe("planner.series.changed")
    await sync(world)
    last = None
    while not told.queue.empty():
        last = (await told.queue.get()).payload
    assert last is not None and not last["running"]
    assert {each["video"]["kind"] for each in last["episodes"]} == {"asset"}
    assert {track["kind"] for each in last["episodes"] for track in each["tracks"]} == {"reuse"}


async def test_the_names_of_the_files_are_asked_of_the_rule_once_for_a_card_and_the_plan_is_made_once(world, monkeypatch):
    """A view of the window and a sync of the same card do not ask the rule of names again: what the rule reads (the card and
    the numbers of the episode) did not change. Names known before the plan, the plan made with them stands."""
    import core.plan.executor as executor
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    episode(neighbours, 1, 2)
    seen, untap = tapped(planner)
    made = []
    counted = executor.plan
    monkeypatch.setattr(executor, "plan", lambda *args: made.append(1) or counted(*args))
    try:
        await planner.request("planner.series.get", {"series_id": SERIES})
        asked = len([each for each in seen if each.topic == "rules.format_filename" and each.kind == "query"])
        plans = len(made)
        await planner.request("planner.series.get", {"series_id": SERIES})
        again = len([each for each in seen if each.topic == "rules.format_filename" and each.kind == "query"]) - asked
    finally:
        untap()
    assert asked >= 1 and plans == 2  # the first view: names asked, then the plan made again with them
    assert again == 0 and len(made) - plans == 1


async def test_a_failure_is_told_with_the_episodes_and_the_voice_over_of_its_row(world):
    """The plan passes over a failed attempt, so the window learns where it belongs from the failure itself."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.daemon.answers["Сериал s01e01 G1"] = "mismatch"
    await sync(world)
    (failure,) = (await planner.request("planner.series.get", {"series_id": SERIES}))["failures"]
    assert (failure["kind"], failure["episodes"], failure["voice"]) == ("align", [[1, 1]], "G1")


async def test_the_reset_of_one_episode_forgets_its_failures_and_leaves_the_failures_of_the_others(world):
    """«Повторить» of an episode resets what failed for it: the attempts of the other episodes stay, so what the plan chose
    because they failed is not undone."""
    neighbours, planner, _ = world
    for number in (1, 2):
        episode(neighbours, number, 2)
        neighbours.daemon.answers[f"Сериал s01e0{number} G1"] = "mismatch"
        neighbours.daemon.answers[f"Сериал s01e0{number} G2"] = "mismatch"
    await sync(world)

    async def failed() -> set[tuple]:
        return {(each["voice"], tuple(map(tuple, each["episodes"])))
                for each in (await planner.request("planner.series.get", {"series_id": SERIES}))["failures"]}

    every = {(voice, ((1, number),)) for voice in ("G1", "G2") for number in (1, 2)}
    assert await failed() == every
    # A voice-over of one episode.
    assert (await planner.request("planner.failures.forget", {
        "series_id": SERIES, "season": 1, "episode": 1, "voice": "G1"}))["reset"] == 1
    assert await failed() == every - {("G1", ((1, 1),))}
    # One episode, whatever its voice-over.
    assert (await planner.request("planner.failures.forget", {"series_id": SERIES, "season": 1, "episode": 2}))["reset"] == 2
    assert await failed() == {("G2", ((1, 1),))}
    # A level that holds no failure resets none; the failures of the series are still there.
    assert (await planner.request("planner.failures.forget", {"series_id": SERIES, "season": 2}))["reset"] == 0
    assert (await planner.request("planner.failures.forget", {"series_id": SERIES, "kind": "cut"}))["reset"] == 0
    assert await failed() == {("G2", ((1, 1),))}
    # A voice-over of a whole season; with no level, all that is left.
    assert (await planner.request("planner.failures.forget", {"series_id": SERIES, "season": 1, "voice": "G2"}))["reset"] == 1
    assert await failed() == set()
    await sync(world)
    assert len(await failed()) == 4, "the failed attempts are made again by the next sync, and they fail again"
    assert (await planner.request("planner.failures.forget", {"series_id": SERIES}))["reset"] == 4
    assert await failed() == set()


async def test_the_voice_overs_the_user_switched_off_are_named_by_the_goal_of_the_episode(world):
    neighbours, planner, _ = world
    episode(neighbours, 1, 2)
    neighbours.take_off("G2", [1])
    await sync(world)
    (goal,) = (await planner.request("planner.series.get", {"series_id": SERIES}))["episodes"]
    assert goal["voices_off"] == ["G2"] and [track["voice"] for track in goal["tracks"]] == ["G1"]


async def test_an_alignment_the_service_crashed_in_is_written_to_the_log_with_its_reason(world, caplog):
    """A crash in the service is no failure of the voice-over, and nobody but the service knows its reason: the log says
    which episode and voice-over, why, and that the source is passed over for this sync with no failure recorded."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.daemon.answers["Сериал s01e01 G1"] = "crash"
    with caplog.at_level("WARNING", logger="planner"):
        await sync(world)
    said = [record.getMessage() for record in caplog.records if record.name == "planner"]
    assert any("Сериал s01e01 G1" in each and "сбой сервиса" in each and "пропущен" in each and "не записана" in each
               for each in said), said


async def test_work_that_failed_for_the_moment_is_the_outcome_of_the_sync_and_an_error_of_the_series_until_it_comes_off(world):
    """The service's job ended with no result for the pair: no failure (the next sync tries again), but the outcome of the
    sync says what and why, and the card of the series shows an error and the work waiting; the next sync that makes it
    clears both."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.daemon.answers["Сериал s01e01 G1"] = "empty"
    told = planner.bus.subscribe("series.status.contribution")
    await sync(world)
    view = await planner.request("planner.series.get", {"series_id": SERIES})
    (problem,) = view["problems"]
    assert (problem["slot"], problem["kind"], problem["voice"]) == ([1, 1], "align", "G1") and "daemon_no_result" in problem["reason"]
    assert view["failures"] == []
    last = None
    while not told.queue.empty():
        last = (await told.queue.get()).payload["flags"]
    assert last["error"] and last["waiting"] and not last["ready"]  # the file waits for its voice-over: nothing built
    neighbours.daemon.answers.clear()
    await sync(world)
    view = await planner.request("planner.series.get", {"series_id": SERIES})
    assert view["problems"] == [] and len(view["assets"]) == 1
    while not told.queue.empty():
        last = (await told.queue.get()).payload["flags"]
    assert not last["error"] and not last["waiting"] and last["ready"]


async def test_a_pair_the_service_fell_on_goes_on_with_the_next_source_in_the_same_sync(world):
    """The service falls on the first row of a voice-over laid onto the video: nothing is recorded, the next row is
    downloaded and aligned in the same sync (a job of its own), the file is built with it and the series owes nothing."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.put(3, "g1cvh", 1, group="G1", player="cvh")
    neighbours.daemon.answers["Сериал s01e01 G1"] = "once crash"  # the first row the service falls on; the next is asked afresh
    view = await sync(world)
    stored = await check(world)
    (container,) = stored.values()
    assert [track["snapshot"]["group"] for track in container["tracks"]] == ["G1"]
    assert view["failures"] == [] and view["problems"] == []
    assert len(neighbours.daemon.bodies) == 2


async def test_a_pair_the_service_fell_on_with_no_other_source_builds_the_file_without_the_voice_and_says_so(world):
    """The same fall with no other row of the voice-over: the file is built without it, nothing is recorded, the outcome
    says what is lacking and why and the card of the series shows an error and the work waiting; the next sync, with the
    service well again, aligns the voice-over and clears both."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    neighbours.daemon.answers["Сериал s01e01 G1"] = "crash"
    told = planner.bus.subscribe("series.status.contribution")
    await sync(world)
    view = await planner.request("planner.series.get", {"series_id": SERIES})
    (problem,) = view["problems"]
    assert (problem["slot"], problem["kind"], problem["voice"]) == ([1, 1], "align", "G1") and "сбой" in problem["reason"]
    assert view["failures"] == []
    last = None
    while not told.queue.empty():
        last = (await told.queue.get()).payload["flags"]
    assert last["error"] and last["waiting"] and last["ready"]  # the file is there without the voice-over
    neighbours.daemon.answers.clear()
    await sync(world)
    view = await planner.request("planner.series.get", {"series_id": SERIES})
    assert view["problems"] == []
    (container,) = (await check(world)).values()
    assert [track["snapshot"]["group"] for track in container["tracks"]] == ["G1"]
    while not told.queue.empty():
        last = (await told.queue.get()).payload["flags"]
    assert not last["error"] and not last["waiting"] and last["ready"]


async def test_a_sync_that_builds_a_file_tells_the_card_of_the_series_its_date(world):
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    touched = planner.bus.subscribe("catalog.series.touch_scan_time")
    await sync(world)
    assert (await touched.queue.get()).payload == {"series_id": SERIES}
    await sync(world)  # nothing new: the date stays
    assert touched.queue.empty()


async def test_the_files_of_the_downloads_are_named_for_people(world):
    """The queue of the downloads and the queue of the alignment service show the file of a download: it says the series,
    the episode and what it is, not a key."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    asked = planner.bus.subscribe("downloads.fetch")
    await sync(world)
    names = set()
    while not asked.queue.empty():
        names.add(os.path.basename((await asked.queue.get()).payload["path"]))
    assert names == {"Сериал s01e01 — видео vk 720p.mp4", "Сериал s01e01 — озвучка G1.mp4"}, names


async def test_only_the_video_of_the_reference_is_downloaded_whole_the_rest_not_above_720p(world):
    """The owner's rule (stage 10): everything but the reference is downloaded not above 720p — a voice-over gives only its
    sound."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    asked = planner.bus.subscribe("downloads.fetch")
    await sync(world)
    caps = {}
    while not asked.queue.empty():
        job = (await asked.queue.get()).payload
        caps["dub" if "озвучка" in job["path"] else "video"] = job.get("max_height")
    assert caps == {"video": None, "dub": 720}, caps


def kept_root(neighbours: Neighbours) -> str:
    return os.path.join(neighbours.root, "work", "conform", "kept", str(SERIES))


def fail_the_build_once(monkeypatch) -> None:
    """The build of the first container that is asked falls (the machine stops, the check of the file read back says no)."""
    real, left = Library.build, [1]

    async def build(self, made, group, results, report_for=None):
        if left[0]:
            left[0] -= 1
            raise BuildFailed(made.goal(group.built[0]).name, "container_length_wrong")
        return await real(self, made, group, results, report_for)

    monkeypatch.setattr(Library, "build", build)


def tapped(planner) -> tuple[list[Envelope], callable]:
    seen: list[Envelope] = []
    return seen, planner.bus.tap(seen.append)


def sent(seen: list[Envelope], topic: str, kind: str | None = None) -> list[dict]:
    return [each.payload for each in seen if each.topic == topic
            and (kind is None or each.payload["need"]["kind"] == kind)]


async def test_a_build_that_falls_keeps_the_alignments_and_the_next_sync_builds_from_them_without_the_service(world, monkeypatch):
    """The results of the alignment service outlive its job: a container that could not be built does not cost the work of the
    service again. The job is gone by then (the setting, the owner's hand): the next sync takes the alignments from the keep —
    no job, no download of the voice-overs — and puts them in the file; once it is in place the keep goes."""
    neighbours, planner, _ = world
    episode(neighbours, 1, 2)
    fail_the_build_once(monkeypatch)
    await sync(world)
    assert not [name for name in os.listdir(neighbours.library) if not name.startswith(".")]
    folder = os.path.join(kept_root(neighbours), "s01e01")
    files = sorted(name for name in os.listdir(folder) if name != "manifest.json")
    assert len(files) == 2 and all(os.stat(os.path.join(folder, name)).st_nlink >= 2 for name in files), "links, not copies"
    neighbours.daemon.jobs.clear()
    shutil.rmtree(os.path.join(neighbours.root, "work", "conform", "planner"))
    seen, untap = tapped(planner)
    try:
        await sync(world)
    finally:
        untap()
    stored = await check(world)
    assert [track["snapshot"]["group"] for track in next(iter(stored.values()))["tracks"]] == ["G1", "G2"]
    assert not sent(seen, "conform.submit") and not sent(seen, "downloads.fetch", "dub")
    assert [job["need"]["kind"] for job in sent(seen, "downloads.fetch")] == ["video"]
    assert not os.path.exists(kept_root(neighbours))


async def test_only_what_is_not_kept_goes_to_the_service_and_is_downloaded(world, monkeypatch):
    neighbours, planner, _ = world
    episode(neighbours, 1, 2)
    fail_the_build_once(monkeypatch)
    await sync(world)
    neighbours.put(2, "g3e1", 1, group="G3", player="kodik")
    seen, untap = tapped(planner)
    try:
        await sync(world)
    finally:
        untap()
    stored = await check(world)
    assert sorted(track["snapshot"]["group"] for track in next(iter(stored.values()))["tracks"]) == ["G1", "G2", "G3"]
    (job,) = sent(seen, "conform.submit")
    assert [os.path.basename(path) for path in job["dubs"]] == ["G3.mp4"]
    assert [each["need"]["group"] for each in sent(seen, "downloads.fetch", "dub")] == ["G3"]
    assert not os.path.exists(kept_root(neighbours))


async def test_an_alignment_kept_to_another_video_is_not_used(world, monkeypatch):
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    fail_the_build_once(monkeypatch)
    await sync(world)
    neighbours.put(3, "hd1", 1, group=REF, height=1080)  # a better reference: another timeline
    seen, untap = tapped(planner)
    try:
        await sync(world)
    finally:
        untap()
    stored = await check(world)
    assert [each["video"]["id"]["row_key"] for each in stored.values()] == ["hd1"]
    assert len(sent(seen, "conform.submit")) == 1 and len(sent(seen, "downloads.fetch", "dub")) == 1
    assert not os.path.exists(kept_root(neighbours))


async def test_the_keep_of_an_episode_the_sync_has_no_container_for_goes_with_that_sync_and_only_with_one_that_covers_it(world, monkeypatch):
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    fail_the_build_once(monkeypatch)
    await sync(world)
    assert os.path.isdir(kept_root(neighbours))
    await sync(world, season=2)  # the scope does not hold the episode: what is kept for it stays
    assert os.path.isdir(kept_root(neighbours))
    neighbours.drop(1, "ref1")
    neighbours.drop(2, "g1e1")
    await sync(world)  # the episode has no rows now: nothing is to be built of what is kept
    assert not os.path.exists(kept_root(neighbours))


async def test_the_keep_goes_with_the_series(world, monkeypatch):
    neighbours, planner, _ = world
    episode(neighbours, 1, 1)
    fail_the_build_once(monkeypatch)
    await sync(world)
    assert os.path.isdir(kept_root(neighbours))
    neighbours.publish_event("series.deleted", {"series_id": SERIES})
    await settle(planner.bus, planner, neighbours)
    assert not os.path.exists(kept_root(neighbours))
