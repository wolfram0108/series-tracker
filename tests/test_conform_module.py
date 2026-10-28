"""Tests of the conform bridge: the daemon's event stream mirrored onto the bus."""
import asyncio
import json

import httpx
import pytest

from core import BaseModule, Bus, BusRequestError, Runner
from modules.conform import ConformModule, DaemonClient, DaemonRefused
from modules.conform.module import compact_job
from modules.conform.paths import translate


def job(job_id, seq=1, status="running", **fields):
    return {"id": job_id, "seq": seq, "label": f"ep {seq}", "status": status, "progress": 0.0,
            "stage": "", "stage_pct": 0.0, "detail": "", "dub_index": 0, "dub_total": 2,
            "cur_dub": "", "elapsed_s": 0.0, "error": None, "created_at": "t0", "updated_at": "t0",
            "trace": [{"big": "x" * 50}], "ops": [{"op": "decode"}], "results": [], **fields}


class FakeClient:
    """A daemon that plays scripted connections: a list of events, or an exception to raise."""

    base_url = "http://daemon.test"

    def __init__(self, *scripts):
        self.scripts = list(scripts)
        self.connections = 0
        self.enqueued = []
        self.refuse = False
        self.known = {}

    async def info(self):
        return {"version": "0.1.0", "device": "cuda", "gpu": True, "gpu_name": "RTX"}

    async def events(self):
        self.connections += 1
        if not self.scripts:
            await asyncio.Event().wait()
        for item in self.scripts.pop(0):
            if isinstance(item, Exception):
                raise item
            await asyncio.sleep(0)
            yield item
        await asyncio.Event().wait()

    async def enqueue(self, body):
        self.enqueued.append(body)
        if self.refuse:
            raise DaemonRefused("демон отклонил задачу (422): bad path")
        return {"id": "j-new", "status": "queued"}

    async def job(self, job_id):
        return self.known.get(job_id)

    async def close(self):
        pass


class Settings(BaseModule):
    """Answers settings.value.get from a dict."""

    name = "settings"

    def __init__(self, bus, values=None):
        self.values = values or {}
        super().__init__(bus)

    def register(self):
        self.handle("settings.value.get", self.on_get)

    async def on_get(self, env):
        return {"key": env.payload["key"], "value": self.values.get(env.payload["key"])}


class Probe(BaseModule):
    name = "probe"


async def started(client, settings=None, **options):
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01, **options),
                          Settings(bus, settings), probe])
    await runner.start()
    return bus, probe, runner


async def until(probe, condition, seconds=3.0):
    """The bridge pushes; the test only waits for the picture it expects."""
    end = asyncio.get_running_loop().time() + seconds
    while True:
        status = await probe.request("conform.status", {}, timeout=2)
        if condition(status):
            return status
        assert asyncio.get_running_loop().time() < end, status
        await asyncio.sleep(0.01)


# --- compact view ---------------------------------------------------------------------


def test_compact_job_keeps_what_the_interface_shows():
    full = job("j1", results=[{
        "dub": "kodik__rus__A__720p.mp4", "ok": True, "audio_resid_ms": 12.5, "audio_coverage": 0.97,
        "audio_cuts": 2, "suspect": False, "warnings": ["drift"], "critical": [], "trace": [1],
        "plots": [{"name": "x"}], "error": None}])
    view = compact_job(full)
    assert "trace" not in view and "ops" not in view
    assert view["results"] == [{
        "dub": "kodik__rus__A__720p.mp4", "ok": True, "skipped": False, "resid_ms": 12.5,
        "coverage": 0.97, "cuts": 2, "suspect": False, "warnings": ["drift"], "critical": [],
        "error": None}]
    assert view["stage"] == "" and view["dub_total"] == 2


# --- mirror of the registry -----------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_changes_and_progress_build_the_picture():
    client = FakeClient([
        ("conform.snapshot", {"items": [job("j1", 1)]}),
        ("conform.changed", {"items": [job("j1", 1, status="done"), job("j2", 2)], "removed": []}),
        ("conform.progress", {"id": "j2", "progress": 0.5, "stage": "band"}),
        ("conform.changed", {"items": [], "removed": ["j1"]}),
    ])
    _, probe, runner = await started(client)
    try:
        status = await until(probe, lambda s: [t["id"] for t in s["tasks"]] == ["j2"]
                             and s["tasks"][0]["stage"] == "band")
        assert status["configured"] is True
        assert status["daemon"] == {"up": True, "error": None, "version": "0.1.0", "device": "cuda",
                                    "gpu": True, "gpu_name": "RTX"}
        assert status["tasks"][0]["progress"] == 0.5
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_every_change_is_pushed_with_the_whole_picture():
    client = FakeClient([("conform.snapshot", {"items": [job("j1")]}),
                         ("conform.progress", {"id": "j1", "progress": 0.25})])
    bus = Bus()
    sub = bus.subscribe("conform.queue.changed")
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01), probe])
    await runner.start()
    try:
        seen = []
        while not (seen and seen[-1]["tasks"] and seen[-1]["tasks"][0]["progress"] == 0.25):
            seen.append((await asyncio.wait_for(sub.queue.get(), 3)).payload)
        assert seen[0]["daemon"]["up"] is True
        assert all("tasks" in picture and "daemon" in picture for picture in seen)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_new_interface_gets_the_picture_without_asking():
    client = FakeClient([("conform.snapshot", {"items": [job("j1")]})])
    bus, probe, runner = await started(client)
    try:
        await until(probe, lambda s: len(s["tasks"]) == 1)
        sub = bus.subscribe("conform.queue.changed")
        probe.publish_event("gateway.sse.clients", {"count": 1})
        picture = (await asyncio.wait_for(sub.queue.get(), 3)).payload
        assert [t["id"] for t in picture["tasks"]] == ["j1"]
        probe.publish_event("gateway.sse.clients", {"count": 0})
        await asyncio.sleep(0.05)
        assert sub.queue.empty()
    finally:
        await runner.stop()


# --- the daemon goes away and comes back ----------------------------------------------


async def pictures_until(bus, runner_start, condition, seconds=3.0):
    """Every picture the bridge pushes, from the start, until one satisfies `condition`."""
    sub = bus.subscribe("conform.queue.changed")
    await runner_start()
    seen = []
    end = asyncio.get_running_loop().time() + seconds
    while not (seen and condition(seen)):
        left = end - asyncio.get_running_loop().time()
        assert left > 0, seen
        seen.append((await asyncio.wait_for(sub.queue.get(), left)).payload)
    return seen


@pytest.mark.asyncio
async def test_unreachable_daemon_is_a_state_and_the_bridge_reconnects():
    client = FakeClient([ConnectionError("refused")],
                        [("conform.snapshot", {"items": [job("j9")]})])
    bus = Bus()
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01), Probe(bus)])
    try:
        seen = await pictures_until(bus, runner.start, lambda s: s[-1]["daemon"]["up"] and s[-1]["tasks"])
        down = [p for p in seen if not p["daemon"]["up"]]
        assert len(down) == 1 and "refused" in down[0]["daemon"]["error"] and down[0]["tasks"] == []
        assert seen[-1]["tasks"][0]["id"] == "j9"
        assert client.connections == 2
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_lost_jobs_are_not_shown_as_current_while_the_daemon_is_down():
    client = FakeClient([("conform.snapshot", {"items": [job("j1")]}), ConnectionError("lost")])
    bus = Bus()
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=60), Probe(bus)])
    try:
        seen = await pictures_until(bus, runner.start, lambda s: not s[-1]["daemon"]["up"])
        assert any(p["tasks"] for p in seen[:-1])
        assert seen[-1]["tasks"] == [] and "lost" in seen[-1]["daemon"]["error"]
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_without_an_address_the_module_is_idle():
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, None), probe])
    await runner.start()
    try:
        status = await probe.request("conform.status", {}, timeout=2)
        assert status == {"configured": False, "daemon": {"up": False, "error": None}, "tasks": []}
    finally:
        await runner.stop()


# --- the HTTP client ------------------------------------------------------------------


def stream_of(frames: str) -> DaemonClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events":
            return httpx.Response(200, content=frames.encode(),
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})
        if request.url.path == "/conform/device":
            return httpx.Response(200, json={"device": "cuda", "name": "RTX 3090", "gpu": True})
        return httpx.Response(404)
    return DaemonClient("http://daemon.test/", httpx.AsyncClient(transport=httpx.MockTransport(handler)))


@pytest.mark.asyncio
async def test_client_yields_only_the_conform_events_and_skips_noise():
    big = json.dumps({"items": [{"x": "y" * 1000}]})
    frames = (f"event: downloads.snapshot\ndata: {big}\n\n"
              ": keep-alive\n\n"
              'event: conform.snapshot\ndata: {"items": []}\n\n'
              "event: conform.changed\ndata: {broken\n\n"
              'event: conform.progress\ndata: {"id": "j1",\ndata: "progress": 0.5}\n\n'
              'event: mux.snapshot\ndata: {"items": []}\n\n')
    client = stream_of(frames)
    got = [item async for item in client.events()]
    assert got == [("conform.snapshot", {"items": []}),
                   ("conform.progress", {"id": "j1", "progress": 0.5})]


@pytest.mark.asyncio
async def test_client_info_joins_health_and_device():
    assert await stream_of("").info() == {"version": "0.1.0", "device": "cuda", "gpu": True,
                                          "gpu_name": "RTX 3090"}


# --- paths, enqueue, how a job ended --------------------------------------------------


def test_paths_follow_the_longest_prefix_on_a_boundary():
    mapping = {"/nas": "/mnt/meganas/media", "/nas/torrents": "/mnt/other"}
    assert translate("/nas/a/b.mp4", mapping) == "/mnt/meganas/media/a/b.mp4"
    assert translate("/nas/torrents/x", mapping) == "/mnt/other/x"
    assert translate("/nas", mapping) == "/mnt/meganas/media"
    for outside in ("/nasty/file", "/home/x"):
        with pytest.raises(ValueError):
            translate(outside, mapping)


SUBMIT = {"ref": "/nas/lib/S/ep1.mp4", "dubs": ["/nas/torrents/series-tracker/7/s01e01/a.mp4"],
          "out_dir": "/nas/torrents/series-tracker/7/s01e01/_aligned",
          "cache_dir": "/nas/torrents/series-tracker/7/s01e01/_cache", "label": "S s01e01"}


@pytest.mark.asyncio
async def test_submit_sends_the_episode_with_daemon_paths_and_returns_the_job():
    client = FakeClient([("conform.snapshot", {"items": []})])
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        reply = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert reply == {"job_id": "j-new"}
        sent = client.enqueued[0]
        assert sent["ref"] == "/mnt/meganas/media/lib/S/ep1.mp4"
        assert sent["dubs"] == ["/mnt/meganas/media/torrents/series-tracker/7/s01e01/a.mp4"]
        assert sent["out_dir"].endswith("/7/s01e01/_aligned") and sent["keep_tmp"] is False
        assert sent["label"] == "S s01e01"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_submit_takes_the_path_map_from_settings():
    client = FakeClient([("conform.snapshot", {"items": []})])
    _, probe, runner = await started(client, {"conform.path_map": '{"/nas": "/share"}'})
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        await probe.request("conform.submit", SUBMIT, timeout=5)
        assert client.enqueued[0]["ref"] == "/share/lib/S/ep1.mp4"
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_submit_fails_when_asking_again_may_help_and_rejects_when_it_cannot():
    client = FakeClient([ConnectionError("down")])
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: not s["daemon"]["up"])
        with pytest.raises(BusRequestError, match="недоступен"):
            await probe.request("conform.submit", SUBMIT, timeout=5)
    finally:
        await runner.stop()
    client = FakeClient([("conform.snapshot", {"items": []})])
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        outside = await probe.request("conform.submit", {**SUBMIT, "ref": "/home/x/a.mp4"}, timeout=5)
        assert "вне общего хранилища" in outside["rejected"] and client.enqueued == []
        client.refuse = True
        refused = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert "отклонил" in refused["rejected"]
    finally:
        await runner.stop()
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, None), Settings(bus), probe])
    await runner.start()
    try:
        with pytest.raises(BusRequestError, match="не настроен"):
            await probe.request("conform.submit", SUBMIT, timeout=5)
    finally:
        await runner.stop()


def ended(job_id, status="done", **fields):
    return job(job_id, status=status, results=[
        {"dub": "a.mp4", "ok": True, "skipped": False,
         "out_path": "/mnt/meganas/media/torrents/series-tracker/7/s01e01/_aligned/a.flac",
         "audio_resid_ms": 11.0, "suspect": False, "warnings": [], "critical": []}], **fields)


@pytest.mark.asyncio
async def test_a_finished_job_is_announced_once_with_local_paths():
    client = FakeClient([("conform.snapshot", {"items": [ended("j1")]}),
                         ("conform.changed", {"items": [ended("j1"), job("j2")], "removed": []}),
                         ("conform.changed", {"items": [ended("j2", status="failed", error="boom")],
                                              "removed": []})])
    bus = Bus()
    sub = bus.subscribe("conform.job.finished")
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01), Settings(bus), probe])
    await runner.start()
    try:
        first = (await asyncio.wait_for(sub.queue.get(), 3)).payload
        second = (await asyncio.wait_for(sub.queue.get(), 3)).payload
        assert (first["job_id"], first["status"]) == ("j1", "done")
        assert first["results"][0]["out_path"] == "/nas/torrents/series-tracker/7/s01e01/_aligned/a.flac"
        assert first["results"][0]["resid_ms"] == 11.0 and first["results"][0]["suspect"] is False
        assert (second["job_id"], second["status"], second["error"]) == ("j2", "failed", "boom")
        await asyncio.sleep(0.1)
        assert sub.queue.empty()
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_job_get_answers_from_the_mirror_then_the_daemon_then_says_gone():
    client = FakeClient([("conform.snapshot", {"items": [ended("j1")]})])
    client.known["j7"] = ended("j7", status="running")
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: len(s["tasks"]) == 1)
        got = await probe.request("conform.job.get", {"job_id": "j1"}, timeout=5)
        assert got["status"] == "done" and got["results"][0]["out_path"].startswith("/nas/")
        assert (await probe.request("conform.job.get", {"job_id": "j7"}, timeout=5))["status"] == "running"
        assert await probe.request("conform.job.get", {"job_id": "nope"}, timeout=5) == {
            "job_id": "nope", "status": "gone", "error": None, "results": []}
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_the_bridge_announces_when_the_daemon_becomes_reachable():
    client = FakeClient([("conform.snapshot", {"items": []})])
    bus = Bus()
    sub = bus.subscribe("conform.daemon.up")
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01), Settings(bus), Probe(bus)])
    await runner.start()
    try:
        await asyncio.wait_for(sub.queue.get(), 3)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_client_enqueue_separates_a_final_refusal_from_a_transient_failure():
    answers = iter([httpx.Response(200, json={"id": "j1", "status": "queued"}),
                    httpx.Response(422, json={"detail": "bad path"}),
                    httpx.Response(503, text="busy")])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return next(answers)

    client = DaemonClient("http://daemon.test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await client.enqueue({"ref": "/r", "dubs": []}))["id"] == "j1"
    assert seen[0] == ("POST", "/conform/enqueue", {"ref": "/r", "dubs": []})
    with pytest.raises(DaemonRefused, match="422"):
        await client.enqueue({"ref": "/r", "dubs": []})
    with pytest.raises(httpx.HTTPStatusError):
        await client.enqueue({"ref": "/r", "dubs": []})


@pytest.mark.asyncio
async def test_client_job_is_none_for_a_job_the_daemon_forgot():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/known"):
            return httpx.Response(200, json={"id": "known", "status": "done"})
        return httpx.Response(404)

    client = DaemonClient("http://daemon.test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await client.job("known"))["status"] == "done"
    assert await client.job("lost") is None
