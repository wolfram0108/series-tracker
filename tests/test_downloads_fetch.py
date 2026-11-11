"""Tests of `downloads.fetch`: the executor of downloads fetches a stream behind a link into a file, under a fuse of silence,
with retries of a transient failure and its own limit of parallel fetches. The stream provider and the settings are fakes;
the downloader is an injected coroutine, so yt-dlp is not needed.
"""
import asyncio
import logging
import os

from types import SimpleNamespace

import pytest

from core import BaseModule, Bus, Runner, storage
from core.db import Database
from modules.downloads import DownloadsModule
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
        super().__init__(bus)

    def register(self):
        self.handle("settings.value.get", self.on_get)
        self.handle("settings.value.set", self.on_set)
        self.handle("sources.video.stream", self.on_stream)

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
                return False, "видео недоступно или приватно"
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
async def test_fetch_downloads_a_stream_without_touching_the_media_tables(system, tmp_path):
    bus, fake, dl, probe = system
    fake.stream_replies["https://site/#cvh=1"] = {
        "handled": True, "stream_url": "https://cdn/1.mp4", "headers": {"User-Agent": "A/1"}}
    path = str(tmp_path / "temp" / "s01e01" / "voice.mp4")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:1", "url": "https://site/#cvh=1", "path": path, "max_height": 720, "series_id": 3}])
    assert done["ok"] is True and done["key"] == "dub:1" and done["path"] == path
    assert os.path.isfile(path) and dl.started == ["https://cdn/1.mp4"]
    assert fake.stream_payloads == [{"url": "https://site/#cvh=1", "max_height": 720}]


@pytest.mark.asyncio
async def test_fetch_of_a_page_no_player_resolves_keeps_its_cap_for_the_downloader(system, tmp_path):
    """A page the downloader reads itself is fetched under the cap of the job, as a player picks its file;
    a job without a cap asks for no cap."""
    bus, _, dl, probe = system
    capped, free = "https://vk.com/video-1_2", "https://vk.com/video-1_3"
    await _fetch(probe, bus, 2, jobs=[
        {"key": "dub:1", "url": capped, "path": str(tmp_path / "a.mp4"), "max_height": 720, "series_id": 3},
        {"key": "dub:2", "url": free, "path": str(tmp_path / "b.mp4"), "series_id": 3}])
    assert dl.kwargs[capped].get("max_height") == 720 and "max_height" not in dl.kwargs[free]


@pytest.mark.asyncio
async def test_fetch_failure_is_reported_and_leaves_no_partial_file(system, tmp_path):
    bus, fake, dl, probe = system
    path = str(tmp_path / "t" / "x.mp4")
    dl.fail_urls.add("https://site/#kodik=1")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:9", "url": "https://site/#kodik=1", "path": path, "series_id": 3}])
    assert done["ok"] is False and "приватно" in done["error"] and done["transient"] is False
    assert not os.path.exists(path)


@pytest.mark.asyncio
async def test_fetch_stream_error_of_the_provider_is_the_error_of_the_job(system, tmp_path):
    bus, fake, _, probe = system
    fake.stream_replies["https://site/#cvh=2"] = {"handled": True, "error": "VK: видео недоступно",
                                                  "transient": False}
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:2", "url": "https://site/#cvh=2", "path": str(tmp_path / "a.mp4"), "series_id": 3}])
    assert (done["ok"], done["error"], done["transient"]) == (False, "VK: видео недоступно", False)


@pytest.mark.asyncio
async def test_fetch_unreachable_sources_is_an_error_not_a_blind_download(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, fake, dl, probe = system
    fake.stream_raises.add("https://site/#cvh=3")
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:3", "url": "https://site/#cvh=3", "path": str(tmp_path / "b.mp4"), "series_id": 3}])
    assert done["ok"] is False and "поток не получен" in done["error"] and dl.started == []
    # Our own module did not answer: nothing was learnt of the source, and its owner asks it again at the next round.
    assert done["transient"] is True


@pytest.mark.asyncio
async def test_fetch_retries_transient_errors_then_succeeds(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, _, dl, probe = system
    dl.timeouts["https://site/#cvh=4"] = 2
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:4", "url": "https://site/#cvh=4", "path": str(tmp_path / "c.mp4"), "series_id": 3}])
    assert done["ok"] is True and dl.started.count("https://site/#cvh=4") == 3


@pytest.mark.asyncio
async def test_fetch_gives_up_after_the_attempt_limit(system, tmp_path, monkeypatch):
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, _, dl, probe = system
    dl.timeouts["https://site/#cvh=5"] = 99
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:5", "url": "https://site/#cvh=5", "path": str(tmp_path / "d.mp4"), "series_id": 3}])
    # The attempts spent, the failure is the source's: the owner of the fetch goes to the next one.
    assert done["ok"] is False and done["transient"] is False
    assert dl.started.count("https://site/#cvh=5") == 3 and not os.path.exists(tmp_path / "d.mp4")


@pytest.mark.asyncio
async def test_a_failure_nobody_recognises_is_asked_again(system, tmp_path, monkeypatch):
    """The words of a downloader for a segment a host cut in its middle name no network and no status: a failure that is
    not known to be the last word of the source is of the way to it."""
    monkeypatch.setattr("modules.downloads.module.RETRY_DELAY_S", 0.0)
    bus, _, dl, probe = system
    url = "https://site/#cvh=6"
    dl.failures[url] = ["загрузчик сегментов: ERROR: [download] Got error: 7254016 bytes read, 9647799 more expected. "
                        "Giving up after 3 retries"]
    (done,) = await _fetch(probe, bus, 1, jobs=[{"key": "dub:6", "url": url, "path": str(tmp_path / "e.mp4"), "series_id": 3}])
    assert done["ok"] is True and dl.started.count(url) == 2


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
async def test_the_progress_of_a_fetch_does_not_wait_for_the_window_to_be_told(system, tmp_path):
    bus, _, dl, probe = system

    def told_nothing() -> None:
        """The window is not told at all: the module calls its telling synchronously and must not wait for it."""

    _running.downloads._broadcast_queue = told_nothing
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:8", "url": "https://site/#cvh=8", "path": str(tmp_path / "p.mp4"), "series_id": 3}])
    assert done["ok"] is True and os.path.isfile(tmp_path / "p.mp4")


@pytest.mark.asyncio
async def test_a_fetch_gives_its_place_back_though_what_it_wrote_cannot_be_removed(system, tmp_path):
    bus, _, dl, probe = system
    path = str(tmp_path / "busy" / "x.mp4")
    dl.fail_urls.add("https://site/#kodik=9")
    with storage.hold(path):  # another operation owns the name: the removal is refused
        (done,) = await _fetch(probe, bus, 1, jobs=[
            {"key": "dub:9", "url": "https://site/#kodik=9", "path": path, "series_id": 3}])
    assert done["ok"] is False and _running.downloads._fetch_active == {} and _running.downloads._fetch_jobs == {}


@pytest.mark.asyncio
async def test_fetch_has_its_own_limit_and_ignores_repeated_keys(system, tmp_path):
    bus, fake, dl, probe = system
    dl.gate = asyncio.Event()
    jobs = [{"key": f"dub:{i}", "url": f"https://site/#cvh={i}", "path": str(tmp_path / f"{i}.mp4"),
             "series_id": 3} for i in range(4)]
    jobs.append(dict(jobs[0]))
    sub = bus.subscribe("downloads.fetch.finished")
    for job in jobs:
        probe.send_command("downloads.fetch", job)
    assert await _wait(lambda: dl.concurrent == 2)
    await _running.bus.settled()  # every job is taken apart: two run, the rest wait
    assert dl.max_concurrent == 2 and dl.concurrent == 2 and len(dl.started) == 2
    dl.gate.set()
    got = [(await asyncio.wait_for(sub.queue.get(), 5)).payload["key"] for _ in range(4)]
    assert sorted(got) == [f"dub:{i}" for i in range(4)]
    await _quiet()
    assert sub.queue.empty() and len(dl.started) == 4


@pytest.mark.asyncio
async def test_the_fetches_of_voice_overs_are_in_the_queue_of_the_window_while_they_wait_and_run(system, tmp_path):
    """A card to a fetch: the one under way at its stage with its figures, the one that waits with its place in its line;
    a finished card stays while the sync of its series runs."""
    bus, fake, dl, probe = system
    dl.gate = asyncio.Event()
    changed = bus.subscribe("downloads.queue.changed")
    for i in range(3):
        probe.send_command("downloads.fetch", {
            "key": f"dub:{i}", "url": f"https://site/#cvh={i}", "path": str(tmp_path / f"{i}.mp4"), "series_id": 3})
    assert await _wait(lambda: dl.concurrent == 2)
    await _running.bus.settled()
    queue = await probe.request("downloads.queue.get", {}, timeout=5)
    assert queue["count"] == 3
    cards = {each["id"]: each for each in queue["tasks"]}
    assert {key: card["status"] for key, card in cards.items()} == {"dub:0": "download", "dub:1": "download", "dub:2": "queued"}
    assert [stage["state"] for stage in cards["dub:0"]["stages"]] == ["done", "done", "active", "pending", "pending"]
    figures = cards["dub:0"]["figures"]
    assert (figures["progress"], figures["speed"], figures["eta"], figures["total_mb"], figures["done_mb"]) == (50, 3.0, 5, 10.0, 5.0)
    assert (cards["dub:2"]["position"], cards["dub:2"]["running"], cards["dub:2"]["limit"]) == (1, 2, 2)
    assert [(line["kind"], line["attempt"], line["of"]) for line in cards["dub:0"]["log"]] == [("attempt", 1, 3)]
    # The window is told by an event published after it reads the queue.
    assert (await asyncio.wait_for(changed.queue.get(), 5)).payload["count"] >= 1
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "2.mp4"))
    await _quiet()
    queue = await probe.request("downloads.queue.get", {}, timeout=5)
    assert queue["count"] == 0 and {each["status"] for each in queue["tasks"]} == {"done"}


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
    cards = {each["id"]: each for each in (await probe.request("downloads.queue.get", {}, timeout=5))["tasks"]}
    kazoku = cards["need:kazoku"]
    assert [(line["kind"], line["variant"]["player"] if line["variant"] else None, line["outcome"])
            for line in kazoku["log"]] == [("attempt", "cvh", "failed"), ("replan", "kodik", "running"),
                                           ("attempt", "kodik", "done")]
    assert kazoku["status"] == "done" and kazoku["replanned"] and kazoku["need"]["voice_number"] == 15

    probe.publish_event("scan.run.changed", {"series_id": 88, "active": False, "seq": 2})
    await _quiet()
    left = {each["id"]: each for each in (await probe.request("downloads.queue.get", {}, timeout=5))["tasks"]}
    assert set(left) == {"need:kazoku", "need:lost"} and left["need:lost"]["log"][-1]["kind"] == "exhausted"
    assert {card.id for card in await _running.downloads.repo.cards()} == {"need:kazoku", "need:lost"}

    assert (await probe.request("downloads.card.remove", {"id": "need:lost"}, timeout=5))["removed"]
    probe.publish_event("scan.run.changed", {"series_id": 88, "active": True, "seq": 3})
    await _quiet()
    assert (await probe.request("downloads.queue.get", {}, timeout=5))["tasks"] == []
    assert await _running.downloads.repo.cards() == []


@pytest.mark.asyncio
async def test_fetch_cancel_drops_the_queue_and_active_jobs_of_a_series(system, tmp_path):
    bus, fake, dl, probe = system
    dl.gate = asyncio.Event()
    for i in range(4):
        probe.send_command("downloads.fetch", {
            "key": f"dub:{i}", "url": f"https://site/#cvh={i}",
            "path": str(tmp_path / f"{i}.mp4"), "series_id": 3 if i < 3 else 4})
    assert await _wait(lambda: dl.started == ["https://site/#cvh=0", "https://site/#cvh=1"])
    probe.publish_event("work.withdrawn", {"series_id": 3})
    # The slots of the withdrawn jobs go to the next series' job; the queued job of series 3 never runs.
    assert await _wait(lambda: "https://site/#cvh=3" in dl.started)
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "3.mp4"))
    assert "https://site/#cvh=2" not in dl.started
    assert not any(os.path.exists(tmp_path / f"{i}.mp4") for i in (0, 1, 2))


@pytest.mark.asyncio
async def test_fetch_cancel_by_key_drops_only_that_job(system, tmp_path):
    bus, fake, dl, probe = system
    dl.gate = asyncio.Event()
    finished = bus.subscribe("downloads.fetch.finished")
    for i in range(3):
        probe.send_command("downloads.fetch", {
            "key": f"file:{i}", "url": f"https://site/#cvh={i}",
            "path": str(tmp_path / f"{i}.mp4"), "series_id": 3})
    assert await _wait(lambda: dl.started == ["https://site/#cvh=0", "https://site/#cvh=1"])
    assert await probe.request("downloads.cancel", {"task_id": "fetch:file:0"}, timeout=5) == {"cancelled": True}
    # The owner of the cancelled fetch hears it end, failed.
    stopped = (await asyncio.wait_for(finished.queue.get(), 5)).payload
    assert (stopped["key"], stopped["ok"], stopped["error"]) == ("file:0", False, "отменено пользователем")
    # The slot of the cancelled job goes to the queued one; the other running job is untouched.
    assert await _wait(lambda: "https://site/#cvh=2" in dl.started)
    dl.gate.set()
    assert await _wait(lambda: os.path.isfile(tmp_path / "1.mp4") and os.path.isfile(tmp_path / "2.mp4"))
    assert not os.path.exists(tmp_path / "0.mp4")


@pytest.mark.asyncio
async def test_fetch_rejects_a_relative_path(system):
    bus, _, dl, probe = system
    (done,) = await _fetch(probe, bus, 1, jobs=[
        {"key": "dub:7", "url": "https://site/#cvh=7", "path": "rel/x.mp4", "series_id": 3}])
    assert done["ok"] is False and "абсолютным" in done["error"] and dl.started == []


@pytest.mark.asyncio
async def test_how_many_go_at_once_is_by_the_way_the_file_comes(system, tmp_path):
    """Downloads by many connections share the threads of all of them (32 by 16 a file: two at once); downloads by one
    connection have places of their own. A full way holds neither the queue nor the other way, and a fetch that waited
    for its place asks its stream anew."""
    bus, fake, dl, probe = system
    assert await probe.request("downloads.limits.get", {}) == {
        "threads_total": 32, "threads_per_file": 16, "single_at_once": 4, "multi_at_once": 2}
    said = await probe.request("downloads.limits.set", {"single_at_once": "1"})
    assert said["ok"] and said["limits"]["single_at_once"] == 1
    refused = await probe.request("downloads.limits.set", {"threads_total": "0"})
    assert not refused["ok"] and "download_limit_invalid" in refused["reason"]
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


@pytest.mark.asyncio
async def test_a_fetch_is_announced_when_it_leaves_the_queue_and_transfers_in_its_turn(system, tmp_path):
    """A fetch is announced when it leaves the queue; with one place of its way, the second transfers only once the first
    has, in the order they were asked."""
    bus, fake, dl, probe = system
    fake.stream_replies["https://site/#cvh=1"] = {"handled": True, "stream_url": "https://cdn/1.mp4", "headers": {}}
    fake.stream_replies["https://site/#cvh=2"] = {"handled": True, "stream_url": "https://cdn/2.mp4", "headers": {}}
    started = bus.subscribe("downloads.fetch.started")
    # One fetch at a time: the setting is read by the module when it changes.
    probe.publish_event("settings.changed", {"key": "download_single_at_once", "value": "1"})
    await _quiet()
    dl.gate = asyncio.Event()
    probe.send_command("downloads.fetch", {"key": "dub:1", "url": "https://site/#cvh=1",
                                           "path": str(tmp_path / "t" / "1.mp4"), "series_id": 3})
    probe.send_command("downloads.fetch", {"key": "dub:2", "url": "https://site/#cvh=2",
                                           "path": str(tmp_path / "t" / "2.mp4"), "series_id": 3})
    first = (await asyncio.wait_for(started.queue.get(), 3)).payload
    assert first == {"key": "dub:1", "series_id": 3, "path": str(tmp_path / "t" / "1.mp4")}
    assert await _wait(lambda: dl.concurrent == 1)
    await _running.bus.settled()
    assert dl.started == ["https://cdn/1.mp4"], "the second waits for the place of its way"
    dl.gate.set()
    assert await _wait(lambda: len(dl.started) == 2 and dl.concurrent == 0)
    assert dl.started == ["https://cdn/1.mp4", "https://cdn/2.mp4"] and dl.max_concurrent == 1


@pytest.mark.asyncio
async def test_a_download_cancelled_in_the_middle_of_its_watch_leaves_no_task_behind(db_path):
    """The watch of silence waits on a beat of the download beside it: a cancel of the download in the middle of that
    wait takes the beat away too, or it would hang until the garbage collector finds it."""
    held = asyncio.Event()
    entered = asyncio.Event()

    async def blocked(url, path, on_progress, **kw):
        entered.set()
        await held.wait()
        return True, ""

    module = DownloadsModule(Bus(), Database(db_path), downloader=blocked)
    watch = asyncio.ensure_future(module._watched("k", "https://x", "/tmp/x.mp4", None))
    await entered.wait()
    watch.cancel()
    await asyncio.gather(watch, return_exceptions=True)
    left = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
    assert left == []
