"""Tests of `downloads.fetch`: the executor of downloads fetches a stream behind a link into a file, under a fuse of silence,
with retries of a transient failure and its own limit of parallel fetches. The stream provider and the settings are fakes;
the downloader is an injected coroutine, so yt-dlp is not needed.
"""
import asyncio
import logging
import os

from types import SimpleNamespace

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.db import Database
from core.reasons import parse, reason
from modules.downloads import DownloadsModule
from calc.downloads.failure import Final
from modules.downloads.ytdlp import player_target
from settle import settle, wait_until
from schema_db import migrated


class FakeNeighbours(BaseModule):
    """Stands for the neighbours a fetch talks to: the stream provider and the settings."""
    name = "fake_neighbours"

    def __init__(self, bus):
        self.settings: dict[str, str] = {}
        self.stream_replies: dict[str, dict] = {}
        self.stream_raises: set[str] = set()
        self.stream_payloads: list[dict] = []
        # The downloads a player makes itself: what it was asked, the gate it waits at, the files it was told to stop.
        self.player_fetches: list[dict] = []
        self.player_gate: asyncio.Event | None = None
        self.player_stops: list[str] = []
        self.player_reply: dict | None = None  # what the player answers instead of the file, when a test says so
        super().__init__(bus)

    def register(self):
        self.handle("settings.value.get", self.on_get)
        self.handle("settings.value.set", self.on_set)
        self.handle("sources.video.stream", self.on_stream)
        self.handle("sources.video.fetch", self.on_player_fetch, concurrent=True)
        self.handle("sources.video.fetch.stop", self.on_player_stop)

    async def on_player_fetch(self, env):
        self.player_fetches.append(dict(env.payload))
        target = env.payload["target"]
        self.publish_event("sources.video.progress", {"target": target, "phase": "download", "progress": 40,
                                                      "dlspeed": 12 * 1024 ** 2, "eta": 3, "total_size_mb": 300.0})
        if self.player_gate is not None:
            await self.player_gate.wait()
        if self.player_reply is not None:
            return self.player_reply
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as file:
            file.write(b"segments")
        return {"ok": True, "seconds": 1200.0, "longest": 4.0, "height": 720}

    async def on_player_stop(self, env):
        self.player_stops.append(env.payload["target"])
        if self.player_gate is not None:
            self.player_gate.set()
        return {"stopped": True}

    async def on_set(self, env):
        self.settings[env.payload["key"]] = env.payload["value"]
        return {"ok": True}

    async def on_stream(self, env):
        url = env.payload["url"]
        self.stream_payloads.append(dict(env.payload))
        if url in self.stream_raises:
            raise RuntimeError("sources unavailable")
        return self.stream_replies.get(url, {"handled": False})

    async def on_get(self, env):
        return {"key": env.payload["key"], "value": self.settings.get(env.payload["key"])}


class FakeDownloader:
    """A steered downloader: it writes a file, or fails as told."""

    def __init__(self):
        self.fail_urls: set[str] = set()
        self.timeouts: dict[str, int] = {}   # url -> how many more attempts end in a network timeout
        self.failures: dict[str, list[str]] = {}  # url -> the words its next attempts fail with, in order
        self.silent: dict[str, int] = {}     # url -> how many more attempts wait for ever with no sign of life
        self.started: list[str] = []
        self.kwargs: dict[str, dict] = {}
        self.gate: asyncio.Event | None = None
        self.concurrent = 0
        self.max_concurrent = 0

    async def __call__(self, url, path, on_progress, *, threads=6, **kw):
        self.threads = threads
        self.started.append(url)
        self.kwargs[url] = kw
        if kw.get("fetcher") is not None:
            # A file its player takes itself: the raw stream by the player, the container made of it here.
            ok, words, _seconds, _longest = await kw["fetcher"](player_target(path), on_progress)
            if ok:
                os.replace(player_target(path), path)
            return ok, words
        self.concurrent += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.silent.get(url, 0):
                self.silent[url] -= 1
                # A lost end of a program looks like this from the outside: nothing ever comes back.
                await asyncio.get_running_loop().create_future()
            await on_progress({"phase": "download", "progress": 50, "dlspeed": 3 * 1024 ** 2, "eta": 5, "total_size_mb": 10.0})
            if self.gate:
                await self.gate.wait()
            if url in self.fail_urls:
                return False, Final(reason("video_unavailable"))
            if self.timeouts.get(url, 0):
                self.timeouts[url] -= 1
                return False, "ffmpeg direct download: Connection timed out"
            if self.failures.get(url):
                return False, self.failures[url].pop(0)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(b"video")
            await on_progress({"phase": "download", "progress": 100, "dlspeed": 0, "eta": 0})
            return True, ""
        finally:
            self.concurrent -= 1


class Probe(BaseModule):
    name = "probe"


@pytest.fixture
def env_paths(tmp_path):
    db_path = migrated(tmp_path / "test.db")
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    return str(db_path), str(media_dir)


@pytest.fixture
async def system(env_paths):
    db_path, media_dir = env_paths
    bus = Bus()
    db = Database(db_path)
    fake = FakeNeighbours(bus)
    dl = FakeDownloader()
    downloads = DownloadsModule(bus, db, downloader=dl)
    probe = Probe(bus)
    runner = Runner(bus, [fake, downloads, probe])
    await runner.start()
    _watch(runner, downloads)
    yield bus, fake, dl, probe
    await runner.stop()
    _watch(None, None)


# The system a test started, for the waiters below: the fixture that starts one says which it is.
_running = SimpleNamespace(bus=None, modules=(), downloads=None)


def _watch(runner, downloads):
    _running.bus, _running.modules, _running.downloads = (
        (runner.bus, tuple(runner.modules), downloads) if runner else (None, (), None))


async def _wait(predicate, timeout=3.0):
    """True when the condition holds; it is looked at again after each step of the bus and each end of a task of the modules.
    `timeout` only ends a test that is stuck."""
    return await wait_until(predicate, _running.bus, *_running.modules, seconds=timeout)


async def _quiet():
    """Returns when the bus took apart every envelope and the modules run nothing: what did not happen by now never will."""
    await settle(_running.bus, *_running.modules)


async def _fetch(probe, bus, expect, **job):
    """Sends downloads.fetch jobs and returns the `finished` events of `expect` of them."""
    sub = bus.subscribe("downloads.fetch.finished")
    for item in job["jobs"]:
        probe.send_command("downloads.fetch", item)
    return [(await asyncio.wait_for(sub.queue.get(), 5)).payload for _ in range(expect)]


@pytest.mark.asyncio
async def test_fetch_unreachable_sources_is_an_error_not_a_blind_download(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, fake, dl, probe = system
    fake.stream_raises.add("https://site/#cvh=3")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:3", "url": "https://site/#cvh=3", "path": str(tmp_path / "b.mp4"), "series_id": 3}])
    assert done["ok"] is False and parse(done["error"])["code"] == "stream_not_got" and dl.started == []
    # Our own module did not answer: nothing was learnt of the source, and its owner asks it again at the next round.
    assert done["transient"] is True


@pytest.mark.asyncio
async def test_a_fetch_silent_past_the_fuse_is_taken_off_with_what_it_waited_for_and_tried_again(system, tmp_path, monkeypatch,
                                                                                                    caplog):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    monkeypatch.setattr("modules.downloads.module.SILENCE_FUSE_S", 0.3)  # a short fuse: the test waits for it to end
    bus, _, dl, probe = system
    dl.silent["https://site/#kodik=7"] = 1
    with caplog.at_level(logging.WARNING, logger="downloads"):
        (done,) = await _fetch(probe, bus, 1, jobs=[
            {"key": "dub:7", "url": "https://site/#kodik=7", "path": str(tmp_path / "s.mp4"), "series_id": 3}])
    assert done["ok"] is True and dl.started.count("https://site/#kodik=7") == 2
    taken_off = [record.getMessage() for record in caplog.records if "ждала" in record.getMessage()]
    assert len(taken_off) == 1 and "__call__" in taken_off[0], "the log names what the download waited for"


@pytest.mark.asyncio
async def test_a_need_is_one_card_through_the_variants_and_its_sync_leaves_only_what_failed_or_was_rebuilt(system, tmp_path):
    """The plan takes another variant of a need: the same card, the line of the old variant, «План перестроен на» the new
    one. At the end of the sync a finished card stays only when its plan was rebuilt, a failed one always; they stay in
    the store until the user removes them or the next sync of the series begins."""
    bus, fake, dl, probe = system

    def fetch(key, url, need_key, player, ok=True):
        if not ok:
            dl.fail_urls.add(url)
        probe.send_command("downloads.fetch", {
            "key": key, "url": url, "path": str(tmp_path / f"{key}.mp4"), "series_id": 88,
            "need": {"key": need_key, "series_id": 88, "series_name": "ДжоДжо", "sync_started": "t1", "episode": "s06e08",
                     "kind": "dub", "group": "Kazoku Project", "voice_number": 15, "cap": 720},
            "variant": {"source_id": 55, "source_n": 1, "source_color": 0, "place": "old.yummyani.me", "player": player,
                        "player_label": player.upper(), "height": 1080 if player == "cvh" else 720}})

    probe.publish_event("scan.run.changed", {"series_id": 88, "active": True, "seq": 1})
    fetch("k1", "https://site/#cvh=1", "need:kazoku", "cvh", ok=False)
    await _wait(lambda: "https://site/#cvh=1" in dl.started)
    await _quiet()
    fetch("k2", "https://site/#kodik=1", "need:kazoku", "kodik")
    fetch("k3", "https://site/#kodik=2", "need:plain", "kodik")
    fetch("k4", "https://site/#cvh=4", "need:lost", "cvh", ok=False)
    await _wait(lambda: os.path.isfile(tmp_path / "k3.mp4"))
    await _quiet()
    cards = {each["id"]: each for each in (await probe.request("downloads.queue.get", {}, timeout=5)).model_dump(mode="json")["tasks"]}
    kazoku = cards["need:kazoku"]
    assert [(line["kind"], line["variant"]["player"] if line["variant"] else None, line["outcome"])
            for line in kazoku["log"]] == [("attempt", "cvh", "failed"), ("replan", "kodik", "running"),
                                           ("attempt", "kodik", "done")]
    assert kazoku["status"] == "done" and kazoku["replanned"] and kazoku["need"]["voice_number"] == 15

    probe.publish_event("scan.run.changed", {"series_id": 88, "active": False, "seq": 2})
    await _quiet()
    left = {each["id"]: each for each in (await probe.request("downloads.queue.get", {}, timeout=5)).model_dump(mode="json")["tasks"]}
    assert set(left) == {"need:kazoku", "need:lost"} and left["need:lost"]["log"][-1]["kind"] == "exhausted"
    assert {card.id for card in await _running.downloads.repo.cards()} == {"need:kazoku", "need:lost"}

    assert (await probe.request("downloads.card.remove", {"id": "need:lost"}, timeout=5)).removed
    probe.publish_event("scan.run.changed", {"series_id": 88, "active": True, "seq": 3})
    await _quiet()
    assert (await probe.request("downloads.queue.get", {}, timeout=5)).model_dump(mode="json")["tasks"] == []
    assert await _running.downloads.repo.cards() == []


@pytest.mark.asyncio
async def test_how_many_go_at_once_is_by_the_way_the_file_comes(system, tmp_path):
    """Downloads by many connections share the threads of all of them (32 by 16 a file: two at once); downloads by one
    connection have places of their own. A full way holds neither the queue nor the other way, and a fetch that waited
    for its place asks its stream anew."""
    bus, fake, dl, probe = system
    assert (await probe.request("downloads.limits.get", {})).model_dump(mode="json") == {
        "threads_total": 32, "threads_per_file": 16, "single_at_once": 4, "provider_at_once": 2, "multi_at_once": 2}
    said = await probe.request("downloads.limits.set", {"single_at_once": "1"})
    assert said.ok and said.limits.single_at_once == 1
    refused = await probe.request("downloads.limits.set", {"threads_total": "0"})
    assert not refused.ok and "download_limit_invalid" in refused.reason
    assert fake.settings == {"download_single_at_once": "1"}, "a limit is kept as a setting; a refused one is not"
    dl.gate = asyncio.Event()
    # A page yt-dlp reads takes many connections; a stream ffmpeg reads (a link with the headers of its host) takes one.
    for i in range(2):
        fake.stream_replies[f"https://site/#cvh={i}"] = {"handled": True, "stream_url": f"https://cdn/{i}.mp4",
                                                         "headers": {"Referer": "https://site/"}}
    for key, url in [("v0", "https://site/#vk=0"), ("v1", "https://site/#vk=1"), ("v2", "https://site/#vk=2"),
                     ("s0", "https://site/#cvh=0"), ("s1", "https://site/#cvh=1")]:
        probe.send_command("downloads.fetch", {"key": key, "url": url, "path": str(tmp_path / f"{key}.mp4"), "series_id": 3})
    assert await _wait(lambda: dl.concurrent == 3)
    await _running.bus.settled()
    assert sorted(dl.started) == ["https://cdn/0.mp4", "https://site/#vk=0", "https://site/#vk=1"]
    assert dl.threads == 16
    dl.gate.set()
    assert await _wait(lambda: len(dl.started) == 5)
    # The third page waited for a place of its way: its stream was asked once more before it went.
    assert [each["url"] for each in fake.stream_payloads].count("https://site/#vk=2") == 2


# --- a file its player downloads itself (Alloha) -----------------------------------------------------------------------

PLAYER_LINK = "https://old.yummyani.me/catalog/item/x#alloha=https%3A%2F%2Falloha.test%2F%3Ftoken_movie%3D1"


@pytest.mark.asyncio
async def test_a_file_its_player_takes_that_is_cancelled_stops_the_player(system, tmp_path):
    bus, fake, dl, probe = system
    fake.stream_replies[PLAYER_LINK] = {"handled": True, "stream_url": PLAYER_LINK, "headers": {}, "player_fetch": True}
    fake.player_gate = asyncio.Event()
    path = str(tmp_path / "temp" / "e1.mp4")
    sub = bus.subscribe("downloads.fetch.finished")
    probe.send_command("downloads.fetch", {"key": "video:2", "url": PLAYER_LINK, "path": path, "series_id": 3})
    assert await _wait(lambda: fake.player_fetches)

    assert (await probe.request("downloads.cancel", {"task_id": "fetch:video:2"}, timeout=5)).cancelled
    done = (await asyncio.wait_for(sub.queue.get(), 5)).payload

    assert done["ok"] is False and await _wait(lambda: fake.player_stops == [player_target(path)])


# --- a file a provider of the socket takes whole (contracts/provider_socket.md, section 7) -----------------------------------
