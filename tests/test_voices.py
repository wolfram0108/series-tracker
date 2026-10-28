"""Tests of the voices module: voice identity, reference choice, layout of a series."""
import asyncio
import os
import sqlite3
import subprocess
import sys

import pytest

from core import BaseModule, Bus, Runner
from core.db import Database
from modules.settings import SettingsModule
from modules.voices import VoicesModule
from modules.voices.identity import same_voice, tokens, voice_groups
from modules.voices.layout import (DEFAULT_PRIORITY, choose_reference, download_chain,
                                   plan_layout, rank)
from modules.voices.states import public_state


def src(player, studio, episodes, kind="", url=None):
    return {"url": url or f"https://site/?player={player}&voice={studio}", "player": player,
            "studio": studio, "kind": kind, "episodes": list(episodes)}


# The real "Стать победителем" catalog: three voices in CVH, the same three in Kodik.
WINNER = [
    src("cvh", "BlackCat", [*range(1, 11), 13], "Дубляж"),
    src("cvh", "RedHeadSound", range(1, 5), "Многоголосый"),
    src("cvh", "FumoDub", range(1, 16), "Одноголосый"),
    src("kodik", "FumoDub", range(1, 16), "voice"),
    src("kodik", "Red Head Sound", range(1, 16), "voice"),
    src("kodik", "BlackCat", range(1, 16), "voice"),
]
FUMODUB_CVH = WINNER[2]["url"]


# --- identity -------------------------------------------------------------------------


def test_names_differing_in_case_and_spaces_are_one_voice():
    assert same_voice("RedHeadSound", "Red Head Sound")
    assert same_voice("BLACKCAT", "blackcat")
    assert not same_voice("BlackCat", "FumoDub")


def test_a_combined_credit_names_every_studio_in_it():
    assert tokens("AniMy / RuChiMe") == {"animy", "ruchime"}
    assert same_voice("AniMy / RuChiMe", "Animy")
    assert same_voice("AniMy / RuChiMe", "RuChiMe")


def test_groups_are_transitive_and_keep_listing_order():
    names = ["AniMy / RuChiMe", "AniStar", "Animy", "RuChiMe", "AniStar & Other"]
    assert voice_groups(names) == [[0, 2, 3], [1, 4]]


def test_an_alias_joins_two_spellings(monkeypatch):
    monkeypatch.setattr("modules.voices.identity.ALIASES", {"aniliberty": "anilibria"})
    assert same_voice("AniLiberty", "AniLibria")


# --- reference choice -----------------------------------------------------------------


def test_reference_is_the_biggest_resolution():
    chosen = choose_reference([
        {"player": "alloha", "resolution": 720}, {"player": "kodik", "resolution": 1080}])
    assert chosen["player"] == "kodik"


def test_equal_resolution_goes_to_the_higher_priority_source():
    candidates = [{"player": "kodik", "resolution": 720}, {"player": "cvh", "resolution": 720},
                  {"player": "allvideo", "resolution": 720}]
    assert choose_reference(candidates)["player"] == "cvh"
    assert [rank(p) for p in ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")] == list(range(6))
    assert rank("unknown") == len(DEFAULT_PRIORITY)


def test_full_tie_keeps_the_first_listed_and_a_custom_order_wins():
    first, second = {"player": "cvh", "resolution": 720, "id": 1}, {"player": "cvh", "resolution": 720, "id": 2}
    assert choose_reference([first, second])["id"] == 1
    candidates = [{"player": "cvh", "resolution": 720}, {"player": "kodik", "resolution": 720}]
    assert choose_reference(candidates, ["kodik", "cvh"])["player"] == "kodik"
    with pytest.raises(ValueError):
        choose_reference([])


# --- layout of a series ---------------------------------------------------------------


def test_winner_layout_matches_the_agreed_example():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    rows = {row["label"]: row for row in layout["voices"]}
    assert rows["FumoDub"]["role"] == "reference"
    assert rows["FumoDub"]["source"]["player"] == "cvh"
    # The other two come from Kodik, which has every episode; CVH is their fallback.
    for label in ("RedHeadSound", "BlackCat"):
        assert rows[label]["role"] == "dub"
        assert rows[label]["source"]["player"] == "kodik"
        assert [f["player"] for f in rows[label]["fallbacks"]] == ["cvh"]
    # The reference comes first, the other voices keep the order of the site.
    assert [row["label"] for row in layout["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]
    assert layout["reference"]["url"] == FUMODUB_CVH
    # The reference voice is already in the video: its Kodik copy is not downloaded.
    assert [(s["player"], s["studio"]) for s in layout["skipped"]] == [("kodik", "FumoDub")]
    assert layout["skipped"][0]["reason"] == "озвучка уже в референсе"


def test_priority_breaks_a_tie_between_equally_full_sources():
    layout = plan_layout([src("kodik", "A", [1, 2]), src("cvh", "A", [1, 2]),
                          src("cvh", "Ref", [1, 2])], "manual",
                         "https://site/?player=cvh&voice=Ref")
    row = next(r for r in layout["voices"] if r["label"] == "A")
    assert row["source"]["player"] == "cvh"
    assert [f["player"] for f in row["fallbacks"]] == ["kodik"]


def test_fallbacks_follow_priority_not_coverage():
    sources = [src("kodik", "A", [1, 2, 3]), src("vibix", "A", [1]), src("cvh", "A", [1, 2]),
               src("cvh", "Ref", [1])]
    layout = plan_layout(sources, "manual", "https://site/?player=cvh&voice=Ref")
    row = next(r for r in layout["voices"] if r["label"] == "A")
    assert row["source"]["player"] == "kodik"
    assert [f["player"] for f in row["fallbacks"]] == ["cvh", "vibix"]


def test_download_chain_starts_with_the_main_source_and_skips_holes():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    red_head = next(r for r in layout["voices"] if r["label"] == "RedHeadSound")
    assert [s["player"] for s in download_chain(red_head, 3)] == ["kodik", "cvh"]
    assert [s["player"] for s in download_chain(red_head, 9)] == ["kodik"]
    black_cat = next(r for r in layout["voices"] if r["label"] == "BlackCat")
    assert download_chain(black_cat, 12) == [black_cat["source"]]
    assert download_chain(black_cat, 99) == []


def test_a_combined_credit_is_one_voice_with_the_reference_of_its_studio():
    sources = [src("cvh", "AniMy / RuChiMe", [1, 2, 3]), src("kodik", "Animy", [1, 2, 3, 4]),
               src("kodik", "Other", [1, 2])]
    layout = plan_layout(sources, "manual", sources[0]["url"])
    assert [(r["label"], r["role"]) for r in layout["voices"]] == [
        ("AniMy / RuChiMe", "reference"), ("Other", "dub")]
    assert [s["studio"] for s in layout["skipped"]] == ["Animy"]


def test_auto_mode_lists_every_voice_and_excludes_nothing():
    layout = plan_layout(WINNER, "auto")
    assert layout["reference"] is None and layout["skipped"] == []
    assert {row["role"] for row in layout["voices"]} == {"dub"}
    assert len(layout["voices"]) == 3
    # Voices are ordered as they would win a resolution tie: FumoDub has a CVH source, the rest only Kodik.
    assert [row["label"] for row in layout["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]


def test_manual_mode_needs_a_known_reference():
    with pytest.raises(ValueError):
        plan_layout(WINNER, "manual", "https://nowhere")
    with pytest.raises(ValueError):
        plan_layout(WINNER, "manual", None)


def test_ranges_show_holes_in_each_source():
    layout = plan_layout(WINNER, "manual", FUMODUB_CVH)
    black_cat_cvh = next(r for r in layout["voices"] if r["label"] == "BlackCat")["fallbacks"][0]
    assert (black_cat_cvh["ranges"], black_cat_cvh["count"]) == ("1-10, 13", 11)


# --- the module on the bus ------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "test.db"
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"],
                   env={"ST_DB_URL": f"sqlite:///{path}", "PATH": "/usr/bin:/bin"},
                   cwd=".", check=True, capture_output=True)
    return str(path)


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    probe = Probe(bus)
    runner = Runner(bus, [SettingsModule(bus, db), VoicesModule(bus, db), probe])
    await runner.start()
    yield probe
    await runner.stop()


@pytest.mark.asyncio
async def test_layout_query_uses_the_default_priority(system):
    reply = await system.request("voices.layout", {"sources": WINNER, "reference": FUMODUB_CVH},
                                 timeout=5)
    assert reply["priority"] == list(DEFAULT_PRIORITY)
    assert [r["label"] for r in reply["voices"]] == ["FumoDub", "BlackCat", "RedHeadSound"]


@pytest.mark.asyncio
async def test_layout_query_takes_the_priority_from_settings(system):
    await system.request("settings.value.set",
                         {"key": "voices.source_priority", "value": '["kodik", "cvh"]'}, timeout=5)
    sources = [src("cvh", "A", [1, 2]), src("kodik", "A", [1, 2]), src("cvh", "Ref", [1])]
    reply = await system.request("voices.layout", {
        "sources": sources, "reference": "https://site/?player=cvh&voice=Ref"}, timeout=5)
    assert reply["priority"] == ["kodik", "cvh"]
    assert next(r for r in reply["voices"] if r["label"] == "A")["source"]["player"] == "kodik"


@pytest.mark.asyncio
async def test_a_broken_priority_setting_falls_back_to_the_default(system):
    await system.request("settings.value.set",
                         {"key": "voices.source_priority", "value": "not json"}, timeout=5)
    reply = await system.request("voices.layout", {"sources": WINNER, "reference": FUMODUB_CVH},
                                 timeout=5)
    assert reply["priority"] == list(DEFAULT_PRIORITY)


# --- download of the additional voice-overs -------------------------------------------


def linked(player, studio, episodes, kind="", url=None):
    """A source whose episode links are what the site catalog would hand out."""
    row = src(player, studio, episodes, kind, url)
    row["links"] = {str(n): f"https://site/title.html#{player}-{studio}-{n}" for n in episodes}
    return row


class Neighbours(BaseModule):
    """catalog + sources + downloads + settings in one fake; fetch commands are captured."""

    name = "neighbours"

    def __init__(self, bus, sources, reference_url, plan, library="/nonexistent"):
        self.sources = sources
        self.reference_url = reference_url
        self.plan = plan  # [(season, episode, site episode)]
        self.library = library
        self.references = {}  # episode -> file name of the reference video on disk
        self.uids = {}  # episode -> media item of its reference (default: u<episode>)
        self.extra_items = []  # more media items of the plan, as downloads.episodes.get shows them
        self.settings = {}
        self.fetches = []
        self.cancels = []
        self.submits = []
        self.submit_reply = None
        self.jobs = {}  # job id -> reply of conform.job.get
        self.source_type = "web_video"
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self.on_series)
        self.handle("sources.parse", self.on_parse)
        self.handle("downloads.episodes.get", self.on_episodes)
        self.handle("settings.value.get", self.on_setting)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle("downloads.fetch.cancel", self.on_cancel)
        self.handle("conform.submit", self.on_submit)
        self.handle("conform.job.get", self.on_job_get)

    async def on_series(self, env):
        return {"id": env.payload["series_id"], "url": "https://site/title.html", "name": "Series",
                "save_path": self.library, "source_type": self.source_type}

    async def on_parse(self, env):
        assert env.payload.get("with_links") is True
        return {"sources": self.sources, "selected_source": self.reference_url}

    async def on_episodes(self, env):
        reference = next(s for s in self.sources if s["url"] == self.reference_url)
        items = [{"unique_id": self.uids.get(episode, f"u{episode}"), "season": season,
                  "episode_start": episode, "source_url": reference["links"][str(site)],
                  "status": "pending", "final_filename": self.references.get(episode),
                  "superseded_by": None} for season, episode, site in self.plan]
        return {"items": items + self.extra_items}

    async def on_setting(self, env):
        return {"key": env.payload["key"], "value": self.settings.get(env.payload["key"])}

    async def on_fetch(self, env):
        self.fetches.append(dict(env.payload))

    async def on_cancel(self, env):
        self.cancels.append(env.payload["series_id"])

    async def on_submit(self, env):
        self.submits.append(dict(env.payload))
        return self.submit_reply or {"job_id": f"job-{len(self.submits)}"}

    async def on_job_get(self, env):
        return self.jobs.get(env.payload["job_id"], {"job_id": env.payload["job_id"], "status": "gone",
                                                     "error": None, "results": []})


@pytest.fixture
async def dubs(db_path, tmp_path):
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO parser_profiles (id, name) VALUES (1, 'p')")
        conn.execute("INSERT INTO series (id, url, name, name_en, site, save_path, source_type, "
                     "parser_profile_id, auto_scan_enabled, vk_search_mode) VALUES (7, "
                     "'https://site/title.html', 's', 's', 'dunhuatv', '/media/s', 'web_video', 1, 0, 'search')")
        conn.commit()
    bus = Bus()
    db = Database(db_path)
    sources = [linked("cvh", "FumoDub", range(1, 4), url="https://site/?player=cvh&voice=FumoDub"),
               linked("kodik", "BlackCat", range(1, 4)),
               linked("cvh", "BlackCat", [1, 2])]
    neighbours = Neighbours(bus, sources, sources[0]["url"], [(1, 1, 1), (1, 2, 2), (1, 3, 3)],
                            library=str(tmp_path / "library"))
    neighbours.settings["voices.temp_root"] = str(tmp_path / "temp")
    probe = Probe(bus)
    voices = VoicesModule(bus, db)
    runner = Runner(bus, [neighbours, SettingsModule(bus, db), voices, probe])
    await runner.start()
    yield probe, neighbours, voices, bus
    await runner.stop()


async def settled(neighbours, count, seconds=3.0):
    end = asyncio.get_running_loop().time() + seconds
    while len(neighbours.fetches) < count:
        assert asyncio.get_running_loop().time() < end, neighbours.fetches
        await asyncio.sleep(0.01)


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
    assert {f["url"] for f in neighbours.fetches} == {
        f"https://site/title.html#kodik-BlackCat-{n}" for n in (1, 2, 3)}
    first = neighbours.fetches[0]
    assert first["max_height"] == 720 and first["series_id"] == 7 and first["key"].startswith("dub:")
    assert first["path"].startswith(neighbours.settings["voices.temp_root"] + "/7/s01e01/")
    assert {r["status"] for r in await rows(probe)} == {"downloading"}
    assert {r["voice_key"] for r in await rows(probe)} == {"blackcat"}


@pytest.mark.asyncio
async def test_a_finished_download_makes_the_dub_ready_and_is_announced(dubs):
    probe, neighbours, _, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    finish(probe, neighbours.fetches[0]["key"])
    event = (await asyncio.wait_for(sub.queue.get(), 3)).payload
    assert event["status"] == "ready" and event["voice_key"] == "blackcat" and event["series_id"] == 7
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
    assert retry["key"] == victim["key"] and retry["url"] == "https://site/title.html#cvh-BlackCat-1"
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
    event = (await asyncio.wait_for(sub.queue.get(), 3)).payload
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
    await asyncio.wait_for(sub.queue.get(), 3)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    assert neighbours.fetches[3]["key"] == victim["key"] and neighbours.fetches[3]["url"] == victim["url"]


@pytest.mark.asyncio
async def test_an_episode_nobody_offers_is_a_failure_not_silence(dubs):
    probe, neighbours, _, _ = dubs
    neighbours.plan.append((1, 4, 3))
    neighbours.sources[1]["links"].pop("3")
    neighbours.sources[1]["episodes"].remove(3)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    missing = [r for r in await rows(probe) if r["status"] == "failed"]
    assert [r["episode"] for r in missing] == [3, 4]
    assert {r["error"] for r in missing} == {"серии нет ни в одном источнике"}
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
    await asyncio.wait_for(sub.queue.get(), 3)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.2)
    assert len(neighbours.fetches) == 3
    os.remove(done["path"])
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    assert neighbours.fetches[3]["key"] == done["key"]


@pytest.mark.asyncio
async def test_episodes_that_left_the_plan_lose_their_pending_rows(dubs):
    probe, neighbours, _, _ = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    finish(probe, neighbours.fetches[2]["key"], ok=False, error="x")
    await asyncio.sleep(0.1)
    neighbours.plan[:] = [(1, 1, 1)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.2)
    assert [r["episode"] for r in await rows(probe)] == [1, 2]


@pytest.mark.asyncio
async def test_other_source_types_and_single_voice_series_are_left_alone(dubs):
    probe, neighbours, _, _ = dubs
    neighbours.source_type = "vk_video"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.2)
    assert neighbours.fetches == [] and await rows(probe) == []


@pytest.mark.asyncio
async def test_deleting_the_series_cancels_downloads_and_removes_its_files(dubs):
    probe, neighbours, _, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    folder = os.path.join(neighbours.settings["voices.temp_root"], "7")
    os.makedirs(folder)
    open(os.path.join(folder, "x.mp4"), "wb").close()
    sub = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    report = (await asyncio.wait_for(sub.queue.get(), 3)).payload
    assert report == {"series_id": 7, "owner": "voices"}
    assert neighbours.cancels == [7] and await rows(probe) == [] and not os.path.exists(folder)


@pytest.mark.asyncio
async def test_after_a_restart_interrupted_downloads_are_sent_again(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    again = VoicesModule(bus, Database(db_path))
    await again.start()
    try:
        await settled(neighbours, 6)
        assert sorted(f["key"] for f in neighbours.fetches[3:]) == sorted(f["key"] for f in neighbours.fetches[:3])
    finally:
        await again.stop()


# --- alignment: from downloaded voice-overs to tracks next to the video -------------------


def touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


async def statuses_until(sub, wanted, seconds=5.0):
    """Wait on the pushed events until `wanted` (a sorted list of statuses seen) is reached."""
    seen = []
    end = asyncio.get_running_loop().time() + seconds
    while sorted(seen) != sorted(wanted):
        left = end - asyncio.get_running_loop().time()
        assert left > 0, (seen, wanted)
        seen.append((await asyncio.wait_for(sub.queue.get(), left)).payload["status"])
    return seen


def reference_on_disk(neighbours, episode=1):
    name = f"Series s01e{episode:02d}.mp4"
    neighbours.references[episode] = name
    touch(os.path.join(neighbours.library, name))
    return os.path.join(neighbours.library, name)


async def two_voices_ready(dubs, plan=((1, 1, 1),), reference=True, finished=True):
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
    submit = neighbours.submits[-1]
    results = []
    for path in submit["dubs"]:
        name = os.path.basename(path)
        out = os.path.join(submit["out_dir"], os.path.splitext(name)[0] + ".flac")
        touch(out, b"flac")
        result = {"dub": name, "ok": True, "skipped": False, "out_path": out, "suspect": False,
                  "critical": [], "warnings": [], "resid_ms": 12.0, "error": None}
        result.update(per_dub.get(os.path.splitext(name)[0], {}))
        results.append(result)
    return {"job_id": job_id, "status": "done", "error": None, "results": results}


@pytest.mark.asyncio
async def test_an_episode_goes_to_alignment_and_its_tracks_land_next_to_the_video(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    submit = neighbours.submits[0]
    assert submit["ref"] == ref and submit["label"] == "Series s01e01"
    assert sorted(os.path.basename(p) for p in submit["dubs"]) == ["blackcat.mp4", "redheadsound.mp4"]
    assert submit["out_dir"].endswith("/7/s01e01/_aligned") and submit["cache_dir"].endswith("/7/s01e01/_cache")
    assert {r["status"] for r in await rows(probe)} == {"aligning"}
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    assert (await asyncio.wait_for(settled_sub.queue.get(), 5)).payload == {
        "series_id": 7, "season": 1, "episode": 1}
    stem = os.path.splitext(ref)[0]
    assert os.path.isfile(f"{stem}.BlackCat.ru.flac") and os.path.isfile(f"{stem}.RedHeadSound.ru.flac")
    done = await rows(probe)
    assert {r["status"] for r in done} == {"placed"}
    assert {r["sidecar_path"] for r in done} == {f"{stem}.BlackCat.ru.flac", f"{stem}.RedHeadSound.ru.flac"}
    assert not os.path.exists(os.path.join(neighbours.settings["voices.temp_root"], "7", "s01e01"))
    assert os.path.isfile(ref)


@pytest.mark.asyncio
async def test_the_episode_waits_for_its_reference_video(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, reference=False)
    await statuses_until(sub, ["ready", "ready"])
    await asyncio.sleep(0.2)
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
    await asyncio.sleep(0.2)
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
    assert [os.path.basename(p) for p in neighbours.submits[0]["dubs"]] == ["blackcat.mp4"]


@pytest.mark.asyncio
async def test_a_result_that_is_not_clean_is_rejected_and_never_placed(dubs, caplog):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    settled_sub = bus.subscribe("voices.episode.settled")
    caplog.set_level("WARNING")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", blackcat={
        "suspect": True, "warnings": ["остаток вне допуска"]}, redheadsound={"critical": ["нет общего звука"]}))
    await statuses_until(sub, ["rejected", "rejected"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["blackcat"]["status"] == "rejected" and "подозрительно" in by_voice["blackcat"]["outcome"]
    assert "критично" in by_voice["redheadsound"]["outcome"]
    stem = os.path.splitext(ref)[0]
    assert not any(name.startswith(os.path.basename(stem) + ".") and name.endswith(".flac")
                   for name in os.listdir(neighbours.library))
    assert any("отклонена" in record.getMessage() for record in caplog.records)
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    assert not os.path.exists(os.path.join(neighbours.settings["voices.temp_root"], "7", "s01e01"))


@pytest.mark.asyncio
async def test_a_missing_result_or_a_failed_job_never_places_anything(dubs):
    probe, neighbours, _, _, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    view = job_done(neighbours, "job-1")
    view["results"] = view["results"][:1]
    probe.publish_event("conform.job.finished", view)
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    outcomes = {r["voice_key"]: r for r in await rows(probe)}
    missing = next(r for r in outcomes.values() if r["status"] == "rejected")
    assert "нет результата" in missing["outcome"]


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
    neighbours.submit_reply = {"unavailable": "сервис выравнивания недоступен", "retry": False}
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    await asyncio.sleep(0.2)
    assert {r["status"] for r in await rows(probe)} == {"ready"}
    neighbours.submit_reply = None
    probe.publish_event("conform.daemon.up", {})
    await statuses_until(sub, ["aligning", "aligning"])


@pytest.mark.asyncio
async def test_a_final_refusal_of_the_service_rejects_the_voices(dubs):
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = {"rejected": "путь вне общего хранилища"}
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "rejected", "rejected"])
    assert {r["outcome"] for r in await rows(probe)} == {"путь вне общего хранилища"}


@pytest.mark.asyncio
async def test_after_a_restart_a_job_that_ended_meanwhile_is_applied(db_path, dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    neighbours.jobs["job-1"] = job_done(neighbours, "job-1")
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await again.start()
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
    await again.start()
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
    assert [os.path.basename(p) for p in neighbours.submits[1]["dubs"]] == ["redheadsound.mp4"]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-2"))
    await statuses_until(sub, ["aligned", "placed"])
    assert {r["status"] for r in await rows(probe)} == {"placed"}


@pytest.mark.asyncio
async def test_a_transient_failure_with_the_service_up_is_retried_without_a_new_event(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = {"unavailable": "RemoteProtocolError: Server disconnected", "retry": True}
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    end = asyncio.get_running_loop().time() + 3
    while not neighbours.submits:
        assert asyncio.get_running_loop().time() < end
        await asyncio.sleep(0.01)
    neighbours.submit_reply = None  # the service recovers; nothing else announces it
    await statuses_until(sub, ["aligning", "aligning"])
    assert len(neighbours.submits) >= 2


@pytest.mark.asyncio
async def test_retries_stop_after_a_few_attempts(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.02)
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRIES", 2)
    probe, neighbours, _, _, sub, _ = await two_voices_ready(dubs, finished=False)
    neighbours.submit_reply = {"unavailable": "blip", "retry": True}
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready"])
    await asyncio.sleep(0.4)
    tried = len(neighbours.submits)
    assert 3 <= tried <= 4  # the first try, two retries, maybe one concurrent pass
    await asyncio.sleep(0.2)
    assert len(neighbours.submits) == tried


@pytest.mark.asyncio
async def test_the_scratch_directory_of_the_service_goes_when_no_job_of_the_series_runs(dubs):
    probe, neighbours, _, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1, 1), (1, 2, 2)]
    for episode in (1, 2):
        reference_on_disk(neighbours, episode)
    scratch = os.path.join(neighbours.library, "_tmp")
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 4)
    for fetch in neighbours.fetches:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready"] * 4 + ["aligning"] * 4)
    first, second = neighbours.submits
    os.makedirs(scratch)  # what the service leaves while it works
    neighbours.submits[:] = [first]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await asyncio.sleep(0.2)
    assert os.path.isdir(scratch)  # the other episode is still being aligned
    series_folder = os.path.join(neighbours.settings["voices.temp_root"], "7")
    assert os.path.isdir(series_folder)  # the second episode's files are still in it
    neighbours.submits[:] = [second]
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-2"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await asyncio.sleep(0.2)
    assert not os.path.exists(scratch)
    assert not os.path.exists(series_folder)  # nothing of the series is left in the temporary directory


@pytest.mark.asyncio
async def test_a_scratch_directory_with_content_is_left_alone(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    scratch = os.path.join(neighbours.library, "_tmp")
    touch(os.path.join(scratch, "run", "buffer.bin"))
    settled_sub = bus.subscribe("voices.episode.settled")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await asyncio.sleep(0.2)
    assert os.path.isfile(os.path.join(scratch, "run", "buffer.bin"))


# --- following the reference ----------------------------------------------------------------


async def placed_set(dubs):
    """One episode, two voices, aligned and placed next to the reference; the episode is settled."""
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    settled_sub = bus.subscribe("voices.episode.settled")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await statuses_until(sub, ["aligned", "aligned", "placed", "placed"])
    await asyncio.wait_for(settled_sub.queue.get(), 5)
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
    assert {r["sidecar_path"] for r in done} == {os.path.join(neighbours.library, "Renamed 01.BlackCat.ru.flac"),
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
    await asyncio.sleep(0.3)
    assert sub.queue.empty() and library_files(neighbours) == before
    assert len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_track_the_user_deleted_is_not_made_again(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    os.remove(f"{os.path.splitext(ref)[0]}.BlackCat.ru.flac")
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await statuses_until(sub, ["rejected"])
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["blackcat"]["status"] == "rejected" and by_voice["blackcat"]["outcome"] == "дорожка удалена"
    assert by_voice["redheadsound"]["status"] == "placed"
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.3)
    assert len(neighbours.fetches) == 2 and len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_replaced_reference_removes_the_tracks_and_the_voices_start_over(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    new_ref = os.path.join(neighbours.library, "Series s01e01 2160p.mp4")
    touch(new_ref)
    neighbours.uids[1] = "u1-new"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    # The old file is still there with the winner beside it: the winner is the reference.
    neighbours.extra_items.append({"unique_id": "u1", "season": 1, "episode_start": 1,
                                   "source_url": "x", "status": "completed", "superseded_by": "u1-new",
                                   "final_filename": os.path.basename(ref)})
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    assert not any(name.endswith(".flac") for name in library_files(neighbours))
    reset = await rows(probe)
    assert {r["sidecar_path"] for r in reset} == {None} and {r["ref_uid"] for r in reset} == {None}
    await settled(neighbours, 4)  # both voices are fetched again
    for fetch in neighbours.fetches[2:]:
        touch(fetch["path"], b"video")
        finish(probe, fetch["key"])
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    assert neighbours.submits[1]["ref"] == new_ref
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
    new_ref = os.path.join(neighbours.library, "Series s01e01 2160p.mp4")
    touch(new_ref)
    neighbours.uids[1] = "u1-new"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    probe.publish_event("conform.job.finished", old_job)
    await asyncio.sleep(0.3)
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
    await asyncio.sleep(0.3)
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
    await asyncio.sleep(0.3)
    assert sub.queue.empty() and len(await rows(probe)) == 2


@pytest.mark.asyncio
async def test_an_event_carries_the_voice_and_the_reason_so_the_interface_asks_nothing(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    changes = bus.subscribe("voices.dub.changed")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", blackcat={
        "suspect": True, "warnings": ["остаток вне допуска"]}))
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    seen = {}
    while not changes.queue.empty():
        event = changes.queue.get_nowait().payload
        seen[(event["voice_key"], event["status"])] = event
    rejected = seen[("blackcat", "rejected")]
    assert rejected["voice_label"] == "BlackCat" and rejected["detail"].startswith("подозрительно")
    assert seen[("redheadsound", "aligned")]["detail"] is None
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["blackcat"]["detail"].startswith("подозрительно") and by_voice["redheadsound"]["detail"] is None


# --- the five states the user sees ------------------------------------------------------------


def test_every_step_of_a_voice_over_shows_as_one_of_five_states():
    shown = {status: public_state(status) for status in (
        "pending", "stale", "downloading", "ready", "aligned", "placed", "moved", "rejected", "failed")}
    assert shown == {"pending": "planned", "stale": "planned", "downloading": "running", "ready": "queued",
                     "aligned": "running", "placed": "done", "moved": "done", "rejected": "error",
                     "failed": "error"}
    assert set(shown.values()) == {"planned", "queued", "running", "done", "error"}


def test_a_voice_over_in_the_service_waits_or_runs_by_the_state_of_its_job():
    for waiting in (None, "queued", "paused"):
        assert public_state("aligning", waiting) == "queued"
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
    assert ("ready", "queued") in seen and seen.count(("aligning", "queued")) == 2
    assert set((await states_of(probe)).values()) == {"queued"}
    probe.publish_event("conform.queue.changed", {"daemon": {"up": True}, "tasks": [{"id": "job-1", "status": "running"}]})
    await statuses_until(sub, ["aligning", "aligning"])
    assert set((await states_of(probe)).values()) == {"running"}
    # A picture that changes nothing is not an event.
    probe.publish_event("conform.queue.changed", {"daemon": {"up": True}, "tasks": [{"id": "job-1", "status": "running"}]})
    await asyncio.sleep(0.2)
    assert sub.queue.empty()
    probe.publish_event("conform.queue.changed", {"daemon": {"up": False}, "tasks": []})
    await statuses_until(sub, ["aligning", "aligning"])
    assert set((await states_of(probe)).values()) == {"queued"}


@pytest.mark.asyncio
async def test_the_end_of_a_job_shows_done_and_error_without_any_other_word(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    changes = bus.subscribe("voices.dub.changed")
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", redheadsound={
        "critical": ["нет общего звука"]}))
    await statuses_until(sub, ["aligned", "placed", "rejected"])
    states = {}
    while not changes.queue.empty():
        event = changes.queue.get_nowait().payload
        states[(event["voice_key"], event["status"])] = event["state"]
    assert states[("blackcat", "placed")] == "done" and states[("redheadsound", "rejected")] == "error"
    assert await states_of(probe) == {"blackcat": "done", "redheadsound": "error"}
