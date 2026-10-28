"""Tests of the conform bridge: the daemon's event stream mirrored onto the bus."""
import asyncio
import json

import httpx
import pytest

from core import BaseModule, Bus, Runner
from modules.conform import ConformModule, DaemonClient
from modules.conform.module import compact_job


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

    async def close(self):
        pass


class Probe(BaseModule):
    name = "probe"


async def started(client, **options):
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [ConformModule(bus, client, backoff_start=0.01, **options), probe])
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
