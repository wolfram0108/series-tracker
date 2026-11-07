"""Stage 12, voices: an episode cut out of its compilation (modules/voices/cuts.py) — the plan of a cut, the file of the
compilation downloaded uncapped and shared, one batch to the service per file, the voice-overs that wait for the cut and
are aligned to its video, the video offered to the library, a failed cut, the single video taken back, the clear queue."""
import asyncio
import os
import sqlite3

import pytest

from core import Bus, Runner
from core.contracts import WORK_STATE, CompilationRef, CompositionRow, Reference, RowState, compilation_key
from core.db import Database
from modules.settings import SettingsModule
from modules.voices import VoicesModule
from series_factory import insert_series
from settle import wait_until
from test_voices import (Neighbours, Probe, _running, drained, finish, linked, quiet, reference_on_disk, touch)

KEY = compilation_key("ПВА", 2, "vk", 1, 1, 3)
COMP_URL = "https://vk/comp-1-3"


class CutWorld(Neighbours):
    """The neighbours of test_voices with a compilation of episodes 1–3 (VK, 2160p) that episodes 1 and 2 stand in."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.members = {1, 2}
        self.media = {}  # episode -> {cut, pending_video, pending_cut}
        self.reversed = []
        self.offers = []
        self.offer_reply = {"accepted": True}
        self.lose_reverse = False  # the service takes the batch and the answer never comes back
        self.voice = "ПВА"  # the voice-over group of the compilation
        self.extra_rows.append(CompositionRow(
            series_id=7, source_id=2, row_key="comp", season=1, episode_start=1, episode_end=3, voice_group="ПВА", voice_label="ПВА",
            player="vk", heights=[2160], state=RowState.PLAYABLE, link=COMP_URL))

    def register(self):
        super().register()
        self.handle("conform.reverse", self.on_reverse)
        self.handle("downloads.video.offer", self.on_offer)

    def references_list(self):
        found = super().references_list()
        ref = CompilationRef(key=KEY, source_id=2, row_key="comp", player="vk", voice_group=self.voice, height=2160,
                             start=1, end=3)
        return [each.model_copy(update={"compilation": ref}) if each.episode in self.members else each for each in found]

    async def on_episodes(self, env):
        import json
        reply = await super().on_episodes(env)
        for item in reply["items"]:
            media = self.media.get(item["episode_start"], {})
            item["want_cut"] = KEY if item["episode_start"] in self.members else None
            item["cut"] = json.dumps(media["cut"]) if media.get("cut") else None
            item["pending_video"] = media.get("pending_video")
            item["pending_cut"] = json.dumps(media["pending_cut"]) if media.get("pending_cut") else None
        return reply

    async def on_reverse(self, env):
        self.reversed.append(dict(env.payload))
        jobs = {}
        for episode in env.payload["episodes"]:
            key = os.path.basename(os.path.dirname(episode["path"])).rsplit("#", 1)[1]
            jobs[key] = f"rev-{len(self.sent_keys) + 1}"
            self.sent_keys[key] = jobs[key]
        if self.lose_reverse:
            self.lose_reverse = False
            raise RuntimeError("the answer was lost")
        return {"outcome": "accepted", "jobs": jobs, "reason": None, "retry": False}

    async def on_offer(self, env):
        self.offers.append(dict(env.payload))
        if self.offer_reply.get("accepted"):
            episode = next(number for season, number in self.plan if f"u{number}" == env.payload["unique_id"])
            name = f".u{episode}.video.mkv"
            touch(os.path.join(self.library, name), b"cut")
            self.media.setdefault(episode, {}).update(pending_video=name, pending_cut=env.payload["cut"])
        return self.offer_reply


@pytest.fixture
async def world(db_path, tmp_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", save_path="/media/s",
                      url="https://site/title.html", source_type="web_video", parser_profile_id=1)
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    sources = [linked("cvh", "FumoDub", range(1, 4), url="https://site/?player=cvh&voice=FumoDub"),
               linked("kodik", "BlackCat", range(1, 4))]
    neighbours = CutWorld(bus, sources, sources[0]["url"], [(1, 1), (1, 2), (1, 3)], library=str(tmp_path / "library"))
    neighbours.settings["conform.work_dir"] = str(tmp_path / "work")
    probe = Probe(bus)
    voices = VoicesModule(bus, db)
    runner = Runner(bus, [neighbours, SettingsModule(bus, db), voices, probe])
    await runner.start()
    _running.bus, _running.voices = bus, voices
    for episode in (1, 2, 3):
        reference_on_disk(neighbours, episode)
    yield probe, neighbours, voices, bus
    await runner.stop()


async def until(condition, what, seconds=5.0):
    assert await wait_until(condition, _running.bus, _running.voices, seconds=seconds), what


async def cuts(probe):
    return {each["episode"]: each for each in (await probe.request("voices.cuts.get", {"series_id": 7}, timeout=5))["items"]}


async def compilation_downloaded(world):
    """A sync: the cuts are planned and admitted, the file of the compilation downloaded."""
    probe, neighbours, voices, bus = world
    probe.send_command("voices.dispatch", {"series_id": 7})
    await until(lambda: any(best(each) for each in neighbours.fetches), neighbours.fetches)
    fetch = next(each for each in neighbours.fetches if best(each))
    touch(fetch["path"], b"compilation")
    finish(probe, fetch["key"])
    if not await wait_until(lambda: neighbours.reversed, _running.bus, _running.voices, seconds=5):
        raise AssertionError(("no batch went to the service", await cuts(probe), await voices.repo.files_of(7, "best")))
    await quiet(voices)
    return fetch


def best(fetch):
    """The download of the compilation for its cuts: uncapped (the same compilation is also a voice-over of episode 3, at
    720p, downloaded apart)."""
    return fetch["url"] == COMP_URL and fetch.get("max_height") is None


def reverse_done(neighbours, ok=True, critical=()):
    """The service ends every job of the last batch: the video of each episode, or an error."""
    batch = neighbours.reversed[-1]
    results = []
    for episode in batch["episodes"]:
        name = os.path.basename(os.path.dirname(episode["path"]))
        out = os.path.join(batch["out_dir"], name, "episode.mkv")
        if ok:
            touch(out, b"cut video")
        job_id = neighbours.sent_keys[name.rsplit("#", 1)[1]]
        result = {"dub": "x", "ok": ok, "out_path": out if ok else None, "from_ref_s": 1300.0, "from_dub_s": 12.0,
                  "error": None if ok else "кадры не сопоставлены", "critical": list(critical)}
        results.append({"job_id": job_id, "kind": "reverse", "status": "done" if ok else "failed",
                        "error": None, "results": [result]})
    return results


@pytest.mark.asyncio
async def test_the_compilation_is_downloaded_once_uncapped_and_its_episodes_go_as_one_batch(world):
    probe, neighbours, voices, bus = world
    fetch = await compilation_downloaded(world)
    assert len([each for each in neighbours.fetches if best(each)]) == 1
    batch = neighbours.reversed[0]
    assert batch["compilation"] == fetch["path"] and len(batch["episodes"]) == 2
    assert all("#" in os.path.basename(os.path.dirname(each["path"])) for each in batch["episodes"])
    assert {each["status"] for each in (await cuts(probe)).values()} == {"aligning"}


@pytest.mark.asyncio
async def test_the_voice_overs_of_a_cut_episode_wait_and_the_compilations_voice_is_not_planned(world):
    """К-6: nothing of episodes 1 and 2 goes to the service before their cut; episode 3 is not cut and goes on."""
    probe, neighbours, voices, bus = world
    await compilation_downloaded(world)
    for each in neighbours.fetches:
        if not best(each):
            touch(each["path"], b"video")
            finish(probe, each["key"])
    await quiet(voices)
    episodes = {int(submit["label"].rsplit("e", 1)[1]) for submit in neighbours.submits}
    assert episodes <= {3}
    dubs = (await probe.request("voices.dubs.get", {"series_id": 7}, timeout=5))["items"]
    voices_of = {(each["episode"], each["voice_label"]) for each in dubs}
    # The video of 1 and 2 carries ПВА (the compilation's); FumoDub of REF 2 is a voice-over to align like any other.
    assert (1, "FumoDub") in voices_of and (1, "ПВА") not in voices_of and (3, "FumoDub") not in voices_of


@pytest.mark.asyncio
async def test_a_cut_video_is_offered_to_the_library_and_the_voice_overs_are_aligned_to_it(world):
    probe, neighbours, voices, bus = world
    await compilation_downloaded(world)
    for result in reverse_done(neighbours):
        probe.publish_event("conform.job.finished", result)
    await until(lambda: len(neighbours.offers) == 2, neighbours.offers)
    offer = neighbours.offers[0]
    assert offer["cut"]["key"] == KEY and offer["cut"]["height"] == 2160 and offer["cut"]["voice_group"] == "ПВА"
    assert offer["cut"]["from_ref_s"] == 1300.0
    for each in neighbours.fetches:
        if not best(each):
            touch(each["path"], b"video")
            finish(probe, each["key"])
    await until(lambda: any(submit["label"].endswith("e01") for submit in neighbours.submits), neighbours.submits)
    await quiet(voices)
    row = next(each for each in await voices.repo.for_series(7) if each["episode"] == 1 and each["ref_path"])
    assert row["ref_path"].endswith(".u1.video.mkv")
    assert {each["status"] for each in (await cuts(probe)).values()} == {"offered"}


@pytest.mark.asyncio
async def test_a_failed_cut_keeps_the_single_video_its_job_stays_and_the_voices_come_back(world):
    probe, neighbours, voices, bus = world
    neighbours.clear_finished = True
    await compilation_downloaded(world)
    for result in reverse_done(neighbours, ok=False):
        probe.publish_event("conform.job.finished", result)
    await quiet(voices)
    found = await cuts(probe)
    assert found[1]["status"] == "failed" and found[1]["detail"]["code"] == "external"
    assert neighbours.offers == [] and neighbours.conform_cancels == []
    probe.send_command("voices.settle", {"series_id": 7})  # the sync: what the plan takes away goes
    await quiet(voices)
    dubs = (await probe.request("voices.dubs.get", {"series_id": 7}, timeout=5))["items"]
    voices_of = {(each["episode"], each["voice_label"]) for each in dubs}
    assert (1, "FumoDub") not in voices_of  # the single video keeps its voice: FumoDub is its own sound again
    assert (await probe.request("voices.cuts.retry", {"series_id": 7, "season": 1}, timeout=5))["restarted"] == 2


@pytest.mark.asyncio
async def test_a_clear_queue_takes_the_job_of_an_offered_cut_out_of_the_history(world):
    probe, neighbours, voices, bus = world
    neighbours.clear_finished = True
    await compilation_downloaded(world)
    for result in reverse_done(neighbours):
        probe.publish_event("conform.job.finished", result)
    await until(lambda: len(neighbours.conform_cancels) == 2, neighbours.conform_cancels)
    await quiet(voices)
    assert sorted(neighbours.conform_cancels) == sorted(neighbours.sent_keys.values())


@pytest.mark.asyncio
async def test_a_cut_is_work_of_its_episode_until_it_ends(world):
    probe, neighbours, voices, bus = world
    await compilation_downloaded(world)
    work = await probe.request("voices.work.get", {"series_id": 7}, timeout=5)
    assert work["active"] and [1, 1, 0] in [list(each) for each in work["busy"]]


@pytest.mark.asyncio
async def test_the_run_hears_that_the_cut_of_an_episode_ended_without_asking(world):
    """The run of a sync builds the container of an episode once no executor holds work of it: the end of a cut is told
    by the event of work, as the end of a voice-over is, not only to a question."""
    probe, neighbours, voices, bus = world
    # The cut is the only work of the episodes: the compilation speaks with the voice of the reference, and no voice-over of
    # another group is there.
    del neighbours.sources[1:]
    neighbours.voice = "FumoDub"
    neighbours.extra_rows[:] = [row.model_copy(update={"voice_group": "FumoDub", "voice_label": "FumoDub"})
                                for row in neighbours.extra_rows]
    await compilation_downloaded(world)
    told = bus.subscribe(WORK_STATE)
    for result in reverse_done(neighbours):
        probe.publish_event("conform.job.finished", result)
    await until(lambda: len(neighbours.offers) == 2, neighbours.offers)
    await quiet(voices)
    states = [each for each in drained(told) if each["owner"] == "voices"]
    assert states and states[-1]["busy"] == [] and states[-1]["active"] is False


@pytest.mark.asyncio
async def test_an_episode_taken_out_of_its_compilation_gets_its_single_video_back(world):
    """The container holds a cut, the plan does not want it any more: the single video is downloaded again, uncapped, and
    offered with no compilation."""
    probe, neighbours, voices, bus = world
    neighbours.members = set()
    neighbours.media[1] = {"cut": {"key": KEY, "height": 2160, "voice_group": "ПВА"}}
    probe.send_command("voices.dispatch", {"series_id": 7})
    await until(lambda: any(each["key"].startswith("cutvideo:") for each in neighbours.fetches), neighbours.fetches)
    fetch = next(each for each in neighbours.fetches if each["key"].startswith("cutvideo:"))
    assert fetch["url"].endswith("cvh-FumoDub-1") and fetch.get("max_height") is None
    touch(fetch["path"], b"single")
    probe.publish_event("downloads.fetch.finished", {"key": fetch["key"], "ok": True, "path": fetch["path"],
                                                     "series_id": 7})
    await until(lambda: neighbours.offers, "nothing was offered")
    assert neighbours.offers[0]["cut"] is None and neighbours.offers[0]["unique_id"] == "u1"


@pytest.mark.asyncio
async def test_a_batch_whose_answer_was_lost_finds_its_jobs_by_their_keys(world):
    probe, neighbours, voices, bus = world
    neighbours.lose_reverse = True
    await compilation_downloaded(world)
    voices._recovery.poke()

    async def attached():
        return {each["status"] for each in (await cuts(probe)).values()} == {"aligning"}

    await until(attached, "the jobs were not found by their keys")
    assert len(neighbours.reversed) == 1  # nothing was sent twice


@pytest.mark.asyncio
async def test_a_cut_with_a_critical_finding_is_not_taken(world):
    """reverse_api.md, 8.2: the video of a cut is judged by `ok` and `critical`."""
    probe, neighbours, voices, bus = world
    await compilation_downloaded(world)
    for result in reverse_done(neighbours, critical=["рассинхрон звука и картинки 900 мс"]):
        probe.publish_event("conform.job.finished", result)
    await quiet(voices)
    found = await cuts(probe)
    assert found[1]["status"] == "failed" and found[1]["detail"]["code"] == "cut_critical"
    assert neighbours.offers == []
