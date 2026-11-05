"""Tests of the voices module: the plan of voice-overs from the layout, their downloads, alignment and aftermath."""
import asyncio
import os
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from conform_fakes import FakeClient
from core import BaseModule, Bus, Runner
from core.contracts import (CompositionReply, CompositionRow, EffectiveLever, EpisodeSettings,
                            Level, Reference, ReferenceMark, RowState, SubmitReply, group_target)
from core.db import Database
from core.envelope import Envelope
from modules.conform import ConformModule
from modules.conform.paths import roots, workspace
from modules.settings import SettingsModule
from modules.voices import VoicesModule
from modules.voices.module import dub_file, temp_path
from modules.voices.settings import DEFAULT_PRIORITY
from modules.voices.states import public_state
from series_factory import insert_series
from settle import settle, wait_until


def code_of(stored):
    """The code of a stored reason or of a pushed detail."""
    from core.reasons import parse
    detail = stored if isinstance(stored, dict) else parse(stored)
    return detail and detail["code"]


def src(player, studio, episodes, kind="", url=None):
    """A voice-over of a player as a page lists it: the rows it has, one per episode."""
    return {"url": url or f"https://site/?player={player}&voice={studio}", "player": player,
            "studio": studio, "kind": kind, "episodes": list(episodes)}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    return str(path)


class Probe(BaseModule):
    name = "probe"


# The system the test started, for the waiters below: `build_system` says which it is.
_running = SimpleNamespace(bus=None, voices=None)


async def next_step():
    """The scripted neighbours act on what the module sent: they look again at the next step of the bus."""
    step = _running.bus.next_step()
    try:
        await asyncio.wait_for(step.wait(), 1.0)  # the one second only ends a wait for a step that never comes
    except asyncio.TimeoutError:
        pass


async def idle():
    """Returns when the bus took apart every envelope and the module runs nothing in the background: what did not happen
    by now will not happen."""
    await settle(_running.bus, _running.voices)


@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    probe = Probe(bus)
    runner = Runner(bus, [SettingsModule(bus, db), VoicesModule(bus, db), probe])
    await runner.start()
    yield probe
    await runner.stop()


async def resolve(probe, *names):
    return (await probe.request("voices.groups.resolve", {"names": list(names)}, timeout=5))["groups"]


async def set_groups(probe, groups):
    import json
    await probe.request("settings.value.set", {"key": "voices.groups", "value": json.dumps(groups)}, timeout=5)


@pytest.mark.asyncio
async def test_a_name_resolves_to_the_group_whose_name_or_trigger_it_equals_without_case_spaces_and_marks(system):
    await set_groups(system, {"Red Head Sound": ["RedHeadSound", "РХС"], "Fumo-Dub": []})
    found = await resolve(system, "red head sound", "REDHEADSOUND", "r.h.s", "рхс", "FumoDub", "BlackCat")
    assert found == {"red head sound": "Red Head Sound", "REDHEADSOUND": "Red Head Sound", "r.h.s": None,
                     "рхс": "Red Head Sound", "FumoDub": "Fumo-Dub", "BlackCat": None}


@pytest.mark.asyncio
async def test_the_settings_a_run_took_answer_as_they_were_taken_whatever_is_saved_since(system):
    """Н-5: voices.settings.export gives the stored values for the snapshot of a run; with them, the groups and the view of
    the settings are those of the start of the run."""
    await set_groups(system, {"Группа": ["x"]})
    taken = (await system.request("voices.settings.export", {}, timeout=5))["values"]
    await set_groups(system, {"Другая": ["x"]})
    by_run = (await system.request("voices.groups.resolve", {"names": ["x"], "values": taken}, timeout=5))["groups"]
    assert by_run == {"x": "Группа"} and await resolve(system, "x") == {"x": "Другая"}
    view = await system.request("voices.settings.get", {"values": taken}, timeout=5)
    assert view["voice_groups"] == {"Группа": ["x"]}
    assert (await system.request("voices.settings.get", {}, timeout=5))["voice_groups"] == {"Другая": ["x"]}

@pytest.mark.asyncio
async def test_without_groups_no_name_belongs_to_one_not_even_a_name_written_twice(system):
    assert await resolve(system, "BlackCat", "BlackCat", "Black Cat") == {"BlackCat": None, "Black Cat": None}


@pytest.mark.asyncio
async def test_an_empty_name_or_one_of_marks_alone_belongs_to_no_group(system):
    await set_groups(system, {"Group": ["x"]})
    assert await resolve(system, "", "  ", "...", "x") == {"": None, "  ": None, "...": None, "x": "Group"}
    assert await resolve(system) == {}


@pytest.mark.asyncio
async def test_a_combined_credit_is_one_name_and_joins_neither_group_of_its_parts(system):
    await set_groups(system, {"First": ["A"], "Second": ["B"], "Both": ["A / B"]})
    found = await resolve(system, "A / B", "A/B", "A & B", "A", "B", "AB")
    # "A / B" is not split into "A" and "B" and not joined with them; it is the name the user wrote as a trigger.
    assert found == {"A / B": "Both", "A/B": "Both", "A & B": "Both", "A": "First", "B": "Second", "AB": "Both"}
    await set_groups(system, {"First": ["A"], "Second": ["B"]})
    assert (await resolve(system, "A / B"))["A / B"] is None


@pytest.mark.asyncio
async def test_a_trigger_two_groups_hold_answers_the_group_written_first_and_a_group_name_outranks_a_trigger(system):
    await set_groups(system, {"One": ["shared", "Two"], "Two": ["shared"]})
    found = await resolve(system, "shared", "two")
    assert found == {"shared": "One", "two": "Two"}


@pytest.mark.asyncio
async def test_a_broken_groups_setting_leaves_every_name_without_a_group(system):
    await set_groups(system, {"Group": ["x"]})
    await system.request("settings.value.set", {"key": "voices.groups", "value": "{broken"}, timeout=5)
    assert await resolve(system, "x") == {"x": None}


def linked(player, studio, episodes, kind="", url=None):
    """A source whose episode links are what the table of the page would hand out."""
    row = src(player, studio, episodes, kind, url)
    row["links"] = {str(n): f"https://site/title.html#{player}-{studio}-{n}" for n in episodes}
    return row


class Neighbours(BaseModule):
    """catalog + composition + scan + downloads + settings in one fake; fetch commands are captured.

    The world is `sources` (each a player's voice-over with the episodes it has and their links, all in one source of
    the series), `reference_url` (the source whose rows are the references) and `plan` (the (season, episode) pairs
    that have a reference). The layout, the references and the plan of downloads are derived from them as the real
    neighbours would hand them out."""

    name = "neighbours"

    def __init__(self, bus, sources, reference_url, plan, library="/nonexistent", conform=True):
        self.serves_conform = conform
        self.sources = sources
        self.reference_url = reference_url
        self.plan = plan  # [(season, episode)] of the episodes that have a reference
        self.library = library
        self.references = {}  # episode -> file name of the reference video on disk
        self.uids = {}  # episode -> media item of its reference (default: u<episode>)
        self.extra_items = []  # more media items of the plan, as downloads.episodes.get shows them
        self.item_keys: dict[int, str] = {}  # episode -> the row of the reference video, when not the reference's own
        self.marks = {}  # (season, episode) -> mark of the reference when it is not the usual one
        self.levers = {}  # (season, episode) -> [EffectiveLever] of the output levers
        self.settings = {}
        self.fetches = []
        self.cancels = []  # series whose downloads were all cancelled
        self.cancelled_keys = []  # downloads cancelled one by one, by key
        self.submits = []
        self.submit_reply = None
        self.jobs = {}  # job id -> reply of conform.job.get
        self.kind = "video"
        self.layout_asks = 0
        self.output_asks = 0
        self.layout_failures = 0  # answers of composition.get that fail before it answers again
        self.layout_gate = None  # an asyncio.Event the answer of composition.get waits for
        self.extra_rows = []  # more rows of the layout (compilations and the like)
        self.conform_cancels = []
        self.finish_at_once = False
        self.lost_before_arrival = 0
        self.lost_after_arrival = 0
        self.sent_keys = {}
        self.cancel_done = True
        self.service_reachable = True
        self.job_get_failures = 0
        self.clear_finished = False  # the owner's choice in the settings of the alignment server
        self.running_jobs = set()  # jobs the service has although the answer to their submission was lost
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self.on_series)
        self.handle("composition.get", self.on_layout)
        self.handle("scan.output.get", self.on_output)
        self.handle("downloads.episodes.get", self.on_episodes)
        self.handle("settings.value.get", self.on_setting)
        self.handle("settings.values.by_prefix", self.on_settings_by_prefix)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle("work.withdrawn", self.on_cancel)
        if self.serves_conform:
            self.handle("conform.submit", self.on_submit)
            self.handle("conform.cancel", self.on_conform_cancel)
            self.handle("conform.job.find", self.on_job_find)
            self.handle("conform.job.get", self.on_job_get)
            self.handle("conform.workspace", self.on_workspace)
            self.handle("conform.settings.get", self.on_conform_settings)

    # --- the layout, the references and the plan of downloads the sources and `plan` stand for ----------------

    def season_of(self, episode):
        return next((season for season, number in self.plan if number == episode), 1)

    def row_key(self, source, episode):
        return f"{source['player']}:{source['studio']}:{episode}"

    def rows(self):
        return [CompositionRow(
            series_id=7, source_id=1, row_key=self.row_key(source, number), season=self.season_of(number),
            episode_start=number, episode_end=number, voice_group=None, voice_label=source["studio"],
            player=source["player"], heights=[720], state=RowState.PLAYABLE, link=source["links"][str(number)])
            for source in self.sources for number in source["episodes"]] + list(self.extra_rows)

    def references_list(self):
        reference = next(s for s in self.sources if s["url"] == self.reference_url)
        found = []
        for season, episode in self.plan:
            mark = self.marks.get((season, episode), ReferenceMark.BY_LEVEL)
            if mark in (ReferenceMark.NONE, ReferenceMark.UNFIT):
                found.append(Reference(season=season, episode=episode, mark=mark))
            else:
                found.append(Reference(season=season, episode=episode, mark=mark, source_id=1,
                                       row_key=self.row_key(reference, episode), player=reference["player"],
                                       height=1080))
        return found

    async def on_series(self, env):
        return {"id": env.payload["series_id"], "url": "https://site/title.html", "name": "Series",
                "save_path": self.library, "kind": self.kind}

    async def on_layout(self, env):
        self.layout_asks += 1
        if self.layout_gate is not None:
            await self.layout_gate.wait()
        if self.layout_failures:
            self.layout_failures -= 1
            raise RuntimeError("composition does not answer")
        return CompositionReply(
            series_id=env.payload["series_id"], rev=1, rows=self.rows(),
            settings=[EpisodeSettings(season=season, episode=episode, levers=levers)
                      for (season, episode), levers in self.levers.items()]).model_dump(mode="json")

    async def on_output(self, env):
        self.output_asks += 1
        return [reference.model_dump(mode="json") for reference in self.references_list()]

    async def on_episodes(self, env):
        reference = next(s for s in self.sources if s["url"] == self.reference_url)
        items = [{"unique_id": self.uids.get(episode, f"u{episode}"), "season": season,
                  "episode_start": episode, "part": None, "source_url": reference["links"].get(str(episode), ""),
                  "status": "pending", "final_filename": self.references.get(episode),
                  "superseded_by": None, "source_id": 1,
                  "row_key": self.item_keys.get(episode, self.row_key(reference, episode))} for season, episode in self.plan]
        return {"items": items + self.extra_items}

    # --- the rest of the neighbours ---------------------------------------------------------------------------

    async def on_setting(self, env):
        return {"key": env.payload["key"], "value": self.settings.get(env.payload["key"])}

    async def on_settings_by_prefix(self, env):
        prefix = env.payload["prefix"]
        return {"values": {key: value for key, value in self.settings.items() if key.startswith(prefix)}}

    async def on_fetch(self, env):
        self.fetches.append(dict(env.payload))

    async def on_cancel(self, env):
        """The notice that work is not needed (the executor, downloads, hears it and stops by itself)."""
        if env.payload.get("keys"):  # the jobs of these keys
            self.cancelled_keys.extend(env.payload["keys"])
        else:
            self.cancels.append(env.payload["series_id"])

    async def on_submit(self, env):
        if self.lost_before_arrival:
            self.lost_before_arrival -= 1
            raise RuntimeError("the request never arrived")
        self.submits.append(dict(env.payload))
        job_id = f"job-{len(self.submits)}"
        self.sent_keys[env.payload["key"]] = job_id
        if self.finish_at_once:  # the service ends the job before it has even answered
            self.publish_event("conform.job.finished", job_result(self.submits[-1], job_id))
        if self.lost_after_arrival:
            self.lost_after_arrival -= 1
            self.running_jobs.add(job_id)
            raise RuntimeError("the answer was lost")
        return self.submit_reply or SubmitReply.accepted(job_id).model_dump()

    async def on_workspace(self, env):
        if not env.payload.get("dub_folder"):
            return roots(self.settings.get("conform.work_dir"))
        return workspace(env.payload["dub_folder"])

    async def on_conform_settings(self, env):
        return {"url": "", "work_dir": "", "work_dir_remote": "", "clear_finished": self.clear_finished}

    async def on_conform_cancel(self, env):
        self.conform_cancels.append(env.payload["job_id"])
        return {"done": self.cancel_done}

    async def on_job_find(self, env):
        if not self.service_reachable:
            return {"reachable": False, "job_id": None}
        return {"reachable": True, "job_id": self.sent_keys.get(env.payload["key"])}

    async def on_job_get(self, env):
        if self.job_get_failures:
            self.job_get_failures -= 1
            raise RuntimeError("the service does not answer")
        job_id = env.payload["job_id"]
        if job_id not in self.jobs and job_id in self.running_jobs:
            return {"job_id": job_id, "status": "running", "error": None, "results": []}
        return self.jobs.get(job_id, {"job_id": job_id, "status": "gone", "error": None, "results": []})


async def build_system(db_path, tmp_path, daemon=None):
    """The module with fake neighbours; with a `daemon` the real conform bridge talks to it instead of a fake."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        insert_series(conn, id=7, name="s", name_en="s", site="dunhuatv", save_path="/media/s",
                      url="https://site/title.html", source_type="web_video", parser_profile_id=1)
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    sources = [linked("cvh", "FumoDub", range(1, 4), url="https://site/?player=cvh&voice=FumoDub"),
               linked("kodik", "BlackCat", range(1, 4)),
               linked("cvh", "BlackCat", [1, 2])]
    neighbours = Neighbours(bus, sources, sources[0]["url"], [(1, 1), (1, 2), (1, 3)],
                            library=str(tmp_path / "library"), conform=daemon is None)
    neighbours.settings["conform.work_dir"] = str(tmp_path / "work")
    probe = Probe(bus)
    voices = VoicesModule(bus, db)
    modules = [neighbours, SettingsModule(bus, db), voices, probe]
    if daemon is not None:
        modules.append(ConformModule(bus, daemon, backoff_start=0.05))
    runner = Runner(bus, modules)
    await runner.start()
    _running.bus, _running.voices = bus, voices
    return (probe, neighbours, voices, bus), runner


async def restarted(module):
    """What run.py does for a module: it starts, and when every module is up the signal reaches it."""
    await module.start()
    await module.on_system_started(Envelope("system.started", "event", {}))


@pytest.fixture
async def dubs(db_path, tmp_path):
    system_, runner = await build_system(db_path, tmp_path)
    yield system_
    await runner.stop()


@pytest.fixture
async def dubs_with_conform(db_path, tmp_path):
    """The same system, but the alignment bridge is the real module over a scripted daemon."""
    daemon = FakeClient([("conform.snapshot", {"items": []})])
    system_, runner = await build_system(db_path, tmp_path, daemon)
    system_[1].daemon = daemon
    system_[1].settings["conform.work_dir_remote"] = "/share/work"
    yield system_
    await runner.stop()


async def settled(neighbours, count, seconds=3.0):
    assert await wait_until(lambda: len(neighbours.fetches) >= count, _running.bus, _running.voices,
                            seconds=seconds), neighbours.fetches


def finish(probe, key, ok=True, error=None):
    probe.publish_event("downloads.fetch.finished", {"key": key, "ok": ok, "error": error,
                                                     "transient": False, "path": "", "series_id": 7})


async def rows(probe):
    return (await probe.request("voices.dubs.get", {"series_id": 7}, timeout=5))["items"]


@pytest.mark.asyncio
async def test_dispatch_downloads_every_other_voice_of_every_planned_episode(dubs):
    probe, neighbours, voices, _ = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    # The player higher in the order of the settings is the main candidate; episode 3 has only the other one.
    assert {f["url"] for f in neighbours.fetches} == {
        "https://site/title.html#cvh-BlackCat-1", "https://site/title.html#cvh-BlackCat-2",
        "https://site/title.html#kodik-BlackCat-3"}
    first = neighbours.fetches[0]
    assert first["max_height"] == 720 and first["series_id"] == 7 and first["key"].startswith("dub:")
    assert first["path"].startswith(downloads(neighbours) + "/7/s01e01/")
    assert {r["status"] for r in await rows(probe)} == {"downloading"}
    assert {r["voice_key"] for r in await rows(probe)} == {"BlackCat"}


@pytest.mark.asyncio
async def test_a_finished_download_makes_the_dub_ready_and_is_announced(dubs):
    probe, neighbours, _, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    finish(probe, neighbours.fetches[0]["key"])
    event = await change(sub, "ready")
    assert event["status"] == "ready" and event["voice_key"] == "BlackCat" and event["series_id"] == 7
    assert sorted(r["status"] for r in await rows(probe)) == ["downloading", "downloading", "ready"]


@pytest.mark.asyncio
async def test_a_failed_source_hands_over_to_the_next_of_the_chain(dubs):
    probe, neighbours, _, _ = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    victim = next(f for f in neighbours.fetches if f["url"].endswith("-1"))
    finish(probe, victim["key"], ok=False, error="timed out")
    await settled(neighbours, 4)
    retry = neighbours.fetches[3]
    assert retry["key"] == victim["key"] and retry["url"] == "https://site/title.html#kodik-BlackCat-1"
    assert next(r for r in await rows(probe) if r["episode"] == 1)["attempt"] == 1


@pytest.mark.asyncio
async def test_a_dub_without_any_source_left_is_failed_and_logged(dubs, caplog):
    probe, neighbours, _, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    victim = next(f for f in neighbours.fetches if f["url"].endswith("-3"))
    caplog.set_level("WARNING")
    finish(probe, victim["key"], ok=False, error="gone")
    event = await change(sub, "failed")
    assert event["status"] == "failed" and event["episode"] == 3
    failed = next(r for r in await rows(probe) if r["episode"] == 3)
    assert failed["status"] == "failed" and failed["error"] == "gone"
    assert any("BlackCat" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_new_dispatch_offers_failed_voices_again_from_the_main_source(dubs):
    probe, neighbours, _, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    victim = next(f for f in neighbours.fetches if f["url"].endswith("-3"))
    finish(probe, victim["key"], ok=False, error="gone")
    await change(sub, "failed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    assert neighbours.fetches[3]["key"] == victim["key"] and neighbours.fetches[3]["url"] == victim["url"]


@pytest.mark.asyncio
async def test_an_episode_nobody_offers_is_a_failure_not_silence(dubs):
    probe, neighbours, _, _ = dubs
    neighbours.plan.append((1, 4))
    reference = neighbours.sources[0]
    reference["episodes"].append(4)
    reference["links"]["4"] = "https://site/title.html#cvh-FumoDub-4"
    neighbours.sources[1]["links"].pop("3")
    neighbours.sources[1]["episodes"].remove(3)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    missing = [r for r in await rows(probe) if r["status"] == "failed"]
    assert [r["episode"] for r in missing] == [3, 4]
    assert {code_of(r["error"]) for r in missing} == {"not_in_any_source"}
    assert len(neighbours.fetches) == 2


@pytest.mark.asyncio
async def test_a_ready_dub_is_not_downloaded_again_while_its_file_exists(dubs):
    probe, neighbours, _, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    done = neighbours.fetches[0]
    os.makedirs(os.path.dirname(done["path"]))
    open(done["path"], "wb").close()
    finish(probe, done["key"])
    await change(sub, "ready")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await idle()
    assert len(neighbours.fetches) == 3
    os.remove(done["path"])
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    assert neighbours.fetches[3]["key"] == done["key"]


@pytest.mark.asyncio
async def test_episodes_that_left_the_plan_lose_their_rows_and_a_download_under_way_is_withdrawn(dubs):
    probe, neighbours, voices, _ = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    finish(probe, neighbours.fetches[2]["key"], ok=False, error="x")
    neighbours.plan[:] = [(1, 1)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await pass_over(voices, neighbours, 2)
    assert [r["episode"] for r in await rows(probe)] == [1]
    assert neighbours.fetches[1]["key"] in neighbours.cancelled_keys  # nobody needs the file any more


@pytest.mark.asyncio
async def test_a_series_that_is_not_a_video_series_is_not_even_asked_about_its_layout(dubs):
    probe, neighbours, voices, _ = dubs
    neighbours.kind = "torrent"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await quiet(voices)
    assert neighbours.layout_asks == neighbours.output_asks == 0
    assert neighbours.fetches == [] and await rows(probe) == []
    neighbours.kind = "video"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    assert neighbours.layout_asks == neighbours.output_asks == 1


@pytest.mark.asyncio
async def test_deleting_the_series_cancels_downloads_and_removes_its_files(dubs):
    probe, neighbours, _, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    folder = os.path.join(downloads(neighbours), "7")
    os.makedirs(folder)
    open(os.path.join(folder, "x.mp4"), "wb").close()
    sub = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    report = (await asyncio.wait_for(sub.queue.get(), 3)).payload
    assert report == {"series_id": 7, "owner": "voices"}
    assert neighbours.cancels == [7] and await rows(probe) == [] and not os.path.exists(folder)


@pytest.mark.asyncio
async def test_a_module_with_interrupted_downloads_starts_when_the_modules_it_asks_are_not_up_yet(db_path, dubs):
    """conform and composition start after voices: the start of the module asks nobody, or the system would not come up."""
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await voices.stop()
    alone = VoicesModule(Bus(), Database(db_path))
    await alone.start()
    try:
        assert await alone.repo.pending(), "the interrupted downloads wait in the database for the signal that all are up"
    finally:
        await alone.stop()


@pytest.mark.asyncio
async def test_after_a_restart_interrupted_downloads_are_sent_again(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        await settled(neighbours, 6)
        assert sorted(f["key"] for f in neighbours.fetches[3:]) == sorted(f["key"] for f in neighbours.fetches[:3])
    finally:
        await again.stop()


def touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


# An event shows the row as it is when it is announced, so steps that follow each other at once may be shown
# as the last of them.
COALESCED_INTO = {"ready": {"aligning", "aligned", "placed"}, "aligning": {"aligned", "placed"},
                  "aligned": {"placed"}}


def reached(seen, wanted):
    """Whether the events seen are the wanted statuses, a coalesced step standing in for the step it swallowed."""
    pool, unmatched = list(seen), []
    for status in wanted:
        if status in pool:
            pool.remove(status)
        else:
            unmatched.append(status)
    for status in unmatched:
        stand_in = next((event for event in pool if event in COALESCED_INTO.get(status, ())), None)
        if stand_in is None:
            return False
        pool.remove(stand_in)
    return not pool


async def statuses_until(sub, wanted, seconds=5.0):
    """Wait on the pushed events until `wanted` (statuses seen) is reached, a coalesced step counting as seen.

    The announcements of a planned row and of a sent download are skipped unless wanted."""
    seen = []
    end = asyncio.get_running_loop().time() + seconds
    while not reached(seen, wanted):
        left = end - asyncio.get_running_loop().time()
        assert left > 0, (seen, wanted)
        try:
            status = (await asyncio.wait_for(sub.queue.get(), left)).payload["status"]
        except asyncio.TimeoutError:
            raise AssertionError((seen, wanted)) from None
        if status not in ("downloading", "pending") or status in wanted:
            seen.append(status)
    return seen


async def change_state(sub, voice_key, state, seconds=3.0):
    """The next pushed event that shows this voice-over in this state."""
    end = asyncio.get_running_loop().time() + seconds
    while True:
        left = end - asyncio.get_running_loop().time()
        assert left > 0, (voice_key, state)
        event = (await asyncio.wait_for(sub.queue.get(), left)).payload
        if event["voice_key"] == voice_key and event["state"] == state:
            return event


async def quiet(voices, seconds=20.0):
    """Wait until the bus took apart every envelope and the module has nothing running in the background, so a test may
    change its data: what a handler was still doing when the last command went out is done."""
    await settle(_running.bus, voices, seconds=seconds)


async def change(sub, status, seconds=3.0):
    """The next pushed event with this status (others pass by)."""
    end = asyncio.get_running_loop().time() + seconds
    while True:
        left = end - asyncio.get_running_loop().time()
        assert left > 0, status
        event = (await asyncio.wait_for(sub.queue.get(), left)).payload
        if event["status"] == status:
            return event


def reference_on_disk(neighbours, episode=1):
    name = f"Series s01e{episode:02d}.mp4"
    neighbours.references[episode] = name
    touch(os.path.join(neighbours.library, name))
    return os.path.join(neighbours.library, name)


def downloads(neighbours):
    """The folder where voice-overs wait for alignment, inside the work folder the owner named."""
    return os.path.join(neighbours.settings["conform.work_dir"], "downloads")


def conform_dir(neighbours):
    """The folder where the service works, inside the work folder the owner named."""
    return os.path.join(neighbours.settings["conform.work_dir"], "conform")


async def two_voices_ready(dubs, plan=((1, 1),), reference=True, finished=True):
    """One planned episode with two additional voices, downloaded; the reference may be missing."""
    probe, neighbours, voices, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = list(plan)
    ref = reference_on_disk(neighbours) if reference else None
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    if finished:
        for fetch in neighbours.fetches:
            touch(fetch["path"], b"video")
            finish(probe, fetch["key"])
    return probe, neighbours, voices, bus, sub, ref


def job_done(neighbours, job_id, **per_dub):
    """A finished job whose aligned tracks exist; per_dub overrides fields of a dub's result."""
    return job_result(neighbours.submits[-1], job_id, **per_dub)


def job_result(submit, job_id, **per_dub):
    results = []
    for path in submit["dubs"]:
        name = os.path.basename(path)
        out = os.path.join(submit["out_dir"], os.path.splitext(name)[0] + ".flac")
        touch(out, b"flac")
        result = {"dub": name, "ok": True, "skipped": False, "out_path": out, "suspect": False,
                  "critical": [], "warnings": [], "resid_ms": 12.0, "error": None}
        result.update(per_dub.get(os.path.splitext(name)[0].split("_", 2)[-1], {}))  # the voice key ends the name
        results.append(result)
    return {"job_id": job_id, "status": "done", "error": None, "results": results}


@pytest.mark.asyncio
async def test_an_episode_goes_to_alignment_and_its_tracks_land_next_to_the_video(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    submit = neighbours.submits[0]
    assert submit["label"] == "Series s01e01"
    assert submit["ref"] != ref and os.path.basename(submit["ref"]) == "7_s01e01_ref.mp4"
    assert submit["ref"].startswith(conform_dir(neighbours) + "/7/s01e01/_ref/")
    assert open(submit["ref"], "rb").read() == open(ref, "rb").read() and os.path.isfile(ref)
    assert sorted(os.path.basename(p) for p in submit["dubs"]) == ["7_s01e01_BlackCat.mp4", "7_s01e01_RedHeadSound.mp4"]
    key = os.path.basename(submit["out_dir"])
    assert os.path.dirname(submit["out_dir"]).endswith("/7/s01e01/_aligned")
    assert os.path.dirname(submit["cache_dir"]).endswith("/7/s01e01/_cache") and os.path.basename(submit["cache_dir"]) == key
    assert {r["status"] for r in await rows(probe)} == {"aligning"}
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    assert (await asyncio.wait_for(settled_sub.queue.get(), 5)).payload == {
        "series_id": 7, "season": 1, "episode": 1, "part": None}
    stem = os.path.splitext(ref)[0]
    assert os.path.isfile(f"{stem}.BlackCat.ru.flac") and os.path.isfile(f"{stem}.RedHeadSound.ru.flac")
    done = await rows(probe)
    assert {r["status"] for r in done} == {"placed"}
    assert {r["output_path"] for r in done} == {f"{stem}.BlackCat.ru.flac", f"{stem}.RedHeadSound.ru.flac"}
    assert not os.path.exists(os.path.join(downloads(neighbours), "7", "s01e01"))
    assert os.path.isfile(ref)


@pytest.mark.asyncio
async def test_the_episode_waits_for_its_reference_video(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, reference=False)
    await statuses_until(sub, ["ready", "ready"])
    await idle()
    assert neighbours.submits == []
    reference_on_disk(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await statuses_until(sub, ["aligning", "aligning"])
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_the_episode_waits_for_every_voice_still_being_downloaded(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    first, second = neighbours.fetches
    touch(first["path"], b"video")
    finish(probe, first["key"])
    await statuses_until(sub, ["ready"])
    await idle()
    assert neighbours.submits == []
    touch(second["path"], b"video")
    finish(probe, second["key"])
    await statuses_until(sub, ["ready", "aligning", "aligning"])
    assert len(neighbours.submits) == 1 and len(neighbours.submits[0]["dubs"]) == 2


@pytest.mark.asyncio
async def test_a_voice_that_could_not_be_downloaded_does_not_hold_the_episode_back(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    lost, kept = neighbours.fetches
    # RedHeadSound has only the Kodik source for episode 1: its chain ends with one failure.
    red = next(f for f in neighbours.fetches if "RedHeadSound" in f["url"])
    black = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    finish(probe, red["key"], ok=False, error="gone")
    touch(black["path"], b"video")
    finish(probe, black["key"])
    await statuses_until(sub, ["failed", "ready", "aligning"])
    assert [os.path.basename(p) for p in neighbours.submits[0]["dubs"]] == ["7_s01e01_BlackCat.mp4"]


@pytest.mark.asyncio
async def test_a_result_that_is_not_ok_without_the_judgement_of_the_service_fails_its_voice_and_is_never_placed(dubs, caplog):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    settled_sub = bus.subscribe("voices.episode.settled")
    caplog.set_level("WARNING")
    # The service crashed on one pair (it gives no verdict) and said "ok" on the other without any audio: neither judged the voice-over.
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", BlackCat={"ok": False, "error": "CUDA out of memory"},
                                                         RedHeadSound={"out_path": None}))
    await statuses_until(sub, ["failed", "failed"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["BlackCat"]["status"] == "failed" and by_voice["BlackCat"]["error"] == "CUDA out of memory"
    assert by_voice["RedHeadSound"]["status"] == "failed" and code_of(by_voice["RedHeadSound"]["error"]) == "output_missing"
    stem = os.path.splitext(ref)[0]
    assert not any(name.startswith(os.path.basename(stem) + ".") and name.endswith(".flac")
                   for name in os.listdir(neighbours.library))
    assert any("не выровнена" in record.getMessage() for record in caplog.records)
    assert not any("отклонена" in record.getMessage() for record in caplog.records)
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    assert not os.path.exists(os.path.join(downloads(neighbours), "7", "s01e01"))


@pytest.mark.asyncio
async def test_a_pair_the_service_turned_down_itself_is_rejected_and_never_placed(dubs, caplog):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    caplog.set_level("WARNING")
    # Two videos that share no frame: the service looked at the pair and turned it down.
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", BlackCat={
        "ok": False, "refused": True, "error": "чужое видео: сопоставлено 1% (< 60%) — выравнивание прервано"}))
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["BlackCat"]["status"] == "rejected" and by_voice["BlackCat"]["outcome"].startswith("чужое видео")
    assert by_voice["RedHeadSound"]["status"] == "placed"
    assert any("отклонена" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_a_success_without_a_file_fails_that_voice_and_leaves_the_others_alone(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    view = job_done(neighbours, "job-1")
    next(r for r in view["results"] if "redheadsound" in r["dub"].lower())["out_path"] = None
    probe.publish_event("conform.job.finished", view)
    await statuses_until(sub, ["aligned", "placed", "failed"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["RedHeadSound"]["status"] == "failed" and code_of(by_voice["RedHeadSound"]["error"]) == "output_missing"
    assert by_voice["BlackCat"]["status"] == "placed"


@pytest.mark.asyncio
async def test_a_result_that_breaks_the_handler_fails_its_voice_and_not_the_batch(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    real = voices.repo.mark_aligned

    async def broken(dub_id, track):
        if "redheadsound" in track.lower():
            raise RuntimeError("database is locked")
        return await real(dub_id, track)

    monkeypatch.setattr(voices.repo, "mark_aligned", broken)
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "placed", "failed"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert code_of(by_voice["RedHeadSound"]["error"]) == "result_unreadable" and by_voice["BlackCat"]["status"] == "placed"


@pytest.mark.asyncio
async def test_a_failed_settle_is_never_announced_as_placed(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    monkeypatch.setattr(voices._output, "settle", lambda source, destination: False)
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    seen = await statuses_until(sub, ["aligned", "aligned", "failed", "failed"])
    await quiet(voices)
    assert "placed" not in seen and all(r["status"] == "failed" for r in await rows(probe))
    assert not sub.queue.qsize() or all((await sub.queue.get()).payload["status"] != "placed" for _ in range(sub.queue.qsize()))


@pytest.mark.asyncio
async def test_a_missing_result_or_a_failed_job_never_places_anything(dubs):
    probe, neighbours, _, _, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    view = job_done(neighbours, "job-1")
    view["results"] = view["results"][:1]
    probe.publish_event("conform.job.finished", view)
    await statuses_until(sub, ["aligned", "placed", "failed"])
    outcomes = {r["voice_key"]: r for r in await rows(probe)}
    missing = next(r for r in outcomes.values() if r["status"] == "failed")
    assert code_of(missing["error"]) == "no_result"


@pytest.mark.asyncio
async def test_a_job_that_failed_midway_keeps_the_pair_it_had_finished_and_fails_the_rest(dubs):
    probe, neighbours, _, _, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    view = job_done(neighbours, "job-1")
    # The worker of the service died after the first pair: the job is failed, and the finished pair is still in its results.
    view.update(status="failed", error="процесс conform завершился аварийно (-9)", results=view["results"][:1])
    probe.publish_event("conform.job.finished", view)
    await statuses_until(sub, ["aligned", "placed", "failed"])
    by_status = {r["status"]: r for r in await rows(probe)}
    assert by_status["failed"]["error"] == "процесс conform завершился аварийно (-9)"
    assert os.path.isfile(by_status["placed"]["output_path"])


@pytest.mark.asyncio
async def test_a_failed_job_makes_its_voices_failed_for_the_next_scan(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", {"job_id": "job-1", "status": "failed",
                                                 "error": "нет доступа к референсу", "results": []})
    await statuses_until(sub, ["failed", "failed"])
    assert {(r["status"], r["error"]) for r in await rows(probe)} == {("failed", "нет доступа к референсу")}
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)  # offered again from the main source


@pytest.mark.asyncio
async def test_an_unreachable_service_leaves_the_voices_waiting_until_it_is_back(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = SubmitReply.unavailable("сервис выравнивания недоступен", retry=False).model_dump()
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    await idle()
    assert {r["status"] for r in await rows(probe)} == {"ready"}
    neighbours.submit_reply = None
    probe.publish_event("conform.daemon.up", {})
    await statuses_until(sub, ["aligning", "aligning"])


@pytest.mark.asyncio
async def test_a_final_refusal_of_the_request_fails_the_voices_for_the_next_sync(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = SubmitReply.rejected("путь вне общего хранилища").model_dump()
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "failed", "failed"])
    assert {(r["status"], r["error"]) for r in await rows(probe)} == {("failed", "путь вне общего хранилища")}


@pytest.mark.asyncio
async def test_after_a_restart_a_job_that_ended_meanwhile_is_applied(db_path, dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    neighbours.jobs["job-1"] = job_done(neighbours, "job-1")
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        probe.publish_event("conform.daemon.up", {})
        await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
        assert {r["status"] for r in await rows(probe)} == {"placed"}
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_job_the_service_forgot_is_sent_again(db_path, dubs):
    probe, neighbours, voices, bus, sub, _ = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        probe.publish_event("conform.daemon.up", {})
        await statuses_until(sub, ["aligning", "aligning"])
        assert len(neighbours.submits) == 2 and {r["job_id"] for r in await rows(probe)} == {"job-2"}
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_voice_that_arrives_later_makes_a_job_of_its_own(dubs):
    probe, neighbours, _, _, sub, ref = await two_voices_ready(dubs, finished=False)
    red = next(f for f in neighbours.fetches if "RedHeadSound" in f["url"])
    black = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    finish(probe, red["key"], ok=False, error="gone")
    touch(black["path"], b"video")
    finish(probe, black["key"])
    await statuses_until(sub, ["failed", "ready", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "placed"])
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    retry = neighbours.fetches[2]
    touch(retry["path"], b"video")
    finish(probe, retry["key"])
    await statuses_until(sub, ["ready", "aligning"])
    assert len(neighbours.submits) == 2
    assert [os.path.basename(p) for p in neighbours.submits[1]["dubs"]] == ["7_s01e01_RedHeadSound.mp4"]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-2"))
    await statuses_until(sub, ["aligned", "placed"])
    assert {r["status"] for r in await rows(probe)} == {"placed"}


@pytest.mark.asyncio
async def test_a_transient_failure_with_the_service_up_is_retried_without_a_new_event(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = SubmitReply.unavailable("RemoteProtocolError: Server disconnected", retry=True).model_dump()
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    await until_true(lambda: bool(neighbours.submits), "the service was not asked")
    neighbours.submit_reply = None  # the service recovers; nothing else announces it
    await statuses_until(sub, ["aligning", "aligning"])
    assert len(neighbours.submits) >= 2


@pytest.mark.asyncio
async def test_an_obligation_that_keeps_failing_is_reported_and_still_carried(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.02)
    monkeypatch.setattr("modules.voices.module.RETRY_CEILING_S", 0.04)
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRIES", 2)
    probe, neighbours, voices, _, sub, _ = await two_voices_ready(dubs, finished=False)
    told = []
    monkeypatch.setattr(voices.log, "error", lambda message, *args, **kwargs: told.append(message % args))
    neighbours.submit_reply = SubmitReply.unavailable("blip", retry=True).model_dump()
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    await until_true(lambda: len(neighbours.submits) >= 6, "the account of attempts goes on without any outside event",
                     every=RETRY_TICK_S)
    assert len(told) == 1 and "series:7" in told[0], "the owner is told once"
    assert list(voices._recovery.failures()) == ["series:7"]
    neighbours.submit_reply = None
    await statuses_until(sub, ["aligning", "aligning"])
    await until_true(lambda: not voices._recovery.failures(), "the account of failures was not closed", every=RETRY_TICK_S)


@pytest.mark.asyncio
async def test_the_folders_of_the_series_go_when_every_episode_of_the_series_is_settled(dubs):
    probe, neighbours, _, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1), (1, 2)]
    for episode in (1, 2):
        reference_on_disk(neighbours, episode)
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready"] * 4 + ["aligning"] * 4)
    first, second = neighbours.submits
    neighbours.submits[:] = [first]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await idle()
    folders = [os.path.join(base, "7") for base in (downloads(neighbours), conform_dir(neighbours))]
    assert all(os.path.isdir(folder) for folder in folders)  # the second episode's files are still in them
    assert not os.path.exists(os.path.join(conform_dir(neighbours), "7", "s01e01"))
    neighbours.submits[:] = [second]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-2"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await idle()
    assert not any(os.path.exists(folder) for folder in folders)  # nothing of the series is left in the work folder


async def placed_set(dubs):
    """One episode, two voices, aligned and placed next to the reference; the episode is settled."""
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    settled_sub = bus.subscribe("voices.episode.settled")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await quiet(voices)
    return probe, neighbours, bus, sub, ref


def library_files(neighbours):
    found = []
    for folder, _, names in os.walk(neighbours.library):
        found += [os.path.relpath(os.path.join(folder, name), neighbours.library) for name in names]
    return sorted(found)


@pytest.mark.asyncio
async def test_the_tracks_follow_a_renamed_video(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    renamed = os.path.join(neighbours.library, "Renamed 01.mp4")
    os.rename(ref, renamed)
    neighbours.references[1] = "Renamed 01.mp4"
    probe.publish_event("downloads.item.renamed", {"series_id": 7, "unique_id": "u1", "filename": "Renamed 01.mp4"})
    await statuses_until(sub, ["moved", "moved"])
    assert library_files(neighbours) == ["Renamed 01.BlackCat.ru.flac", "Renamed 01.RedHeadSound.ru.flac",
                                         "Renamed 01.mp4"]
    done = await rows(probe)
    assert {r["output_path"] for r in done} == {os.path.join(neighbours.library, "Renamed 01.BlackCat.ru.flac"),
                                                 os.path.join(neighbours.library, "Renamed 01.RedHeadSound.ru.flac")}
    assert {r["status"] for r in done} == {"placed"}


@pytest.mark.asyncio
async def test_the_tracks_follow_a_video_moved_to_another_directory(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    target = os.path.join(neighbours.library, "Season 01", os.path.basename(ref))
    os.makedirs(os.path.dirname(target))
    os.rename(ref, target)
    neighbours.references[1] = os.path.join("Season 01", os.path.basename(ref))
    probe.publish_event("library.relocation.finished", {"series_id": 7, "success": True, "message": "ok"})
    await statuses_until(sub, ["moved", "moved"])
    stem = os.path.splitext(os.path.basename(ref))[0]
    assert library_files(neighbours) == [f"Season 01/{stem}.BlackCat.ru.flac",
                                         f"Season 01/{stem}.RedHeadSound.ru.flac", f"Season 01/{stem}.mp4"]


@pytest.mark.asyncio
async def test_an_unchanged_reference_leaves_the_tracks_alone(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    before = library_files(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    probe.publish_event("conform.daemon.up", {})
    await idle()
    assert sub.queue.empty() and library_files(neighbours) == before
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_track_the_user_deleted_is_not_made_again(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    os.remove(f"{os.path.splitext(ref)[0]}.BlackCat.ru.flac")
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await statuses_until(sub, ["rejected"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["BlackCat"]["status"] == "rejected" and code_of(by_voice["BlackCat"]["outcome"]) == "track_removed"
    assert by_voice["RedHeadSound"]["status"] == "placed"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await idle()
    assert len(neighbours.fetches) == 2 and len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_replaced_reference_removes_the_tracks_and_the_voices_start_over(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    new_ref = another_video(neighbours)
    # The old file is still there with the winner beside it: the winner is the reference.
    neighbours.extra_items.append({"unique_id": "u1", "season": 1, "episode_start": 1, "part": None,
                                   "source_url": "x", "status": "completed", "superseded_by": "u1-new",
                                   "final_filename": os.path.basename(ref)})
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    assert not any(name.endswith(".flac") for name in library_files(neighbours))
    reset = await rows(probe)
    assert {r["output_path"] for r in reset} == {None} and {r["ref_uid"] for r in reset} == {None}
    await settled(neighbours, 4)  # both voices are fetched again
    for fetch in neighbours.fetches[2:]:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    assert os.path.basename(neighbours.submits[1]["ref"]) == "7_s01e01_ref.mp4"
    assert neighbours.submits[1]["ref"] != neighbours.submits[0]["ref"]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-2"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    stem = os.path.splitext(new_ref)[0]
    assert os.path.isfile(f"{stem}.BlackCat.ru.flac") and os.path.isfile(f"{stem}.RedHeadSound.ru.flac")
    assert {r["ref_uid"] for r in await rows(probe)} == {"u1-new"}


@pytest.mark.asyncio
async def test_the_result_of_a_job_for_a_replaced_reference_is_ignored(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    old_job = job_done(neighbours, "job-1")
    another_video(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    probe.publish_event("conform.job.finished", old_job)
    await idle()
    assert not any(name.endswith(".flac") for name in library_files(neighbours))
    assert {r["status"] for r in await rows(probe)} <= {"pending", "downloading"}


@pytest.mark.asyncio
async def test_an_episode_that_left_the_plan_loses_its_tracks_with_its_video(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    os.remove(ref)
    neighbours.plan[:] = []
    neighbours.references.clear()
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 0})
    await statuses_until(sub, ["removed", "removed"])
    assert library_files(neighbours) == [] and await rows(probe) == []


@pytest.mark.asyncio
async def test_an_episode_that_left_the_plan_keeps_its_tracks_while_the_video_is_there(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    neighbours.plan[:] = []
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await idle()
    assert sub.queue.empty() and len(library_files(neighbours)) == 3 and len(await rows(probe)) == 2


@pytest.mark.asyncio
async def test_deleting_a_series_leaves_the_videos_and_their_tracks(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    report = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(report.queue.get(), 5)
    assert len(library_files(neighbours)) == 3 and await rows(probe) == []


@pytest.mark.asyncio
async def test_a_failed_relocation_is_not_a_reason_to_look_at_the_tracks(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    os.rename(ref, ref + ".moved")
    probe.publish_event("library.relocation.finished", {"series_id": 7, "success": False, "message": "boom"})
    await idle()
    assert sub.queue.empty() and len(await rows(probe)) == 2


@pytest.mark.asyncio
async def test_what_the_service_adds_to_an_ok_verdict_is_no_ground_to_refuse_the_track(dubs, caplog):
    """`suspect`, `critical` and `warnings` say what the service found and put right: the track it wrote is in step with the
    reference, so it is placed; the words go to the journal."""
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    caplog.set_level("INFO")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", BlackCat={
        "suspect": True, "warnings": ["сопоставлено лишь 79% кадров"]}, RedHeadSound={"critical": ["нет общего звука"]}))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    stem = os.path.splitext(ref)[0]
    assert os.path.isfile(f"{stem}.BlackCat.ru.flac") and os.path.isfile(f"{stem}.RedHeadSound.ru.flac")
    assert {r["status"] for r in await rows(probe)} == {"placed"}
    said = [record.getMessage() for record in caplog.records if "сервис сообщил" in record.getMessage()]
    assert any("79% кадров" in text for text in said) and any("нет общего звука" in text for text in said)
    assert not any("отклонена" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_an_event_carries_the_voice_and_the_reason_so_the_interface_asks_nothing(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    changes = bus.subscribe("voices.dub.changed")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", BlackCat={"ok": False, "refused": True}))
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    seen = {}
    while not changes.queue.empty():
        event = changes.queue.get_nowait().payload
        seen[(event["voice_key"], event["status"])] = event
    rejected = seen[("BlackCat", "rejected")]
    assert rejected["voice_label"] == "BlackCat" and rejected["detail"]["code"] == "align_failed"
    assert seen[("RedHeadSound", "aligned")]["detail"] is None
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["BlackCat"]["detail"]["code"] == "align_failed" and by_voice["RedHeadSound"]["detail"] is None


def test_every_step_of_a_voice_over_shows_as_one_of_seven_states():
    shown = {status: public_state(status) for status in (
        "pending", "stale", "downloading", "ready", "aligned", "placed", "moved", "rejected", "failed")}
    assert shown == {"pending": "planned", "stale": "planned", "downloading": "downloading",
                     "ready": "align_queued", "aligned": "running", "placed": "done", "moved": "done",
                     "rejected": "error", "failed": "error"}
    seen = set(shown.values()) | {public_state("downloading", fetching=False), public_state("aligning", "running")}
    assert seen == {"planned", "download_queued", "downloading", "align_queued", "running", "done", "error"}


def test_a_download_that_waits_for_a_place_is_queued_not_downloading():
    assert public_state("downloading", fetching=False) == "download_queued"
    assert public_state("downloading", fetching=True) == "downloading"


def test_a_voice_over_in_the_service_waits_or_runs_by_the_state_of_its_job():
    for waiting in (None, "queued", "paused"):
        assert public_state("aligning", waiting) == "align_queued"
    for working in ("running", "done", "failed"):
        assert public_state("aligning", working) == "running"


async def states_of(probe):
    return {r["voice_key"]: r["state"] for r in await rows(probe)}


@pytest.mark.asyncio
async def test_a_voice_over_goes_from_queued_to_running_when_the_service_starts_its_job(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    changes = bus.subscribe("voices.dub.changed")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    seen = []
    while not changes.queue.empty():
        event = changes.queue.get_nowait().payload
        seen.append((event["status"], event["state"]))
    assert ("ready", "align_queued") in seen and seen.count(("aligning", "align_queued")) == 2
    assert set((await states_of(probe)).values()) == {"align_queued"}
    probe.publish_event("conform.queue.changed", {"daemon": {"up": True}, "tasks": [{"id": "job-1", "status": "running"}]})
    await statuses_until(sub, ["aligning", "aligning"])
    assert set((await states_of(probe)).values()) == {"running"}
    # A picture that changes nothing is not an event.
    probe.publish_event("conform.queue.changed", {"daemon": {"up": True}, "tasks": [{"id": "job-1", "status": "running"}]})
    await idle()
    assert sub.queue.empty()
    probe.publish_event("conform.queue.changed", {"daemon": {"up": False}, "tasks": []})
    await statuses_until(sub, ["aligning", "aligning"])
    assert set((await states_of(probe)).values()) == {"align_queued"}


@pytest.mark.asyncio
async def test_the_end_of_a_job_shows_done_and_error_without_any_other_word(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    changes = bus.subscribe("voices.dub.changed")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", RedHeadSound={"ok": False, "refused": True}))
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    states = {}
    while not changes.queue.empty():
        event = changes.queue.get_nowait().payload
        states[(event["voice_key"], event["status"])] = event["state"]
    assert states[("BlackCat", "placed")] == "done" and states[("RedHeadSound", "rejected")] == "error"
    assert await states_of(probe) == {"BlackCat": "done", "RedHeadSound": "error"}


def start(probe, fetch):
    probe.publish_event("downloads.fetch.started", {"key": fetch["key"], "series_id": 7, "path": fetch["path"]})


@pytest.mark.asyncio
async def test_a_sent_download_is_queued_until_it_really_starts_and_downloading_after(dubs):
    probe, neighbours, _, bus, sub, _ = await two_voices_ready(dubs, finished=False)
    changes = bus.subscribe("voices.dub.changed")
    assert set((await states_of(probe)).values()) == {"download_queued"}
    first = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    start(probe, first)
    await change_state(changes, "BlackCat", "downloading")
    assert await states_of(probe) == {"BlackCat": "downloading", "RedHeadSound": "download_queued"}
    touch(first["path"], b"video")
    finish(probe, first["key"])
    ready = await change(changes, "ready")
    assert ready["state"] == "align_queued"
    assert (await states_of(probe))["BlackCat"] == "align_queued"


@pytest.mark.asyncio
async def test_a_sent_download_is_announced_at_once_as_queued(dubs):
    probe, neighbours, _, bus = dubs
    changes = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    sent = [await change(changes, "downloading") for _ in range(3)]
    assert {e["state"] for e in sent} == {"download_queued"} and {e["voice_label"] for e in sent} == {"BlackCat"}


@pytest.mark.asyncio
async def test_the_next_source_of_a_voice_is_queued_again_until_its_own_start(dubs):
    probe, neighbours, _, bus, sub, _ = await two_voices_ready(dubs, finished=False)
    await idle()
    changes = bus.subscribe("voices.dub.changed")
    black = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    start(probe, black)
    await change_state(changes, "BlackCat", "downloading")
    finish(probe, black["key"], ok=False, error="timed out")
    await settled(neighbours, 3)  # the fallback source of BlackCat is sent
    await change_state(changes, "BlackCat", "download_queued")
    assert (await states_of(probe))["BlackCat"] == "download_queued"
    start(probe, neighbours.fetches[2])
    await change_state(changes, "BlackCat", "downloading")
    assert (await states_of(probe))["BlackCat"] == "downloading"


@pytest.mark.asyncio
async def test_a_start_of_a_foreign_download_is_none_of_our_business(dubs):
    probe, neighbours, _, bus, sub, _ = await two_voices_ready(dubs, finished=False)
    await idle()
    changes = bus.subscribe("voices.dub.changed")
    probe.publish_event("downloads.fetch.started", {"key": "other:1", "series_id": 7, "path": "/x"})
    probe.publish_event("downloads.fetch.started", {"key": "dub:99999", "series_id": 7, "path": "/x"})
    await idle()
    assert changes.queue.empty()


async def capped_two_episodes(dubs, cap="1"):
    probe, neighbours, voices, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1), (1, 2)]
    neighbours.settings["voices.max_temp_episodes"] = cap
    for episode in (1, 2):
        reference_on_disk(neighbours, episode)
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    await idle()
    return probe, neighbours, bus, sub


@pytest.mark.asyncio
async def test_with_a_limit_the_next_episode_waits_planned_and_starts_when_a_place_is_free(dubs):
    probe, neighbours, bus, sub = await capped_two_episodes(dubs)
    assert len(neighbours.fetches) == 2 and all("-1" in f["url"] or "#kodik" in f["url"] for f in neighbours.fetches)
    by_episode = {}
    for row in await rows(probe):
        by_episode.setdefault(row["episode"], set()).add(row["state"])
    assert by_episode == {1: {"download_queued"}, 2: {"planned"}}
    settled_sub = bus.subscribe("voices.episode.settled")
    for fetch in list(neighbours.fetches):
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    assert len(neighbours.fetches) == 2  # still no place for the second episode
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await settled(neighbours, 4)
    assert {f["path"].split("/")[-2] for f in neighbours.fetches[2:]} == {"s01e02"}


@pytest.mark.asyncio
async def test_an_episode_whose_voices_all_failed_frees_its_place_too(dubs):
    probe, neighbours, bus, sub = await capped_two_episodes(dubs)
    black = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    red = next(f for f in neighbours.fetches if "RedHeadSound" in f["url"])
    finish(probe, black["key"], ok=False, error="gone")
    await settled(neighbours, 3)  # BlackCat tries its fallback source
    finish(probe, neighbours.fetches[2]["key"], ok=False, error="gone")
    finish(probe, red["key"], ok=False, error="gone")
    await settled(neighbours, 5)
    assert {f["path"].split("/")[-2] for f in neighbours.fetches[3:]} == {"s01e02"}


@pytest.mark.asyncio
async def test_without_a_limit_every_episode_starts_at_once(dubs):
    probe, neighbours, bus, sub = await capped_two_episodes(dubs, cap="0")
    await settled(neighbours, 4)
    assert len(neighbours.fetches) == 4


@pytest.mark.asyncio
async def test_a_waiting_voice_over_is_taken_for_download_only_once(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    row = (await voices.repo.for_series(7))[0]
    sent = len(neighbours.fetches)
    await voices._start(await voices.repo.get(row["id"]))  # taken already: nothing is sent again
    await idle()
    assert len(neighbours.fetches) == sent


@pytest.mark.asyncio
async def test_the_job_of_a_replaced_reference_is_cancelled_in_the_service(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    another_video(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    await idle()
    assert set(neighbours.conform_cancels) == {"job-1"}  # one job for the two voice-overs; a late answer may cancel it twice


@pytest.mark.asyncio
async def test_deleting_a_series_cancels_its_running_job_in_the_service(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    report = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(report.queue.get(), 5)
    await idle()
    assert set(neighbours.conform_cancels) == {"job-1"}  # the journal and a late answer may both cancel the same job


@pytest.mark.asyncio
async def test_a_finished_job_is_taken_out_of_the_history_once_when_its_reference_is_replaced(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    another_video(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])

    async def dropped():
        return neighbours.conform_cancels == ["job-1"]  # one job for the two tracks

    await until_true(dropped, neighbours.conform_cancels)


@pytest.mark.asyncio
async def test_deleting_a_series_takes_its_finished_job_out_of_the_history_and_its_folder_off_the_disk(dubs):
    probe, neighbours, voices, submit = await settled_with_a_record(dubs)
    report = probe.bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(report.queue.get(), 5)

    async def dropped():
        return neighbours.conform_cancels == ["job-1"] and not os.path.exists(submit["out_dir"])

    await until_true(dropped, (neighbours.conform_cancels, os.path.isdir(submit["out_dir"])))


FORM = {"priority": ["kodik", "cvh"], "voice_groups": {"anilibria": ["aniliberty"]}}


@pytest.mark.asyncio
async def test_a_saved_form_applies_at_once_and_is_announced_to_every_open_screen(system):
    announced = system.bus.subscribe("voices.settings.changed")
    saved = await system.request("voices.settings.set", FORM, timeout=5)
    assert saved["saved"] is True and saved["problems"] == []
    assert saved["settings"]["priority"] == ["kodik", "cvh"] and saved["settings"]["voice_groups"] == FORM["voice_groups"]
    assert (await system.request("voices.settings.get", {}, timeout=5)) == saved["settings"]
    assert (await asyncio.wait_for(announced.queue.get(), 5)).payload == saved["settings"]
    # The next question of the layout is answered by the groups just saved, with no restart.
    assert await resolve(system, "AniLiberty", "AniLibria") == {"AniLiberty": "anilibria", "AniLibria": "anilibria"}


@pytest.mark.asyncio
async def test_a_refused_form_writes_nothing_says_why_and_announces_nothing(system):
    announced = system.bus.subscribe("voices.settings.changed")
    refused = await system.request("voices.settings.set", {
        "priority": ["kodik", "nowhere"], "voice_groups": {"a": ["x"], "b": ["x"]}}, timeout=5)
    assert refused["saved"] is False
    assert sorted((problem["field"], problem["detail"]["code"]) for problem in refused["problems"]) == [
        ("priority", "priority_unknown_player"), ("voice_groups", "trigger_in_two_groups")]
    stored = await system.request("settings.values.by_prefix", {"prefix": "voices."}, timeout=5)
    assert stored["values"] == {} and announced.queue.empty()
    assert refused["settings"]["priority"] == list(DEFAULT_PRIORITY)


@pytest.mark.asyncio
async def test_a_stored_value_the_module_cannot_use_is_shown_as_refused_not_as_a_default(system):
    await system.request("settings.value.set", {"key": "voices.groups", "value": "{broken"}, timeout=5)
    view = await system.request("voices.settings.get", {}, timeout=5)
    assert view["rejected"] == ["voice_groups"] and view["voice_groups"] == {}


@pytest.mark.asyncio
async def test_a_player_named_in_the_stored_player_groups_may_stand_in_a_saved_order(system):
    await system.request("settings.value.set", {"key": "voices.player_groups", "value": '{"anilibria": ["aniliberty"]}'}, timeout=5)
    saved = await system.request("voices.settings.set", {"priority": ["anilibria", "kodik"], "voice_groups": {}}, timeout=5)
    assert saved["saved"] is True and saved["settings"]["priority"] == ["anilibria", "kodik"]


def test_two_voices_with_the_same_safe_title_get_two_names():
    from modules.voices.placement import sidecar_path as path_of
    first = path_of("/lib/Show s01e01.mp4", "AniMy / RuChiMe", ".flac")
    assert first == "/lib/Show s01e01.AniMy RuChiMe.ru.flac"
    second = path_of("/lib/Show s01e01.mp4", "AniMy RuChiMe", ".flac", {first})
    third = path_of("/lib/Show s01e01.mp4", "AniMy: RuChiMe", ".flac", {first, second})
    assert second == "/lib/Show s01e01.AniMy RuChiMe 2.ru.flac" and third.endswith("RuChiMe 3.ru.flac")


@pytest.mark.asyncio
async def test_a_track_never_overwrites_the_track_of_another_voice(dubs):
    probe, neighbours, voices, bus = dubs
    # Voices the layout could not group are numbered; their names differ by a digit and are as like as titles can be.
    neighbours.sources.append(linked("kodik", "Неизвестная озвучка 1", range(1, 4)))
    neighbours.sources.append(linked("kodik", "Неизвестная озвучка 2", range(1, 4)))
    neighbours.plan[:] = [(1, 1)]
    reference_on_disk(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    assert len({r["voice_key"] for r in await rows(probe)}) == 3
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready"] * 3 + ["aligning"] * 3)
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    placed = {r["output_path"] for r in await rows(probe)}
    assert len(placed) == 3 and all(os.path.isfile(p) for p in placed)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await idle()
    assert {r["output_path"] for r in await rows(probe)} == placed  # a later look moves nothing


async def serve(probe, neighbours, holder, finished, progress, seconds=60.0):
    """Stands in for downloads and the alignment service until `finished()` is true.

    `progress` remembers how much has been served, so a second call never serves anything twice.
    Returns the most episodes that held temporary files at one moment."""
    peak = 0
    end = asyncio.get_running_loop().time() + seconds
    while not await finished():
        if asyncio.get_running_loop().time() >= end:
            from collections import Counter
            stuck = Counter((r["episode"], r["status"]) for r in await rows(probe) if r["status"] != "pending")
            raise AssertionError((len(neighbours.fetches), len(neighbours.submits), sorted(stuck.items()),
                                  sorted(await holder[0].repo.active_episodes())))
        for fetch in neighbours.fetches[progress["fetches"]:]:
            start(probe, fetch)
            touch(fetch["path"], b"video")
            finish(probe, fetch["key"])
        progress["fetches"] = len(neighbours.fetches)
        for number, submit in enumerate(neighbours.submits[progress["submits"]:], start=progress["submits"] + 1):
            view = job_result(submit, f"job-{number}")
            neighbours.jobs[f"job-{number}"] = view
            probe.publish_event("conform.job.finished", view)
        progress["submits"] = len(neighbours.submits)
        peak = max(peak, len(await holder[0].repo.active_episodes()))
        await next_step()
    return peak


async def a_series_of(dubs, episodes, voices_count, cap):
    probe, neighbours, voices, bus = dubs
    names = ["BlackCat", "RedHeadSound", "AniStar", "Animy", "FumoSub", "Lostfilm"][:voices_count]
    reference = linked("cvh", "FumoDub", episodes, url="https://site/?player=cvh&voice=FumoDub")
    neighbours.sources[:] = [reference] + [linked("kodik", name, episodes) for name in names]
    neighbours.reference_url = reference["url"]
    neighbours.plan[:] = [(1, n) for n in episodes]
    neighbours.settings["voices.max_temp_episodes"] = str(cap)
    for n in episodes:
        reference_on_disk(neighbours, n)
    return probe, neighbours, voices, bus


@pytest.mark.asyncio
async def test_twelve_episodes_of_four_voices_flow_through_under_a_limit_of_three(dubs, caplog):
    caplog.set_level("WARNING")
    episodes = range(1, 13)
    probe, neighbours, voices, bus = await a_series_of(dubs, episodes, 4, 3)
    probe.send_command("voices.dispatch", {"series_id": 7})

    async def all_placed():
        return sum(r["status"] == "placed" for r in await rows(probe)) == 48

    peak = await serve(probe, neighbours, [voices], all_placed, {"fetches": 0, "submits": 0})
    assert peak <= 3
    keys = [f["key"] for f in neighbours.fetches]
    assert len(keys) == 48 == len(set(keys))  # every voice-over of every episode was fetched once
    assert len(neighbours.submits) == 12 and all(len(s["dubs"]) == 4 for s in neighbours.submits)
    assert len(neighbours.submits) == len({s["ref"] for s in neighbours.submits})
    await idle()
    flacs = [name for name in library_files(neighbours) if name.endswith(".flac")]
    assert len(flacs) == 48 and neighbours.conform_cancels == []
    assert not os.path.exists(downloads(neighbours)) or os.listdir(downloads(neighbours)) == []
    # The jobs' records are in the service's history, so their folders stay; the copies of the references are gone.
    assert not [name for _, names, _ in os.walk(conform_dir(neighbours)) for name in names if name == "_ref"]
    assert not any("нет на месте" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
async def test_the_whole_series_still_arrives_when_the_module_restarts_midway(dubs, db_path, caplog):
    caplog.set_level("WARNING")
    episodes = range(1, 9)
    probe, neighbours, voices, bus = await a_series_of(dubs, episodes, 3, 2)
    holder = [voices]
    probe.send_command("voices.dispatch", {"series_id": 7})

    async def placed_at_least(count):
        return sum(r["status"] == "placed" for r in await rows(probe)) >= count

    async def enough():
        return await placed_at_least(8)

    progress = {"fetches": 0, "submits": 0}
    await serve(probe, neighbours, holder, enough, progress)
    await voices.stop()
    progress["fetches"], progress["submits"] = len(neighbours.fetches), len(neighbours.submits)
    again = VoicesModule(bus, Database(db_path))
    holder[0] = again
    await restarted(again)
    probe.publish_event("conform.daemon.up", {})

    async def all_placed():
        return sum(r["status"] == "placed" for r in await rows(probe)) == 24

    try:
        await serve(probe, neighbours, holder, all_placed, progress)
        await idle()
        assert len([n for n in library_files(neighbours) if n.endswith(".flac")]) == 24
        assert {r["status"] for r in await rows(probe)} == {"placed"}
        assert not any("нет на месте" in record.getMessage() for record in caplog.records)
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_job_that_ends_before_the_voices_know_its_number_still_ends_them(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    neighbours.finish_at_once = True
    settled_sub = bus.subscribe("voices.episode.settled")
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    try:
        await asyncio.wait_for(settled_sub.queue.get(), 5)
    except asyncio.TimeoutError:
        raise AssertionError([(r["status"], r["job_id"], r["detail"]) for r in await rows(probe)],
                             list(voices._unclaimed), neighbours.submits) from None
    assert {r["status"] for r in await rows(probe)} == {"placed"}
    assert not voices._unclaimed


@pytest.mark.asyncio
async def test_a_late_ending_of_a_download_does_not_turn_a_waiting_voice_into_a_ready_one(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    row = (await rows(probe))[0]
    stale = (await voices.repo.for_series(7))[0]
    await voices.repo.advance(stale["id"], "x")  # waits for its next source: not downloading now
    finish(probe, f"dub:{stale['id']}")
    await idle()
    after = await voices.repo.get(stale["id"])
    assert after["status"] == "pending" and row["voice_key"] == after["voice_key"]


async def aligned_but_not_placed(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    view = job_done(neighbours, "job-1")
    for row in await voices.repo.for_series(7):
        result = next(r for r in view["results"] if os.path.splitext(r["dub"])[0] == os.path.splitext(
            os.path.basename(row["temp_path"]))[0])
        await voices.repo.mark_aligned(row["id"], result["out_path"])
    return probe, neighbours, voices, bus, ref


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted_at", ["before", "copied", "moved"])
async def test_an_interrupted_placement_is_finished_after_a_restart(dubs, interrupted_at):
    import shutil

    probe, neighbours, voices, bus, ref = await aligned_but_not_placed(dubs)
    row = (await voices.repo.for_series(7))[0]
    from modules.voices.placement import place as place_file
    from modules.voices.placement import sidecar_path as path_of
    source = row["aligned_path"]
    destination = path_of(ref, row["voice_label"], ".flac")
    await voices.repo.begin_move(row["id"], destination, source)  # the intent is written first
    if interrupted_at == "copied":
        shutil.copyfile(source, destination)  # the destination appeared, the source is still there
    elif interrupted_at == "moved":
        place_file(source, destination)  # the file moved, the record was not updated
    await voices._advance(7)
    after = await voices.repo.get(row["id"])
    assert after["status"] == "placed" and after["output_path"] == destination
    assert after["move_from"] is None and after["aligned_path"] is None
    assert os.path.isfile(destination) and not os.path.exists(source)


@pytest.mark.asyncio
async def test_a_track_that_is_in_neither_place_after_an_interruption_is_an_error_not_silence(dubs):
    probe, neighbours, voices, bus, ref = await aligned_but_not_placed(dubs)
    row = (await voices.repo.for_series(7))[0]
    await voices.repo.begin_move(row["id"], os.path.join(neighbours.library, "x.flac"), row["aligned_path"])
    os.remove(row["aligned_path"])
    await voices._advance(7)
    after = await voices.repo.get(row["id"])
    assert after["status"] == "failed" and code_of(after["error"]) == "track_vanished_on_move"


@pytest.mark.asyncio
async def test_an_interrupted_follow_of_the_video_is_finished_too(dubs):
    from modules.voices.placement import sidecar_path as path_of

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    row = (await voices.repo.for_series(7))[0]
    renamed = os.path.join(neighbours.library, "Renamed 01.mp4")
    os.rename(ref, renamed)
    neighbours.references[1] = "Renamed 01.mp4"
    expected = path_of(renamed, row["voice_label"], ".flac")
    await voices.repo.begin_move(row["id"], expected, row["output_path"])  # interrupted before the move
    await voices._advance(7)
    after = await voices.repo.get(row["id"])
    assert after["status"] == "placed" and after["output_path"] == expected and after["move_from"] is None
    assert os.path.isfile(expected) and not os.path.exists(row["output_path"])
    assert len(library_files(neighbours)) == 3


@pytest.mark.asyncio
async def test_a_move_begun_for_a_placed_track_is_finished_by_the_next_start(db_path, dubs):
    from modules.voices.placement import sidecar_path as path_of

    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    await quiet(voices)
    row = (await voices.repo.for_series(7))[0]
    renamed = os.path.join(neighbours.library, "Renamed 01.mp4")
    os.rename(ref, renamed)
    neighbours.references[1] = "Renamed 01.mp4"
    expected = path_of(renamed, row["voice_label"], ".flac")
    await voices.repo.begin_move(row["id"], expected, row["output_path"])  # the module stops right here
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        async def taken_up():
            return (await again.repo.get(row["id"]))["move_from"] is None

        assert await wait_until(taken_up, bus, again, seconds=5.0), "the begun move was not taken up at the start"
        assert os.path.isfile(expected) and not os.path.exists(row["output_path"])
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_failed_removal_hands_the_file_to_the_cleanup_journal_and_is_tried_again(dubs):
    from unittest.mock import patch

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    group = await voices.repo.for_series(7)
    paths = [row["output_path"] for row in group]
    with patch("modules.voices.module.os.remove", side_effect=PermissionError("read-only")):
        assert await voices._follow(7, 1, 1, 0, group, ref, "another-reference")
    assert all(os.path.exists(path) for path in paths)  # the tracks are still there...
    assert {entry["path"] for entry in await voices.repo.cleanup_list()} == set(paths)  # ...and not forgotten
    assert all(row["output_path"] is None for row in await voices.repo.for_series(7))
    async def swept():  # a name another attempt still owns is answered "not now": the sweep is made again
        await voices._cleanup.sweep()
        return not any(os.path.exists(path) for path in paths) and await voices.repo.cleanup_list() == []

    await until_true(swept, "the journal was not carried out")


@pytest.mark.asyncio
async def test_a_journaled_path_that_a_voice_over_holds_again_is_not_removed(dubs):
    from unittest.mock import patch

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    group = await voices.repo.for_series(7)
    paths = [row["output_path"] for row in group]
    with patch("modules.voices.module.os.remove", side_effect=PermissionError("read-only")):
        assert await voices._follow(7, 1, 1, 0, group, ref, "another-reference")
        for row in group:  # the new reference got its tracks under the same names, before any removal can succeed
            await voices.repo.set_output(row["id"], row["output_path"], ref)
        await quiet(voices)
    await voices._cleanup.sweep()
    assert all(os.path.exists(path) for path in paths) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_failed_removal_of_the_temporary_folder_is_journaled_and_the_episode_still_settles(dubs):
    from unittest.mock import patch

    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    settled_sub = bus.subscribe("voices.episode.settled")
    with patch("modules.voices.module.shutil.rmtree", side_effect=PermissionError("busy")):
        result = job_done(neighbours, "job-1")
        neighbours.jobs["job-1"] = result  # the record of the job is in the service's history: its folder stays
        probe.publish_event("conform.job.finished", result)
        await asyncio.wait_for(settled_sub.queue.get(), 5)
        journal = await voices.repo.cleanup_list()
    assert len(journal) == 2 and {entry["kind"] for entry in journal} == {"tree"}  # the voice-overs' folder and the copy of the reference
    assert all(os.path.isdir(entry["path"]) for entry in journal)
    await quiet(voices)  # a pass of the module that runs its own sweep owns the path while it removes it
    await voices._cleanup.sweep()
    assert not any(os.path.exists(entry["path"]) for entry in journal) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_file_that_is_already_gone_is_a_success_not_a_journal_entry(dubs):
    voices = dubs[2]
    assert await voices._cleanup.discard(os.path.join(os.sep, "tmp", "nobody-has-this")) is True
    assert await voices._cleanup.discard(os.path.join(os.sep, "tmp", "nobody-has-this-dir"), kind="tree") is True
    assert await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_temporary_files_are_removed_where_they_were_made_not_where_the_settings_now_point(dubs, tmp_path):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    made_in, worked_in = downloads(neighbours), conform_dir(neighbours)
    neighbours.settings["conform.work_dir"] = str(tmp_path / "somewhere-else")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await quiet(voices)
    assert not os.path.exists(os.path.join(made_in, "7", "s01e01"))
    assert not os.path.exists(os.path.join(worked_in, "7", "s01e01"))
    assert not os.path.exists(tmp_path / "somewhere-else")


@pytest.mark.asyncio
async def test_a_removal_that_the_storage_does_not_answer_is_journaled_not_awaited_forever(dubs, monkeypatch):
    import time

    voices = dubs[2]
    monkeypatch.setattr("modules.voices.cleanup.REMOVE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(voices._cleanup, "unlink", lambda path, kind: time.sleep(0.6))
    started = time.monotonic()
    assert await voices._cleanup.discard("/some/slow/file") is False
    assert time.monotonic() - started < 0.5
    assert [entry["path"] for entry in await voices.repo.cleanup_list()] == ["/some/slow/file"]


async def stalled_removal(path, release):
    """A removal of `path` that the storage does not answer: the wait ends, the call goes on."""
    from core import storage
    with pytest.raises(storage.StorageTimeout):
        await storage.call(lambda: release.wait(10), timeout=0.05, owns=path)


@pytest.mark.asyncio
async def test_a_name_whose_removal_has_not_ended_is_not_given_to_a_new_download_until_it_has(dubs):
    import threading

    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    taken = neighbours.fetches[0]["path"]
    release = threading.Event()
    await stalled_removal(taken, release)
    neighbours.fetches.clear()
    await voices.repo.requeue_interrupted()  # every download is waiting to be started again
    await voices._admit()
    await settled(neighbours, 2)
    assert taken not in [fetch["path"] for fetch in neighbours.fetches]  # the other names go on
    assert len(neighbours.fetches) == 2
    release.set()
    await settled(neighbours, 3)  # the name is free again: the voice-over takes it
    assert taken in [fetch["path"] for fetch in neighbours.fetches]


@pytest.mark.asyncio
async def test_the_cleanup_sweep_leaves_an_entry_alone_while_its_removal_still_runs(dubs, tmp_path):
    import threading

    voices = dubs[2]
    path = str(tmp_path / "stuck.flac")
    touch(path, b"flac")
    release = threading.Event()
    await stalled_removal(path, release)
    await voices.repo.journal_cleanup(path, "path", "timed out")
    await voices._cleanup.sweep()
    assert [entry["path"] for entry in await voices.repo.cleanup_list()] == [path] and os.path.exists(path)
    release.set()
    await idle()
    await voices._cleanup.sweep()
    assert await voices.repo.cleanup_list() == [] and not os.path.exists(path)


@pytest.mark.asyncio
async def test_tracks_are_removed_with_their_reference_whatever_the_clock_of_the_storage_says(dubs):
    import time

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    group = await voices.repo.for_series(7)
    paths = [row["output_path"] for row in group]
    ahead = time.time_ns() + 3600 * 10**9  # the share and this server disagree about the time
    for path in paths:
        os.utime(path, ns=(ahead, ahead))
    assert await voices._follow(7, 1, 1, 0, group, ref, "another-reference")
    assert not any(os.path.exists(path) for path in paths) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["none", "refused", "stays", "owned"])
async def test_a_removal_ends_with_the_file_gone_or_on_the_journal_and_never_neither(dubs, tmp_path, fault):
    import threading
    from unittest.mock import patch

    voices = dubs[2]
    path = str(tmp_path / "track.flac")
    touch(path, b"flac")
    release = threading.Event()
    try:
        if fault == "refused":
            with patch("modules.voices.module.os.remove", side_effect=PermissionError("read-only")):
                gone = await voices._cleanup.discard(path)
        elif fault == "stays":  # the call returns without error and the name is still taken
            with patch("modules.voices.module.os.remove"):
                gone = await voices._cleanup.discard(path)
        elif fault == "owned":
            await stalled_removal(path, release)
            gone = await voices._cleanup.discard(path)
        else:
            gone = await voices._cleanup.discard(path)
        journaled = path in {entry["path"] for entry in await voices.repo.cleanup_list()}
        assert gone is not journaled and gone is (not os.path.exists(path))
        assert gone is (fault == "none")
    finally:
        release.set()


@pytest.mark.asyncio
async def test_a_journaled_name_inside_the_folder_a_running_job_writes_to_is_left_for_later(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    out_dir = (await voices.repo.for_series(7))[0]["out_dir"]
    stale = os.path.join(out_dir, "left-over.flac")
    touch(stale, b"flac")
    await voices.repo.journal_cleanup(stale, "path", "was busy")
    assert await voices._cleanup.clear(stale) is False
    assert os.path.exists(stale) and [e["path"] for e in await voices.repo.cleanup_list()] == [stale]


async def ready_to_send(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    return probe, neighbours, voices, bus, sub, ref


async def job_numbers_known(probe, expected, seconds=5.0):
    """Wait until every voice-over has its job number: the interface is told of a lost answer before it is found."""
    async def known():
        return {r["job_id"] for r in await rows(probe)} == expected

    await until_true(known, "the voice-overs do not all have their job numbers", seconds, every=RETRY_TICK_S)


def finish_all(probe, neighbours):
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])


@pytest.mark.asyncio
async def test_a_job_that_arrived_but_whose_answer_was_lost_is_found_again_not_sent_twice(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    settled_sub = bus.subscribe("voices.episode.settled")
    finish_all(probe, neighbours)
    await job_numbers_known(probe, {"job-1"})  # the number is found again
    assert len(neighbours.submits) == 1
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    assert {r["status"] for r in await rows(probe)} == {"placed"} and len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_an_http_answer_that_never_came_back_is_asked_for_by_the_key_not_sent_twice(dubs_with_conform, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    daemon = dubs_with_conform[1].daemon
    daemon.lose_answers = 1  # the daemon takes the job, the read times out
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs_with_conform, finished=False)
    finish_all(probe, neighbours)
    async def aligning():
        return all(r["status"] == "aligning" and r["job_id"] for r in await rows(probe))

    await until_true(aligning, "the voice-overs are not aligning", 8.0)
    assert len(daemon.enqueued) == 1
    assert {r["job_id"] for r in await rows(probe)} == {"j-1"}


@pytest.mark.asyncio
async def test_the_service_is_given_and_asked_to_write_only_inside_the_work_folder(dubs_with_conform):
    daemon = dubs_with_conform[1].daemon
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs_with_conform, finished=False)
    finish_all(probe, neighbours)
    await until_true(lambda: bool(daemon.enqueued), "the daemon was not given the job", 8.0)
    body = daemon.enqueued[0]
    paths = [body["ref"], *body["dubs"], body["out_dir"], body["cache_dir"]]
    assert all(path.startswith("/share/work/") for path in paths), paths  # not one path of the library
    assert body["ref"].startswith("/share/work/conform/7/s01e01/_ref/")
    assert os.path.basename(body["ref"]) == "7_s01e01_ref.mp4"
    assert all(path.startswith("/share/work/downloads/7/s01e01/7_s01e01_") for path in body["dubs"])
    assert body["out_dir"].startswith("/share/work/conform/7/s01e01/_aligned/")


@pytest.mark.asyncio
async def test_tracks_of_one_job_are_found_where_the_job_asked_for_them_whatever_roots_the_voices_came_from(dubs, tmp_path):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    first, second = sorted(await voices.repo.for_series(7), key=lambda row: row["id"])
    fetches = {fetch["key"]: fetch for fetch in neighbours.fetches}
    touch(fetches[f"dub:{first['id']}"]["path"], b"video")
    finish(probe, f"dub:{first['id']}")
    moved = str(tmp_path / "another-root" / "7" / "s01e01" / os.path.basename(fetches[f"dub:{second['id']}"]["path"]))
    touch(moved, b"video")  # the setting of the temporary root changed between the two downloads
    await voices.repo._db.execute("UPDATE voice_dubs SET temp_path=? WHERE id=?", (moved, second["id"]))
    finish(probe, f"dub:{second['id']}")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    assert {r["status"] for r in await rows(probe)} == {"placed"}


@pytest.mark.asyncio
async def test_a_job_that_never_arrived_is_sent_again_once_the_service_says_it_has_none(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_before_arrival = 1
    finish_all(probe, neighbours)
    await job_numbers_known(probe, {"job-1"})
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_while_the_service_cannot_say_whether_it_has_the_job_nothing_is_sent_again(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    neighbours.service_reachable = False
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    await idle()
    assert len(neighbours.submits) == 1
    assert {(r["status"], r["job_id"]) for r in await rows(probe)} == {("aligning", None)}
    neighbours.service_reachable = True
    probe.publish_event("conform.daemon.up", {})
    await job_numbers_known(probe, {"job-1"})
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_cancel_that_could_not_be_made_is_kept_and_made_when_the_service_is_back(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    neighbours.cancel_done = False
    another_video(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])

    assert [e["path"] for e in await voices.repo.cleanup_list()] == ["job-1"]
    neighbours.cancel_done = True
    probe.publish_event("conform.daemon.up", {})
    async def journal_empty():
        return not await voices.repo.cleanup_list()

    await until_true(journal_empty, "the journal of cleanup was not emptied")
    assert neighbours.conform_cancels.count("job-1") >= 2


def drained(sub):
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait().payload)
    return events


def picture(events, snapshot=None):
    """The interface's reduction of the pushed packets: the newest revision of a voice-over wins."""
    shown, revs = {}, {}
    if snapshot is not None:
        for item in snapshot["items"]:
            key = (item["series_id"], item["season"], item["episode"], item["voice_key"])
            shown[key], revs[key] = item, (snapshot["epoch"], snapshot["rev"])
    for event in events:
        key = (event["series_id"], event["season"], event["episode"], event["voice_key"])
        if (event["epoch"], event["rev"]) <= revs.get(key, (0, 0)):
            continue
        revs[key] = (event["epoch"], event["rev"])
        if event["status"] == "removed":
            shown.pop(key, None)
        else:
            shown[key] = event
    return {key: (item["state"], item["detail"]) for key, item in shown.items()}


async def held(probe):
    return {(7, row["season"], row["episode"], row["voice_key"]): (row["state"], row["detail"])
            for row in await rows(probe)}


@pytest.mark.asyncio
async def test_planned_rows_are_announced_when_created_and_when_they_leave_the_plan(dubs):
    probe, neighbours, bus, sub = await capped_two_episodes(dubs)
    events = drained(sub)
    assert {(e["episode"], e["state"]) for e in events if e["status"] == "pending"} == {(1, "planned"), (2, "planned")}
    assert picture(events) == await held(probe)
    neighbours.plan[:] = [(1, 1)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await idle()
    later = drained(sub)
    assert {(e["episode"], e["status"]) for e in later} == {(2, "removed")} and len(later) == 2
    assert picture(events + later) == await held(probe)


@pytest.mark.asyncio
async def test_deleting_the_series_tells_the_interface_every_row_is_gone(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await idle()
    before = drained(sub)
    assert picture(before) and picture(before) == await held(probe)
    purged = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(purged.queue.get(), 3)
    after = drained(sub)
    assert {e["status"] for e in after} == {"removed"} and picture(before + after) == {}


@pytest.mark.asyncio
async def test_revisions_only_grow(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    stamps = [(e["epoch"], e["rev"]) for e in drained(sub)]
    assert stamps == sorted(set(stamps))


@pytest.mark.asyncio
async def test_a_later_start_of_the_module_outranks_an_earlier_one_whatever_the_clock_says(db_path, dubs, monkeypatch):
    import time
    probe, neighbours, voices, bus = dubs
    every = bus.subscribe("voices.dub.changed")
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    first = drained(every)
    await voices.stop()
    monkeypatch.setattr(time, "time_ns", lambda: 1)  # the clock is set far back between the two runs
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        packets = bus.subscribe("voices.dubs.snapshot")
        probe.publish_event("gateway.sse.clients", {"count": 1})
        snapshot = (await asyncio.wait_for(packets.queue.get(), 3)).payload
        assert snapshot["epoch"] > max(event["epoch"] for event in first)
        assert (snapshot["epoch"], snapshot["rev"]) > max((event["epoch"], event["rev"]) for event in first)
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_whole_episode_leaves_the_interface_with_the_picture_the_database_holds(dubs):
    probe, neighbours, voices, bus = dubs
    every = bus.subscribe("voices.dub.changed")
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    await quiet(voices)
    assert picture(drained(every)) == await held(probe)


@pytest.mark.asyncio
async def test_a_connecting_interface_gets_every_row_at_once_and_newer_packets_win(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    packets = bus.subscribe("voices.dubs.snapshot")
    probe.publish_event("gateway.sse.clients", {"count": 0})
    probe.publish_event("gateway.sse.clients", {"count": 1})
    snapshot = (await asyncio.wait_for(packets.queue.get(), 3)).payload
    assert packets.queue.empty()
    assert picture([], snapshot) == await held(probe)
    assert all(item["rev"] <= snapshot["rev"] for item in drained(sub))
    older = {"series_id": 7, "season": 1, "episode": 1, "voice_key": "BlackCat", "voice_label": "BlackCat",
             "status": "failed", "state": "error", "detail": "old", "epoch": snapshot["epoch"],
             "rev": snapshot["rev"] - 1}
    assert picture([older], snapshot) == await held(probe)


@pytest.mark.asyncio
async def test_a_wrong_setting_is_logged_once_and_the_default_is_used(dubs, caplog):
    probe, neighbours, voices, bus = dubs
    neighbours.settings["voices.max_temp_episodes"] = "many"
    with caplog.at_level("WARNING"):
        probe.send_command("voices.dispatch", {"series_id": 7})
        await settled(neighbours, 3)
        probe.send_command("voices.dispatch", {"series_id": 7})
        await idle()
    named = [r for r in caplog.records if "max_temp_episodes" in r.getMessage()]
    assert len(named) == 1


@pytest.mark.asyncio
async def test_the_aligned_track_is_found_where_the_module_asked_for_it_whatever_the_services_path_map(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    view = job_done(neighbours, "job-1")
    for result in view["results"]:
        result["out_path"] = "/mnt/meganas/media/elsewhere/" + os.path.basename(result["out_path"])
    probe.publish_event("conform.job.finished", view)
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    assert all(os.path.isfile(r["output_path"]) for r in await rows(probe))


class FolderOutput:
    """Another form of output: the tracks of a video in a folder `audio` next to it."""

    def destination(self, reference, label, source, taken):
        number = 1
        while True:
            path = os.path.join(os.path.dirname(reference), "audio", f"{os.path.basename(reference)}.{label}{number}.flac")
            if path not in taken:
                return path
            number += 1

    def exists(self, artifact):
        return os.path.isfile(artifact)

    def settle(self, source, destination):
        if os.path.isfile(source):
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.move(source, destination)
            return True
        return os.path.isfile(destination)

    def remove(self, artifact):
        os.remove(artifact)


@pytest.fixture
def folder_output(monkeypatch):
    monkeypatch.setattr("modules.voices.module.SidecarOutput", FolderOutput)


@pytest.mark.asyncio
async def test_another_form_of_output_places_follows_and_removes_without_a_change_in_the_module(folder_output, dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    placed = await rows(probe)
    assert all(os.path.join(os.path.dirname(ref), "audio") in r["output_path"] and os.path.isfile(r["output_path"])
               for r in placed)
    another_video(neighbours)
    neighbours.extra_items.append({"unique_id": "u1", "season": 1, "episode_start": 1, "part": None, "source_url": "x",
                                   "status": "completed", "superseded_by": "u1-new",
                                   "final_filename": os.path.basename(ref)})
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    assert not any(os.path.isfile(r["output_path"]) for r in placed)


@pytest.mark.asyncio
async def test_a_job_and_its_ending_that_meet_in_the_middle_of_each_others_checks_still_end_the_voices(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    submit = neighbours.submits[-1]
    ids = [r["id"] for r in await voices.repo.for_series(7)]
    await voices.repo.release_to_ready(ids)
    await voices.repo.mark_submitting(ids, "k9", "u1", ref, submit["out_dir"])
    ending = Envelope(topic="conform.job.finished", kind="event", payload=job_result(submit, "job-9"))
    real = voices.repo.by_job

    async def slow_after_reading(job_id):
        found = await real(job_id)
        await idle()
        return found

    voices.repo.by_job = slow_after_reading
    try:
        ended = asyncio.create_task(voices.on_job_finished(ending))
        await idle()
        await voices._attach("k9", "job-9")
        await ended
    finally:
        voices.repo.by_job = real
    await quiet(voices)
    assert not voices._unclaimed
    assert {r["status"] for r in await rows(probe)} <= {"aligned", "placed"}


@pytest.mark.asyncio
async def test_an_unknown_submission_is_asked_for_again_with_no_outside_event(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    neighbours.service_reachable = False  # the look at the service fails; the event feed stays up
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    await quiet(voices)
    assert len(neighbours.submits) == 1 and all(r["job_id"] is None for r in await rows(probe))
    assert voices._recovery.failures().keys() == {"submission:" + next(iter(neighbours.sent_keys))}
    neighbours.service_reachable = True
    async def numbered():
        return all(r["job_id"] for r in await rows(probe))

    await until_true(numbered, "the voice-overs did not get their job numbers")
    await until_true(lambda: not voices._recovery.failures(), "the account of failures was not closed")
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_reply_that_is_not_a_clear_answer_is_no_answer_and_the_interface_is_told(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.submit_reply = {"job_id": "job-1"}  # the old shape: not an outcome, so not an answer
    neighbours.running_jobs.add("job-1")  # the service took the job; only its answer was unreadable
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    waiting = await change(sub, "aligning")
    assert waiting["hint"] == "awaiting_answer" and waiting["state"] == "align_queued"
    neighbours.submit_reply = None
    async def found():
        return all(r["job_id"] == "job-1" for r in await rows(probe))

    await until_true(found, "the job was not found by its number")
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_job_whose_ending_could_not_be_learned_is_asked_about_again_by_itself(dubs, db_path, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    await voices.stop()
    neighbours.jobs["job-1"] = job_done(neighbours, "job-1")
    neighbours.job_get_failures = 2  # the service is up for the feed and does not answer the question twice
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
        assert {r["status"] for r in await rows(probe)} == {"placed"} and neighbours.job_get_failures == 0
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_job_that_could_not_be_cancelled_is_cancelled_when_the_service_can_be_reached_again(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    voices, neighbours = dubs[2], dubs[1]
    neighbours.cancel_done = False
    await voices._cancel_job("job-9")
    assert [(e["path"], e["kind"]) for e in await voices.repo.cleanup_list()] == [("job-9", "job")]
    neighbours.cancel_done = True
    async def journal_empty():
        return not await voices.repo.cleanup_list()

    await until_true(journal_empty, "the journal of cleanup was not emptied", every=RETRY_TICK_S)
    assert neighbours.conform_cancels.count("job-9") >= 2


@pytest.mark.asyncio
async def test_a_sent_download_is_announced_before_its_command_goes_out(dubs, monkeypatch):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    announced_at_send = []
    real = voices.send_command

    def watching(topic, payload, *args, **kwargs):
        if topic == "downloads.fetch":
            announced_at_send.append(sum(1 for env in list(sub.queue._queue) if env.payload["status"] == "downloading"))
        return real(topic, payload, *args, **kwargs)

    monkeypatch.setattr(voices, "send_command", watching)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    assert announced_at_send == [1, 2, 3], "each command finds its announcement already published"


def another_video(neighbours) -> str:
    """The reference of episode 1 becomes a file of another video (another row of the table), so the tracks made for the
    old one are stale; a better resolution of the same video would keep them (ЭТАП_11, п. 5)."""
    path = os.path.join(neighbours.library, "Series s01e01 2160p.mp4")
    touch(path)
    neighbours.uids[1] = "u1-new"
    neighbours.item_keys[1] = "elsewhere-1"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    return path


async def reference_replaced(neighbours, probe):
    """The video of the episode is replaced by another one: the tracks made for the old one are stale."""
    another_video(neighbours)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})


# The module carries an obligation again every SUBMIT_RETRY_S, and the tests that watch it do that set it to this.
RETRY_TICK_S = 0.05


async def until_true(condition, what, seconds=5.0, every=None):
    """Returns when the condition holds: it is looked at again after every step of the bus and every end of a task, and,
    for a test whose subject is the timer of a carried obligation, after every `every` seconds."""
    assert await wait_until(condition, _running.bus, _running.voices, seconds=seconds, every=every), what


@pytest.mark.asyncio
async def test_an_old_job_that_is_not_confirmed_stopped_cannot_put_its_bytes_into_the_new_result(dubs):
    from pathlib import Path
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    old_submit = dict(neighbours.submits[0])
    neighbours.cancel_done = False
    await reference_replaced(neighbours, probe)
    await statuses_until(sub, ["stale", "stale"])
    await settled(neighbours, 4)
    for fetch in neighbours.fetches[2:]:
        touch(fetch["path"], b"new-video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    journal = {(e["path"], e["kind"]): e for e in await voices.repo.cleanup_list()}
    assert journal[("job-1", "job")]["scope"] == old_submit["out_dir"], "the job is remembered with its folder"
    new_submit = neighbours.submits[1]
    assert old_submit["out_dir"] != new_submit["out_dir"] and old_submit["cache_dir"] != new_submit["cache_dir"]
    new_result = job_done(neighbours, "job-2")
    for result in new_result["results"]:
        Path(result["out_path"]).write_bytes(b"new-reference-audio")
    for result in job_result(old_submit, "job-1")["results"]:  # the old job is still alive and writes
        Path(result["out_path"]).write_bytes(b"old-reference-audio")
    probe.publish_event("conform.job.finished", new_result)
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    await quiet(voices)
    actual = await voices.repo.for_series(7)
    assert all(r["ref_uid"] == "u1-new" for r in actual)
    assert all(Path(r["output_path"]).read_bytes() == b"new-reference-audio" for r in actual)
    assert os.path.isdir(old_submit["out_dir"]), "the folder of a job not confirmed stopped goes with no episode"


@pytest.mark.asyncio
async def test_the_folder_of_a_job_goes_when_its_stop_is_confirmed_and_not_before(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    folder = neighbours.submits[0]["out_dir"]
    neighbours.cancel_done = False
    await reference_replaced(neighbours, probe)
    await statuses_until(sub, ["stale", "stale"])
    touch(os.path.join(folder, "written-by-the-old-job.flac"), b"late")
    await idle()
    assert os.path.isdir(folder), "while the job may still write, its folder stays"
    neighbours.cancel_done = True

    async def journal_empty():
        return await voices.repo.cleanup_list() == []

    await until_true(journal_empty, await voices.repo.cleanup_list(), every=RETRY_TICK_S)
    assert not os.path.exists(folder)


@pytest.mark.asyncio
async def test_a_submission_without_an_answer_whose_voices_are_reset_is_cancelled_by_its_key(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    neighbours.service_reachable = False
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    await quiet(voices)
    key = next(iter(neighbours.sent_keys))
    await reference_replaced(neighbours, probe)

    async def handed_over():
        return (key, "submission") in {(e["path"], e["kind"]) for e in await voices.repo.cleanup_list()}

    await until_true(handed_over, await voices.repo.cleanup_list())
    assert neighbours.conform_cancels == []
    neighbours.service_reachable = True

    async def cancelled_and_cleared():
        return set(neighbours.conform_cancels) == {"job-1"} and await voices.repo.cleanup_list() == []

    await until_true(cancelled_and_cleared, (neighbours.conform_cancels, await voices.repo.cleanup_list()), every=RETRY_TICK_S)


@pytest.mark.asyncio
async def test_an_episode_that_left_the_plan_stops_its_running_job(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    os.remove(ref)
    neighbours.references.clear()
    neighbours.plan[:] = []
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 0})

    async def stopped_and_gone():
        return set(neighbours.conform_cancels) == {"job-1"} and await rows(probe) == []

    await until_true(stopped_and_gone, (neighbours.conform_cancels, await rows(probe)))


@pytest.mark.asyncio
async def test_a_row_lets_go_of_its_files_in_the_same_step_that_the_journal_takes_them(dubs, monkeypatch):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    row = (await voices.repo.for_series(7))[0]
    track = row["output_path"]
    steps = []
    real = voices.repo._db.execute_many

    async def watching(statements):
        steps.append(len(statements))
        return await real(statements)

    monkeypatch.setattr(voices.repo._db, "execute_many", watching)
    await voices.repo.reset(row["id"], "test")
    assert steps and steps[-1] > 1, "the journal and the row change in one transaction"
    assert (await voices.repo.get(row["id"]))["output_path"] is None
    assert track in {entry["path"] for entry in await voices.repo.cleanup_list()}


@pytest.mark.asyncio
async def test_cleanup_does_not_forget_a_file_while_its_row_is_being_reset(dubs, monkeypatch):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    group = await voices.repo.for_series(7)
    old = group[0]["output_path"]
    real_unlink = voices._cleanup.unlink

    def refuse_old(path, kind):
        if path == old:
            raise PermissionError("the share refuses for a while")
        return real_unlink(path, kind)

    monkeypatch.setattr(voices._cleanup, "unlink", refuse_old)
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.005)
    real_reset = voices.repo.reset

    async def slow_reset(*args, **kwargs):
        await idle()  # the recovery gets its turns before the row lets go
        return await real_reset(*args, **kwargs)

    monkeypatch.setattr(voices.repo, "reset", slow_reset)
    await voices._follow(7, 1, 1, 0, group, ref, "new-reference")
    await idle()
    assert os.path.exists(old) and old in {e["path"] for e in await voices.repo.cleanup_list()}


@pytest.mark.asyncio
async def test_an_episode_that_left_the_plan_hands_its_tracks_to_the_journal_with_its_rows(dubs):
    from unittest.mock import patch

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    tracks = [row["output_path"] for row in await voices.repo.for_series(7)]
    os.remove(ref)
    neighbours.references.clear()
    neighbours.plan[:] = []
    with patch("modules.voices.module.os.remove", side_effect=PermissionError("read-only")):
        probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 0})
        await until_true(lambda: _empty(probe), "the rows of the episode are gone")
        await quiet(voices)  # every pass of the module ends while the removal is still refused
        assert {e["path"] for e in await voices.repo.cleanup_list()} >= set(tracks)
    assert all(os.path.exists(track) for track in tracks)
    await voices._cleanup.sweep()
    assert not any(os.path.exists(track) for track in tracks) and await voices.repo.cleanup_list() == []


async def _empty(probe):
    return await rows(probe) == []


@pytest.mark.asyncio
async def test_a_recovered_job_number_hands_its_job_to_the_duty_of_learning_its_end(dubs, db_path, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    neighbours.service_reachable = False
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    await quiet(voices)
    await voices.stop()
    neighbours.jobs["job-1"] = job_done(neighbours, "job-1")  # the job ended while the module was stopped
    neighbours.service_reachable = True
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        async def placed():
            return {r["status"] for r in await rows(probe)} == {"placed"}

        await until_true(placed, (await rows(probe), again._recovery.failures()))
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_failed_voice_over_that_leaves_the_plan_hands_its_file_to_the_journal_with_its_row(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    red = next(f for f in neighbours.fetches if "RedHeadSound" in f["url"])
    touch(red["path"], b"half a video")
    row = next(r for r in await voices.repo.for_series(7) if r["temp_path"] == red["path"])
    await voices.repo.mark_failed(row["id"], "download broke")
    neighbours.sources[:] = [s for s in neighbours.sources if s["studio"] != "RedHeadSound"]
    probe.send_command("voices.dispatch", {"series_id": 7})

    async def file_gone():
        return not os.path.exists(red["path"]) and await voices.repo.cleanup_list() == []

    await until_true(file_gone, (os.path.exists(red["path"]), await voices.repo.cleanup_list()))
    assert {r["voice_key"] for r in await voices.repo.for_series(7)} == {"BlackCat"}


@pytest.mark.asyncio
async def test_the_folder_above_an_unconfirmed_job_stays_while_the_job_may_write(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    folder = neighbours.submits[0]["out_dir"]
    series_folder = os.path.dirname(os.path.dirname(os.path.dirname(folder)))
    track = os.path.join(folder, "still-being-written.flac")
    touch(track, b"in progress")
    neighbours.cancel_done = False
    purged = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(purged.queue.get(), 5)
    await idle()
    entries = {e["path"]: e for e in await voices.repo.cleanup_list()}
    assert entries["job-1"]["scope"] == folder and series_folder in entries, "the folder above waits on the journal"
    assert os.path.exists(track) and os.path.isdir(series_folder)
    neighbours.cancel_done = True

    async def everything_gone():
        return await voices.repo.cleanup_list() == [] and not os.path.exists(series_folder)

    await until_true(everything_gone, (await voices.repo.cleanup_list(), os.path.exists(series_folder)), every=RETRY_TICK_S)


@pytest.mark.asyncio
async def test_an_entry_is_ended_only_in_the_version_that_was_read(dubs):
    repo = dubs[2].repo
    await repo.journal_cleanup("/some/name", "path", "first hand-over")
    read = (await repo.cleanup_list())[0]
    await repo.note_cleanup_error("/some/name", "a failed attempt")  # the same obligation: the version stays
    assert (await repo.cleanup_list())[0]["version"] == read["version"]
    await repo.journal_cleanup("/some/name", "path", "handed over again")
    assert await repo.cleanup_done("/some/name", read["version"]) is False
    assert [e["path"] for e in await repo.cleanup_list()] == ["/some/name"]
    assert await repo.cleanup_done("/some/name", (await repo.cleanup_list())[0]["version"]) is True


@pytest.mark.asyncio
async def test_a_pass_that_removed_the_old_file_does_not_end_the_entry_of_its_new_owner(dubs, tmp_path, monkeypatch):
    voices = dubs[2]
    path = str(tmp_path / "reused.flac")
    touch(path, b"old")
    await voices.repo.journal_cleanup(path, "path", "old owner")
    real = voices.repo.cleanup_done

    async def when_a_new_owner_arrives(name, version):
        await voices.repo.journal_cleanup(name, "path", "new owner")  # between the removal and the end of the pass
        return await real(name, version)

    monkeypatch.setattr(voices.repo, "cleanup_done", when_a_new_owner_arrives)
    assert await voices._cleanup.clear(path) is False
    assert [e["path"] for e in await voices.repo.cleanup_list()] == [path]


async def sending_held_back(voices, monkeypatch):
    """The request of a submission stays on its way until `release` is set."""
    entered, release = asyncio.Event(), asyncio.Event()
    request = voices.request

    async def delayed(topic, payload=None, **kwargs):
        if topic == "conform.submit":
            entered.set()
            await release.wait()
        return await request(topic, payload, **kwargs)

    monkeypatch.setattr(voices, "request", delayed)
    return entered, release


@pytest.mark.asyncio
async def test_a_submission_accepted_after_its_series_was_deleted_is_cancelled(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    entered, release = await sending_held_back(voices, monkeypatch)
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await asyncio.wait_for(entered.wait(), 3)
    key = next(r["submit_key"] for r in await voices.repo.for_series(7))
    try:
        await voices.on_series_deleted(Envelope("series.deleted", "event", {"series_id": 7}))
        await bus.settled()  # the send is held back on purpose, so the module is not quiet, only the bus
        assert (key, "submission") in {(e["path"], e["kind"]) for e in await voices.repo.cleanup_list()}, \
            "while the request is on its way no look at the service ends the entry"
    finally:
        release.set()

    async def cancelled_and_cleared():
        return set(neighbours.conform_cancels) == {"job-1"} and await voices.repo.cleanup_list() == []

    await until_true(cancelled_and_cleared, (neighbours.conform_cancels, await voices.repo.cleanup_list()),
                     every=RETRY_TICK_S)
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_job_accepted_for_voice_overs_that_are_gone_is_cancelled_at_once(dubs):
    voices, neighbours = dubs[2], dubs[1]
    await voices._attach("a-key-nobody-waits-for", "job-77", "/some/folder/of/the/job")
    assert neighbours.conform_cancels == ["job-77"] and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_submission_on_its_way_is_not_sent_again_when_the_service_comes_back(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    entered, release = await sending_held_back(voices, monkeypatch)
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await asyncio.wait_for(entered.wait(), 3)
    probe.publish_event("conform.daemon.up", {})
    await bus.settled()  # the send is held back on purpose, so the module is not quiet, only the bus
    release.set()
    await job_numbers_known(probe, {"job-1"})
    await quiet(voices)
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_name_is_owned_before_the_removal_looks_at_who_holds_it(dubs, tmp_path, monkeypatch):
    from core import storage
    voices = dubs[2]
    path = str(tmp_path / "track.flac")
    touch(path, b"old")
    await voices.repo.journal_cleanup(path, "path", "handed over")
    go, outcome = asyncio.Event(), []

    async def a_new_owner():
        await go.wait()
        try:
            await storage.call(lambda: None, owns=path)
            outcome.append("took the name")
        except storage.StorageBusy:
            outcome.append("had to wait")

    other = asyncio.create_task(a_new_owner())
    real = voices.repo.all_rows

    async def looking(*args):
        found = await real(*args)
        go.set()
        await idle()  # the pass has looked and not yet removed
        return found

    monkeypatch.setattr(voices.repo, "all_rows", looking)
    assert await voices._cleanup.clear(path) is True
    await other
    assert outcome == ["had to wait"] and not os.path.exists(path)


@pytest.mark.asyncio
async def test_a_job_found_by_its_key_that_nothing_owns_is_cancelled(dubs):
    voices, neighbours = dubs[2], dubs[1]
    neighbours.sent_keys["a-key-nobody-owns"] = "job-88"
    assert await voices._recover_one("a-key-nobody-owns") is True
    assert neighbours.conform_cancels == ["job-88"] and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_voice_over_in_alignment_tells_the_queue_its_job_and_the_file_it_was_sent_as(dubs):
    _, _, _, _, sub, _ = await two_voices_ready(dubs)
    named = {}
    end = asyncio.get_running_loop().time() + 5
    while len(named) < 2:
        payload = (await asyncio.wait_for(sub.queue.get(), end - asyncio.get_running_loop().time())).payload
        if payload["job_id"]:
            named[payload["voice_key"]] = (payload["job_id"], payload["file"])
    assert named == {"BlackCat": ("job-1", "7_s01e01_BlackCat.mp4"), "RedHeadSound": ("job-1", "7_s01e01_RedHeadSound.mp4")}


def test_the_file_a_voice_over_is_sent_as_is_named_in_one_place():
    assert os.path.basename(temp_path("/w", 7, 1, 1, 0, "BlackCat")) == dub_file(7, 1, 1, 0, "BlackCat") == "7_s01e01_BlackCat.mp4"
    assert dub_file(7, 1, 2, 0, "BlackCat") != dub_file(8, 1, 1, 0, "BlackCat") != dub_file(7, 1, 1, 0, "BlackCat")
    assert dub_file(7, 1, 1, 2, "BlackCat") == "7_s01e01p2_BlackCat.mp4" != dub_file(7, 1, 1, 1, "BlackCat")


async def settled_with_a_record(dubs, **per_dub):
    """One episode aligned and settled while the record of its job is still in the service's history."""
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    submit = dict(neighbours.submits[0])
    settled_sub = bus.subscribe("voices.episode.settled")
    result = job_done(neighbours, "job-1", **per_dub)
    neighbours.jobs["job-1"] = result
    probe.publish_event("conform.job.finished", result)
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await quiet(voices)
    return probe, neighbours, voices, submit


@pytest.mark.asyncio
async def test_the_folder_a_job_wrote_in_stays_while_its_record_is_in_the_history_and_goes_with_the_record(dubs):
    probe, neighbours, voices, submit = await settled_with_a_record(dubs, RedHeadSound={"ok": False})
    out_dir, episode = submit["out_dir"], os.path.dirname(os.path.dirname(submit["out_dir"]))
    assert sorted(os.listdir(out_dir)) == ["7_s01e01_RedHeadSound.flac"]  # the refused track stays, the placed one moved
    assert not os.path.exists(os.path.join(episode, "_ref")) and not os.path.exists(os.path.join(downloads(neighbours), "7", "s01e01"))
    assert neighbours.conform_cancels == [] and await voices.repo.cleanup_list() == []
    del neighbours.jobs["job-1"]  # the owner deletes the record
    probe.publish_event("conform.job.removed", {"job_id": "job-1"})

    async def folder_gone():
        return not os.path.exists(episode)

    await until_true(folder_gone, os.listdir(episode) if os.path.isdir(episode) else None)
    assert not os.path.exists(os.path.join(conform_dir(neighbours), "7"))  # the empty folders around it went too


@pytest.mark.asyncio
async def test_a_record_removed_while_the_module_was_away_is_found_when_the_service_is_back(dubs):
    probe, neighbours, voices, submit = await settled_with_a_record(dubs)
    out_dir = submit["out_dir"]
    assert os.path.isdir(out_dir)
    del neighbours.jobs["job-1"]  # removed with nobody listening
    probe.publish_event("conform.daemon.up", {})

    async def folder_gone():
        return not os.path.exists(out_dir)

    await until_true(folder_gone, os.listdir(out_dir))


@pytest.mark.asyncio
async def test_the_folder_of_a_job_whose_service_cannot_say_if_the_record_is_gone_stays(dubs):
    probe, neighbours, voices, submit = await settled_with_a_record(dubs)
    del neighbours.jobs["job-1"]
    neighbours.job_get_failures = 1  # the service does not answer the question
    probe.publish_event("conform.daemon.up", {})

    async def asked():
        return neighbours.job_get_failures == 0

    await until_true(asked, "the service was never asked")
    await quiet(voices)
    assert os.path.isdir(submit["out_dir"])


@pytest.mark.asyncio
async def test_a_job_leaves_the_history_with_its_folder_when_the_owner_asked_for_a_clear_queue(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    submit = dict(neighbours.submits[0])
    neighbours.clear_finished = True
    settled_sub = bus.subscribe("voices.episode.settled")
    result = job_done(neighbours, "job-1")
    neighbours.jobs["job-1"] = result
    probe.publish_event("conform.job.finished", result)
    await asyncio.wait_for(settled_sub.queue.get(), 5)

    async def cleared():
        return not os.path.exists(os.path.dirname(os.path.dirname(submit["out_dir"]))) and await voices.repo.cleanup_list() == []

    await until_true(cleared, "the job and its folder did not go")
    assert neighbours.conform_cancels == ["job-1"]


@pytest.mark.asyncio
async def test_a_replaced_reference_takes_the_finished_job_out_of_the_history_with_its_folder(dubs):
    probe, neighbours, voices, submit = await settled_with_a_record(dubs)
    out_dir = submit["out_dir"]
    await reference_replaced(neighbours, probe)

    async def job_dropped():
        return "job-1" in neighbours.conform_cancels and not os.path.exists(out_dir)

    await until_true(job_dropped, (neighbours.conform_cancels, os.path.isdir(out_dir)))


# --- the plan follows the layout and the references; only a sync of the series downloads --------------------


def layout_changed(probe, **fields):
    probe.publish_event("composition.changed", {"series_id": 7, "rev": 2, "episodes": [], **fields})


def synced(probe):
    """The user ran a sync after a change: the disk and the service follow the plan only now (И-59)."""
    probe.send_command("voices.settle", {"series_id": 7})


def reference_changed(probe, season=1, episode=1, mark="by_level"):
    probe.publish_event("scan.reference.changed", {"series_id": 7, "season": season, "episode": episode, "mark": mark})


async def pass_over(voices, neighbours, asks):
    """The planning pass that asked the layout for the `asks`-th time has ended."""
    async def asked():
        return neighbours.layout_asks >= asks

    await until_true(asked, ("the layout was not asked", neighbours.layout_asks))
    await quiet(voices)


@pytest.mark.asyncio
async def test_a_change_of_the_layout_plans_the_voice_overs_but_downloads_nothing_until_a_sync(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    layout_changed(probe)
    await statuses_until(sub, ["pending"] * 3)
    await pass_over(voices, neighbours, 1)
    assert neighbours.fetches == []
    assert {(r["episode"], r["status"], r["state"]) for r in await rows(probe)} == {
        (1, "pending", "planned"), (2, "pending", "planned"), (3, "pending", "planned")}
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    assert {r["status"] for r in await rows(probe)} == {"downloading"}


@pytest.mark.asyncio
async def test_a_voice_over_that_left_the_layout_before_any_work_leaves_the_plan_with_an_event(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    synced(probe)
    await statuses_until(sub, ["pending"] * 3)
    del neighbours.sources[1:]  # only the voice of the reference is left
    synced(probe)
    await statuses_until(sub, ["removed"] * 3)
    assert await rows(probe) == [] and neighbours.fetches == []


@pytest.mark.asyncio
async def test_an_episode_whose_reference_is_none_or_unfit_gets_no_voice_over(dubs):
    probe, neighbours, voices, bus = dubs
    neighbours.marks[(1, 2)] = ReferenceMark.NONE
    neighbours.marks[(1, 3)] = ReferenceMark.UNFIT
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    assert [r["episode"] for r in await rows(probe)] == [1] and len(neighbours.fetches) == 1


@pytest.mark.asyncio
async def test_a_reference_that_turns_none_takes_the_planned_voice_overs_of_its_episode_with_it(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    synced(probe)
    await statuses_until(sub, ["pending"] * 3)
    neighbours.marks[(1, 3)] = ReferenceMark.NONE
    synced(probe)
    await statuses_until(sub, ["removed"])
    assert [r["episode"] for r in await rows(probe)] == [1, 2]


@pytest.mark.asyncio
async def test_a_reference_that_turns_none_hands_the_downloaded_files_of_its_episode_to_the_journal(dubs):
    probe, neighbours, voices, bus, sub, _ = await two_voices_ready(dubs, reference=False)
    await statuses_until(sub, ["ready", "ready"])
    paths = [row["temp_path"] for row in await rows(probe)]
    assert len(paths) == 2 and all(os.path.isfile(path) for path in paths)
    neighbours.marks[(1, 1)] = ReferenceMark.NONE
    synced(probe)
    await statuses_until(sub, ["removed", "removed"])
    assert await rows(probe) == [] and not any(os.path.exists(path) for path in paths)
    assert await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_reference_that_turns_none_leaves_the_tracks_of_a_video_that_is_still_there(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    neighbours.marks[(1, 1)] = ReferenceMark.NONE
    reference_changed(probe, 1, 1, "none")
    await pass_over(voices, neighbours, 2)
    assert len(await rows(probe)) == 2 and len(library_files(neighbours)) == 3 and sub.queue.empty()


@pytest.mark.asyncio
async def test_an_episode_whose_reference_row_the_layout_lost_keeps_its_rows_until_the_reference_is_decided(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    synced(probe)
    await statuses_until(sub, ["pending"] * 3)
    reference = neighbours.sources[0]
    reference["episodes"].remove(2)
    reference["links"].pop("2")  # the reference still points at the row of episode 2, the layout no longer has it
    synced(probe)
    await pass_over(voices, neighbours, 2)
    assert [r["episode"] for r in await rows(probe)] == [1, 2, 3] and sub.queue.empty()
    neighbours.marks[(1, 2)] = ReferenceMark.NONE  # the reference is decided: there is none
    synced(probe)
    await statuses_until(sub, ["removed"])
    assert [r["episode"] for r in await rows(probe)] == [1, 3]


@pytest.mark.asyncio
async def test_an_event_that_says_only_the_summaries_changed_is_not_acted_on(dubs, monkeypatch):
    probe, _, voices, _ = dubs
    asked = []
    monkeypatch.setattr(voices, "_want_plan", asked.append)
    layout_changed(probe, summary_only=True)
    layout_changed(probe)
    reference_changed(probe)

    async def both():
        return len(asked) >= 2

    await until_true(both, asked)
    assert asked == [7, 7]


@pytest.mark.asyncio
async def test_a_failed_download_is_offered_again_by_a_sync_and_not_by_a_change_of_the_layout(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    victim = next(f for f in neighbours.fetches if f["url"].endswith("kodik-BlackCat-3"))
    finish(probe, victim["key"], ok=False, error="gone")
    await change(sub, "failed")
    layout_changed(probe)
    await pass_over(voices, neighbours, 2)
    assert len(neighbours.fetches) == 3 and next(r for r in await rows(probe) if r["episode"] == 3)["status"] == "failed"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    assert neighbours.fetches[3]["key"] == victim["key"]


@pytest.mark.asyncio
async def test_a_voice_over_that_failed_for_want_of_a_source_is_planned_again_when_the_layout_has_one(dubs):
    probe, neighbours, voices, bus = dubs
    kodik = neighbours.sources[1]
    kodik["episodes"].remove(3)
    link = kodik["links"].pop("3")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    await quiet(voices)
    assert next(r for r in await rows(probe) if r["episode"] == 3)["status"] == "failed"
    kodik["episodes"].append(3)
    kodik["links"]["3"] = link
    layout_changed(probe)
    await settled(neighbours, 3)
    assert neighbours.fetches[2]["url"] == link


@pytest.mark.asyncio
async def test_a_restart_asks_no_neighbour_for_the_plan_and_the_sync_of_a_series_is_not_forgotten(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    asks = (neighbours.layout_asks, neighbours.output_asks)
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        await settled(neighbours, 6)
        assert (neighbours.layout_asks, neighbours.output_asks) == asks
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_series_that_was_never_synced_stays_planned_after_a_restart(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    layout_changed(probe)
    await statuses_until(sub, ["pending"] * 3)
    await pass_over(voices, neighbours, 1)
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        await idle()
        assert neighbours.fetches == [] and {r["status"] for r in await rows(probe)} == {"pending"}
        probe.send_command("voices.dispatch", {"series_id": 7})
        await settled(neighbours, 3)
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_neighbour_that_does_not_answer_the_plan_is_asked_again_by_itself(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.02)
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    neighbours.layout_failures = 2
    layout_changed(probe)
    await statuses_until(sub, ["pending"] * 3)
    assert neighbours.layout_asks == 3

    async def carried():
        return not voices._recovery.failures()

    await until_true(carried, voices._recovery.failures(), every=RETRY_TICK_S)


@pytest.mark.asyncio
async def test_a_series_deleted_while_its_plan_is_being_written_leaves_no_row_behind(dubs):
    probe, neighbours, voices, bus = dubs
    neighbours.layout_gate = asyncio.Event()
    purged = bus.subscribe("series.purged")
    layout_changed(probe)

    async def asked():
        return neighbours.layout_asks >= 1

    await until_true(asked, "the layout was not asked")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(purged.queue.get(), 3)
    neighbours.layout_gate.set()
    await quiet(voices)
    assert await rows(probe) == [] and neighbours.fetches == []


@pytest.mark.asyncio
async def test_the_levers_choose_which_voice_overs_an_episode_gets_and_where_from(dubs):
    probe, neighbours, voices, bus = dubs
    neighbours.levers[(1, 1)] = [EffectiveLever(lever="voice.take", target=group_target("BlackCat"), value=False,
                                                level=Level.EPISODE)]
    neighbours.levers[(1, 2)] = [EffectiveLever(lever="voice.player", target=group_target("BlackCat"),
                                                value={"source_id": 1, "player": "kodik"}, level=Level.SEASON)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    await quiet(voices)
    assert [r["episode"] for r in await rows(probe)] == [2, 3]
    assert {f["url"] for f in neighbours.fetches} == {
        "https://site/title.html#kodik-BlackCat-2", "https://site/title.html#kodik-BlackCat-3"}


@pytest.mark.asyncio
async def test_a_reference_that_turns_none_while_a_voice_over_downloads_takes_its_episode_and_withdraws_the_download(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    neighbours.marks[(1, 3)] = ReferenceMark.NONE
    synced(probe)
    await pass_over(voices, neighbours, 2)
    victim = next(f for f in neighbours.fetches if f["url"].endswith("kodik-BlackCat-3"))
    assert [r["episode"] for r in await rows(probe)] == [1, 2] and victim["key"] in neighbours.cancelled_keys



# --- a track is valid while it is aligned to a video of the same identity (stage 11, point 5) ---------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("video", ["same_video_other_file", "other_video"])
async def test_a_track_follows_a_better_version_of_its_video_and_goes_stale_for_another_video(dubs, video):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    before = {row["voice_key"]: row for row in await voices.repo.for_series(7) if row["episode"] == 1}
    assert all(row["aligned_to"] for row in before.values())  # the identity of the video is written down with the job
    neighbours.uids[1] = "u1-better"  # the row of a better resolution of the slot, or of another video
    if video == "other_video":
        neighbours.item_keys[1] = "elsewhere-1"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await quiet(voices)
    after = {row["voice_key"]: row for row in await voices.repo.for_series(7) if row["episode"] == 1}
    if video == "same_video_other_file":
        assert {key: (row["status"], row["ref_uid"]) for key, row in after.items()} == {
            key: ("placed", "u1-better") for key in before}
        assert {key: row["id"] for key, row in after.items()} == {key: row["id"] for key, row in before.items()}
    else:
        assert all(row["status"] != "placed" for row in after.values())  # aligned to another video: aligned anew


@pytest.mark.asyncio
async def test_a_track_packed_without_its_identity_takes_it_from_the_container_and_then_follows_its_video(dubs):
    """A track aligned before the identity was recorded is told it by the packing (the container holds the truth);
    then a better resolution of the same video keeps it instead of aligning it anew."""
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    before = {row["id"]: row for row in await voices.repo.for_series(7) if row["episode"] == 1}
    identity = next(iter(before.values()))["aligned_to"]
    await voices.repo._db.execute(f"UPDATE voice_dubs SET aligned_to='' WHERE id IN ({','.join('?' * len(before))})",
                                  tuple(before))
    await probe.request("voices.tracks.packed", {"series_id": 7, "container": "/media/e01.mkv", "aligned_to": identity,
                                                 "dub_ids": list(before)}, timeout=5)
    packed = {row["id"]: row for row in await voices.repo.for_series(7) if row["episode"] == 1}
    assert {(row["aligned_to"], row["output_path"], row["ref_path"]) for row in packed.values()} == {
        (identity, None, "/media/e01.mkv")}
    assert {item["hint"] for item in await rows(probe) if item["episode"] == 1} == {"packed"}
    neighbours.uids[1] = "u1-better"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await quiet(voices)
    after = {row["id"]: row for row in await voices.repo.for_series(7) if row["episode"] == 1}
    assert {key: (row["status"], row["ref_uid"]) for key, row in after.items()} == {
        key: ("placed", "u1-better") for key in before}
