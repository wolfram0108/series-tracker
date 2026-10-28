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
from modules.voices.identity import Equivalence, group_key, same_voice, tokens, voice_groups
from modules.voices.layout import (DEFAULT_PRIORITY, choose_reference, download_chain,
                                   plan_layout, rank)
from modules.voices.states import public_state


def code_of(stored):
    """The code of a stored reason or of a pushed detail."""
    from modules.voices.reasons import parse
    detail = stored if isinstance(stored, dict) else parse(stored)
    return detail and detail["code"]


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


def test_rules_given_as_data_join_two_spellings_without_touching_the_built_in_table():
    rules = Equivalence.of({"AniLiberty": "AniLibria", " Red-Head ": "Red Head Sound"})
    assert same_voice("AniLiberty", "AniLibria", rules) and same_voice("Red Head", "RedHeadSound", rules)
    assert not same_voice("AniLiberty", "AniLibria")
    assert group_key(["AniLiberty", "AniLibria"], rules) == "anilibria"


def test_rules_that_are_not_names_are_ignored():
    assert Equivalence.of(["not", "a", "mapping"]).voices == {}
    assert Equivalence.of({"a": 1, 2: "b", "": "c", "d": "   ", "ok": "fine"}).voices == {"ok": "fine"}
    assert Equivalence.of(None, "text").players == {}




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
        self.has_voices = True
        self.parses = 0
        self.conform_cancels = []
        self.finish_at_once = False
        self.lost_before_arrival = 0
        self.lost_after_arrival = 0
        self.sent_keys = {}
        self.cancel_done = True
        self.service_reachable = True
        super().__init__(bus)

    def register(self):
        self.handle("catalog.series.get", self.on_series)
        self.handle("sources.provider.info", self.on_provider_info)
        self.handle("sources.parse", self.on_parse)
        self.handle("downloads.episodes.get", self.on_episodes)
        self.handle("settings.value.get", self.on_setting)
        self.handle("settings.values.by_prefix", self.on_settings_by_prefix)
        self.handle("downloads.fetch", self.on_fetch)
        self.handle("downloads.fetch.cancel", self.on_cancel)
        self.handle("conform.submit", self.on_submit)
        self.handle("conform.cancel", self.on_conform_cancel)
        self.handle("conform.job.find", self.on_job_find)
        self.handle("conform.job.get", self.on_job_get)

    async def on_series(self, env):
        return {"id": env.payload["series_id"], "url": "https://site/title.html", "name": "Series",
                "save_path": self.library, "source_type": self.source_type}

    async def on_provider_info(self, env):
        return {"provider": "fake", "voices": self.has_voices}

    async def on_parse(self, env):
        self.parses += 1
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

    async def on_settings_by_prefix(self, env):
        prefix = env.payload["prefix"]
        return {"values": {key: value for key, value in self.settings.items() if key.startswith(prefix)}}

    async def on_fetch(self, env):
        self.fetches.append(dict(env.payload))

    async def on_cancel(self, env):
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
            raise RuntimeError("the answer was lost")
        return self.submit_reply or {"job_id": job_id}

    async def on_conform_cancel(self, env):
        self.conform_cancels.append(env.payload["job_id"])
        return {"done": self.cancel_done}

    async def on_job_find(self, env):
        if not self.service_reachable:
            return {"reachable": False, "job_id": None}
        return {"reachable": True, "job_id": self.sent_keys.get(env.payload["key"])}

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
    event = await change(sub, "ready")
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
    neighbours.plan.append((1, 4, 3))
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




def touch(path, content=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(content)


async def statuses_until(sub, wanted, seconds=5.0):
    """Wait on the pushed events until `wanted` (a sorted list of statuses seen) is reached.

    The announcements of a planned row and of a sent download are skipped unless wanted."""
    seen = []
    end = asyncio.get_running_loop().time() + seconds
    while sorted(seen) != sorted(wanted):
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


async def quiet(voices):
    """Wait until the module has nothing running in the background, so a test may change its data."""
    while voices._bg:
        await asyncio.gather(*list(voices._bg))


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
    return job_result(neighbours.submits[-1], job_id, **per_dub)


def job_result(submit, job_id, **per_dub):
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
    assert by_voice["blackcat"]["status"] == "rejected" and code_of(by_voice["blackcat"]["outcome"]) == "suspect"
    assert code_of(by_voice["redheadsound"]["outcome"]) == "critical"
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
    assert code_of(missing["outcome"]) == "no_result"


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
    assert by_voice["blackcat"]["status"] == "rejected" and code_of(by_voice["blackcat"]["outcome"]) == "track_removed"
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
    assert rejected["voice_label"] == "BlackCat" and rejected["detail"]["code"] == "suspect"
    assert seen[("redheadsound", "aligned")]["detail"] is None
    by_voice = {r["voice_key"]: r for r in await rows(probe)}
    assert by_voice["blackcat"]["detail"]["code"] == "suspect" and by_voice["redheadsound"]["detail"] is None




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
    await asyncio.sleep(0.2)
    assert sub.queue.empty()
    probe.publish_event("conform.queue.changed", {"daemon": {"up": False}, "tasks": []})
    await statuses_until(sub, ["aligning", "aligning"])
    assert set((await states_of(probe)).values()) == {"align_queued"}


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


def start(probe, fetch):
    probe.publish_event("downloads.fetch.started", {"key": fetch["key"], "series_id": 7, "path": fetch["path"]})


@pytest.mark.asyncio
async def test_a_sent_download_is_queued_until_it_really_starts_and_downloading_after(dubs):
    probe, neighbours, _, bus, sub, _ = await two_voices_ready(dubs, finished=False)
    changes = bus.subscribe("voices.dub.changed")
    assert set((await states_of(probe)).values()) == {"download_queued"}
    first = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    start(probe, first)
    await change_state(changes, "blackcat", "downloading")
    assert await states_of(probe) == {"blackcat": "downloading", "redheadsound": "download_queued"}
    touch(first["path"], b"video")
    finish(probe, first["key"])
    ready = await change(changes, "ready")
    assert ready["state"] == "align_queued"
    assert (await states_of(probe))["blackcat"] == "align_queued"


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
    await asyncio.sleep(0.2)
    changes = bus.subscribe("voices.dub.changed")
    black = next(f for f in neighbours.fetches if "BlackCat" in f["url"])
    start(probe, black)
    await change_state(changes, "blackcat", "downloading")
    finish(probe, black["key"], ok=False, error="timed out")
    await settled(neighbours, 3)  # the fallback source of BlackCat is sent
    await change_state(changes, "blackcat", "download_queued")
    assert (await states_of(probe))["blackcat"] == "download_queued"
    start(probe, neighbours.fetches[2])
    await change_state(changes, "blackcat", "downloading")
    assert (await states_of(probe))["blackcat"] == "downloading"


@pytest.mark.asyncio
async def test_a_start_of_a_foreign_download_is_none_of_our_business(dubs):
    probe, neighbours, _, bus, sub, _ = await two_voices_ready(dubs, finished=False)
    await asyncio.sleep(0.2)
    changes = bus.subscribe("voices.dub.changed")
    probe.publish_event("downloads.fetch.started", {"key": "other:1", "series_id": 7, "path": "/x"})
    probe.publish_event("downloads.fetch.started", {"key": "dub:99999", "series_id": 7, "path": "/x"})
    await asyncio.sleep(0.2)
    assert changes.queue.empty()


@pytest.mark.asyncio
async def test_a_series_whose_provider_has_no_voice_catalog_is_not_even_parsed(dubs):
    probe, neighbours, _, _ = dubs
    neighbours.has_voices = False
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.3)
    assert neighbours.parses == 0 and neighbours.fetches == [] and await rows(probe) == []
    neighbours.has_voices = True
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    assert neighbours.parses == 1




async def capped_two_episodes(dubs, cap="1"):
    probe, neighbours, voices, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1, 1), (1, 2, 2)]
    neighbours.settings["voices.max_temp_episodes"] = cap
    for episode in (1, 2):
        reference_on_disk(neighbours, episode)
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    await asyncio.sleep(0.3)
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
    await asyncio.sleep(0.1)
    assert len(neighbours.fetches) == sent


@pytest.mark.asyncio
async def test_the_job_of_a_replaced_reference_is_cancelled_in_the_service(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    new_ref = os.path.join(neighbours.library, "Series s01e01 2160p.mp4")
    touch(new_ref)
    neighbours.uids[1] = "u1-new"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    await asyncio.sleep(0.1)
    assert neighbours.conform_cancels == ["job-1"]  # one job for the two voice-overs


@pytest.mark.asyncio
async def test_deleting_a_series_cancels_its_job_in_the_service_and_a_finished_one_is_left(dubs):
    probe, neighbours, _, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    report = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(report.queue.get(), 5)
    await asyncio.sleep(0.1)
    assert neighbours.conform_cancels == ["job-1"]


@pytest.mark.asyncio
async def test_a_placed_track_costs_no_cancel_when_its_reference_is_replaced(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    touch(os.path.join(neighbours.library, "Series s01e01 2160p.mp4"))
    neighbours.uids[1] = "u1-new"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    await asyncio.sleep(0.1)
    assert neighbours.conform_cancels == []




def test_layout_takes_synonymous_voices_of_different_sites_as_one_voice():
    sources = [src("cvh", "FumoDub", [1, 2], url="https://site/?player=cvh&voice=FumoDub"),
               src("kodik", "AniLibria", [1, 2, 3]), src("cvh", "AniLiberty", [1, 2])]
    without = plan_layout(sources, "manual", sources[0]["url"])
    assert sorted(r["label"] for r in without["voices"] if r["role"] == "dub") == ["AniLiberty", "AniLibria"]
    rules = Equivalence.of({"aniliberty": "anilibria"})
    with_rules = plan_layout(sources, "manual", sources[0]["url"], rules=rules)
    dub = [r for r in with_rules["voices"] if r["role"] == "dub"]
    assert len(dub) == 1 and dub[0]["key"] == "anilibria"
    assert dub[0]["source"]["player"] == "kodik" and [f["player"] for f in dub[0]["fallbacks"]] == ["cvh"]


def test_players_that_the_rules_call_the_same_take_one_place_in_the_priority():
    assert rank("vk", ["alloha", "cvh", "kodik"]) == 3
    rules = Equivalence.of(players={"vk": "cvh"})
    assert rank("vk", ["alloha", "cvh", "kodik"], rules) == 1 == rank("cvh", ["alloha", "cvh", "kodik"], rules)
    candidates = [{"player": "kodik", "resolution": 720}, {"player": "vk", "resolution": 720}]
    assert choose_reference(candidates, ["alloha", "cvh", "kodik"], rules)["player"] == "vk"


@pytest.mark.asyncio
async def test_the_module_reads_the_rules_from_settings_and_survives_garbage(system):
    sources = [src("cvh", "Ref", [1], url="https://site/?player=cvh&voice=Ref"),
               src("kodik", "AniLibria", [1, 2]), src("cvh", "AniLiberty", [1])]
    query = {"sources": sources, "reference": sources[0]["url"]}
    plain = await system.request("voices.layout", query, timeout=5)
    assert len([r for r in plain["voices"] if r["role"] == "dub"]) == 2
    await system.request("settings.value.set", {"key": "voices.aliases",
                                                "value": '{"aniliberty": "anilibria"}'}, timeout=5)
    merged = await system.request("voices.layout", query, timeout=5)
    assert len([r for r in merged["voices"] if r["role"] == "dub"]) == 1
    await system.request("settings.value.set", {"key": "voices.aliases", "value": "{broken"}, timeout=5)
    again = await system.request("voices.layout", query, timeout=5)
    assert len([r for r in again["voices"] if r["role"] == "dub"]) == 2




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
    # Two different voices (the credit names two studios; the other is one word) with one safe title.
    neighbours.sources.append(linked("kodik", "AniMy / RuChiMe", range(1, 4)))
    neighbours.sources.append(linked("kodik", "AniMy RuChiMe", range(1, 4)))
    neighbours.plan[:] = [(1, 1, 1)]
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
    placed = {r["sidecar_path"] for r in await rows(probe)}
    assert len(placed) == 3 and all(os.path.isfile(p) for p in placed)
    assert sum(" 2.ru.flac" in p for p in placed) == 1
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 1})
    await asyncio.sleep(0.3)
    assert {r["sidecar_path"] for r in await rows(probe)} == placed  # a later look moves nothing




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
        await asyncio.sleep(0.005)
    return peak


async def a_series_of(dubs, episodes, voices_count, cap):
    probe, neighbours, voices, bus = dubs
    names = ["BlackCat", "RedHeadSound", "AniStar", "Animy", "FumoSub", "Lostfilm"][:voices_count]
    reference = linked("cvh", "FumoDub", episodes, url="https://site/?player=cvh&voice=FumoDub")
    neighbours.sources[:] = [reference] + [linked("kodik", name, episodes) for name in names]
    neighbours.reference_url = reference["url"]
    neighbours.plan[:] = [(1, n, n) for n in episodes]
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
    await asyncio.sleep(0.3)
    flacs = [name for name in library_files(neighbours) if name.endswith(".flac")]
    assert len(flacs) == 48 and neighbours.conform_cancels == []
    temp_root = neighbours.settings["voices.temp_root"]
    assert not os.path.exists(temp_root) or os.listdir(temp_root) == []
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
    await again.start()
    probe.publish_event("conform.daemon.up", {})

    async def all_placed():
        return sum(r["status"] == "placed" for r in await rows(probe)) == 24

    try:
        await serve(probe, neighbours, holder, all_placed, progress)
        await asyncio.sleep(0.3)
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
    await asyncio.sleep(0.2)
    after = await voices.repo.get(stale["id"])
    assert after["status"] == "pending" and row["voice_key"] == after["voice_key"]


# files and records that must not part: interrupted moves, failed removals --------------------


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
    assert after["status"] == "placed" and after["sidecar_path"] == destination
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
    await voices.repo.begin_move(row["id"], expected, row["sidecar_path"])  # interrupted before the move
    await voices._advance(7)
    after = await voices.repo.get(row["id"])
    assert after["status"] == "placed" and after["sidecar_path"] == expected and after["move_from"] is None
    assert os.path.isfile(expected) and not os.path.exists(row["sidecar_path"])
    assert len(library_files(neighbours)) == 3


@pytest.mark.asyncio
async def test_a_failed_removal_hands_the_file_to_the_cleanup_journal_and_is_tried_again(dubs):
    from unittest.mock import patch

    voices = dubs[2]
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    group = await voices.repo.for_series(7)
    paths = [row["sidecar_path"] for row in group]
    with patch("modules.voices.module.os.remove", side_effect=PermissionError("read-only")):
        assert await voices._follow(7, 1, 1, group, ref, "another-reference")
    assert all(os.path.exists(path) for path in paths)  # the tracks are still there...
    assert {entry["path"] for entry in await voices.repo.cleanup_list()} == set(paths)  # ...and not forgotten
    assert all(row["sidecar_path"] is None for row in await voices.repo.for_series(7))
    await voices._sweep_cleanup()
    assert not any(os.path.exists(path) for path in paths) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_failed_removal_of_the_temporary_folder_is_journaled_and_the_episode_still_settles(dubs):
    from unittest.mock import patch

    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    settled_sub = bus.subscribe("voices.episode.settled")
    with patch("modules.voices.module.shutil.rmtree", side_effect=PermissionError("busy")):
        probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
        await asyncio.wait_for(settled_sub.queue.get(), 5)
        journal = await voices.repo.cleanup_list()
    assert len(journal) == 1 and journal[0]["kind"] == "tree" and os.path.isdir(journal[0]["path"])
    await voices._sweep_cleanup()
    assert not os.path.exists(journal[0]["path"]) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_file_that_is_already_gone_is_a_success_not_a_journal_entry(dubs):
    voices = dubs[2]
    assert await voices._discard(os.path.join(os.sep, "tmp", "nobody-has-this")) is True
    assert await voices._discard(os.path.join(os.sep, "tmp", "nobody-has-this-dir"), tree=True) is True
    assert await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_temporary_files_are_removed_where_they_were_made_not_where_the_settings_now_point(dubs, tmp_path):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    made_in = neighbours.settings["voices.temp_root"]
    neighbours.settings["voices.temp_root"] = str(tmp_path / "somewhere-else")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await quiet(voices)
    assert not os.path.exists(os.path.join(made_in, "7", "s01e01"))
    assert not os.path.exists(tmp_path / "somewhere-else")


@pytest.mark.asyncio
async def test_a_removal_that_the_storage_does_not_answer_is_journaled_not_awaited_forever(dubs, monkeypatch):
    import time

    voices = dubs[2]
    monkeypatch.setattr("modules.voices.module.REMOVE_TIMEOUT_S", 0.1)
    monkeypatch.setattr(type(voices), "_unlink", staticmethod(lambda path, tree: time.sleep(0.6)))
    started = time.monotonic()
    assert await voices._discard("/some/slow/file") is False
    assert time.monotonic() - started < 0.5
    assert [entry["path"] for entry in await voices.repo.cleanup_list()] == ["/some/slow/file"]


# a request whose answer is lost, a job that must not be sent twice -------------------------


async def ready_to_send(dubs, monkeypatch):
    monkeypatch.setattr("modules.voices.module.SUBMIT_RETRY_S", 0.05)
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs, finished=False)
    return probe, neighbours, voices, bus, sub, ref


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
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])  # the number is found again
    assert len(neighbours.submits) == 1
    assert {r["job_id"] for r in await rows(probe)} == {"job-1"}
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    assert {r["status"] for r in await rows(probe)} == {"placed"} and len(neighbours.submits) == 1


@pytest.mark.asyncio
async def test_a_job_that_never_arrived_is_sent_again_once_the_service_says_it_has_none(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_before_arrival = 1
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    assert len(neighbours.submits) == 1 and {r["job_id"] for r in await rows(probe)} == {"job-1"}


@pytest.mark.asyncio
async def test_while_the_service_cannot_say_whether_it_has_the_job_nothing_is_sent_again(dubs, monkeypatch):
    probe, neighbours, voices, bus, sub, ref = await ready_to_send(dubs, monkeypatch)
    neighbours.lost_after_arrival = 1
    neighbours.service_reachable = False
    finish_all(probe, neighbours)
    await statuses_until(sub, ["ready", "ready"])
    await asyncio.sleep(0.5)
    assert len(neighbours.submits) == 1
    assert {(r["status"], r["job_id"]) for r in await rows(probe)} == {("aligning", None)}
    neighbours.service_reachable = True
    probe.publish_event("conform.daemon.up", {})
    await statuses_until(sub, ["aligning", "aligning"])
    assert len(neighbours.submits) == 1 and {r["job_id"] for r in await rows(probe)} == {"job-1"}


@pytest.mark.asyncio
async def test_a_cancel_that_could_not_be_made_is_kept_and_made_when_the_service_is_back(dubs):
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    await quiet(voices)
    neighbours.cancel_done = False
    touch(os.path.join(neighbours.library, "Series s01e01 2160p.mp4"))
    neighbours.uids[1] = "u1-new"
    neighbours.references[1] = "Series s01e01 2160p.mp4"
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})
    await statuses_until(sub, ["stale", "stale"])
    assert [e["path"] for e in await voices.repo.cleanup_list()] == ["job-1"]
    neighbours.cancel_done = True
    probe.publish_event("conform.daemon.up", {})
    end = asyncio.get_running_loop().time() + 3
    while await voices.repo.cleanup_list():
        assert asyncio.get_running_loop().time() < end
        await asyncio.sleep(0.01)
    assert neighbours.conform_cancels.count("job-1") >= 2


# what the interface is told must be what the database holds -------------------------------


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
            shown[key], revs[key] = item, snapshot["rev"]
    for event in events:
        key = (event["series_id"], event["season"], event["episode"], event["voice_key"])
        if event["rev"] <= revs.get(key, 0):
            continue
        revs[key] = event["rev"]
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
    neighbours.plan[:] = [(1, 1, 1)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.6)
    later = drained(sub)
    assert {(e["episode"], e["status"]) for e in later} == {(2, "removed")} and len(later) == 2
    assert picture(events + later) == await held(probe)


@pytest.mark.asyncio
async def test_deleting_the_series_tells_the_interface_every_row_is_gone(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await asyncio.sleep(0.3)
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
    revs = [e["rev"] for e in drained(sub)]
    assert revs == sorted(set(revs))


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
    older = {"series_id": 7, "season": 1, "episode": 1, "voice_key": "blackcat", "voice_label": "BlackCat",
             "status": "failed", "state": "error", "detail": "old", "rev": snapshot["rev"] - 1}
    assert picture([older], snapshot) == await held(probe)


# what the module reads from its settings and from its own rows -----------------------------


@pytest.mark.asyncio
async def test_a_wrong_setting_is_logged_once_and_the_default_is_used(dubs, caplog):
    probe, neighbours, voices, bus = dubs
    neighbours.settings["voices.max_temp_episodes"] = "many"
    with caplog.at_level("WARNING"):
        probe.send_command("voices.dispatch", {"series_id": 7})
        await settled(neighbours, 3)
        probe.send_command("voices.dispatch", {"series_id": 7})
        await asyncio.sleep(0.5)
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
    assert all(os.path.isfile(r["sidecar_path"]) for r in await rows(probe))


@pytest.mark.asyncio
async def test_changed_rules_do_not_make_a_new_voice_of_a_recorded_one(dubs):
    probe, neighbours, voices, bus = dubs
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    neighbours.plan[:] = [(1, 1, 1)]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    before = {r["voice_key"] for r in await rows(probe)}
    assert before == {"blackcat", "redheadsound"}
    neighbours.settings["voices.aliases"] = '{"redheadsound": "zzz"}'
    probe.send_command("voices.dispatch", {"series_id": 7})
    await asyncio.sleep(0.6)
    assert {r["voice_key"] for r in await rows(probe)} == before and len(neighbours.fetches) == 2
