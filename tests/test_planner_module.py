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

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core import container as mkv
from core.contracts import (VOICE_TAKE, CompositionReply, CompositionRow, EffectiveLever, EpisodeSettings, Level,
                            RowState, group_target)
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
        self.handle("composition.layout", self.on_composition)
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

    async def on_composition(self, env: Envelope) -> CompositionReply:
        return CompositionReply(series_id=SERIES, rev=1, rows=self.site.rows, settings=self.site.settings)

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
