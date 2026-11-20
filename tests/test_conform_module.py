"""Tests of the conform bridge: the daemon's event stream mirrored onto the bus."""
import asyncio
import base64
import json

import httpx
import pytest

from core.bus import Bus
from core.module import BaseModule, BusRequestError
from core.runner import Runner
from core.envelope import Envelope
from modules.conform import ConformModule, DaemonClient, DaemonRefused, NotDelivered, Unconfirmed
from conform_fakes import FakeClient
from core.reasons import parse
from settle import wait_until


def job(job_id, seq=1, status="running", **fields):
    return {"id": job_id, "seq": seq, "label": f"ep {seq}", "status": status, "progress": 0.0,
            "stage": "", "stage_pct": 0.0, "detail": "", "dub_index": 0, "dub_total": 2,
            "cur_dub": "", "elapsed_s": 0.0, "error": None, "created_at": "t0", "updated_at": "t0",
            "trace": [{"big": "x" * 50}], "ops": [{"op": "decode"}], "results": [], **fields}


# The code names no folder: the settings the tests stand in for carry the storage as both servers see it.
PATH_MAP = {"conform.work_dir": "/nas", "conform.work_dir_remote": "/mnt/meganas/media"}


class Settings(BaseModule):
    """Answers settings.value.get and settings.values.by_prefix from a dict."""

    name = "settings"

    def __init__(self, bus, values=None):
        self.values = dict(PATH_MAP) if values is None else values
        super().__init__(bus)

    def register(self):
        self.handle("settings.value.get", self.on_get)
        self.handle("settings.values.by_prefix", self.on_prefix)

    async def on_prefix(self, env):
        return {"values": {key: value for key, value in self.values.items() if key.startswith(env.payload["prefix"])}}

    async def on_get(self, env):
        return {"key": env.payload["key"], "value": self.values.get(env.payload["key"])}


class Probe(BaseModule):
    name = "probe"


async def started(client, settings=None, **options):
    bus = Bus()
    probe = Probe(bus)
    probe.bridge = ConformModule(bus, client, backoff_start=0.01, **options)
    runner = Runner(bus, [probe.bridge, Settings(bus, settings), probe])
    await runner.start()
    return bus, probe, runner


async def until(probe, condition, seconds=3.0):
    """The bridge pushes; the test only waits for the picture it expects."""
    # The bus carries the picture only as a push; the test reads the bridge's copy after every step.
    assert await wait_until(lambda: condition(probe.bridge.snapshot()), probe.bus, probe.bridge, seconds=seconds), \
        probe.bridge.snapshot()
    return probe.bridge.snapshot()




# The records of a trace as the service writes them: a gate that decided, and the exception of a pair that crashed.
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
        assert status["daemon"] == {"up": True, "error": None, "version": "0.1.0", "device": "cuda",
                                    "gpu": True, "gpu_name": "RTX"}
        assert status["tasks"][0]["progress"] == 0.5
    finally:
        await runner.stop()


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


def stream_of(frames: str) -> DaemonClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/events":
            return httpx.Response(200, content=frames.encode(),
                                  headers={"content-type": "text/event-stream"})
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "version": "0.1.0"})
        if request.url.path == "/conform/device":
            return httpx.Response(200, json={"device": "cuda", "name": "RTX 3090", "gpu": True})
        if request.url.path == "/conform/settings":
            return httpx.Response(200, json={"limit": 4, "active": 0, "params": {"drift_speed_pct": {
                "min": 0.25, "max": 10.0, "default": 1.25}, "audio_method": {"values": ["band", "muq"], "default": "band"}}})
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


SUBMIT = {"ref": "/nas/lib/S/ep1.mp4", "dubs": ["/nas/torrents/series-tracker/7/s01e01/a.mp4"],
          "out_dir": "/nas/torrents/series-tracker/7/s01e01/_aligned",
          "cache_dir": "/nas/torrents/series-tracker/7/s01e01/_cache", "label": "S s01e01"}


@pytest.mark.asyncio
async def test_submit_fails_when_asking_again_may_help_and_rejects_when_it_cannot():
    class Unreachable(FakeClient):
        async def info(self):
            raise ConnectionError("down")

    client = Unreachable()
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: not s["daemon"]["up"] and s["daemon"]["error"])  # looked at, and not there
        down = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert down["outcome"] == "unavailable" and parse(down["reason"])["code"] == "conform_down"
        assert down["retry"] is False
    finally:
        await runner.stop()
    client = FakeClient([("conform.snapshot", {"items": []})])
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        outside = await probe.request("conform.submit", {**SUBMIT, "ref": "/home/x/a.mp4"}, timeout=5)
        assert outside["outcome"] == "rejected" and parse(outside["reason"])["code"] == "path_outside_share"
        assert client.enqueued == []
        client.refuse = True
        refused = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert refused["outcome"] == "rejected" and parse(refused["reason"])["code"] == "daemon_refused"
        client.refuse = NotDelivered("ConnectError: refused")
        not_sent = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert not_sent["outcome"] == "unavailable" and not_sent["retry"] is True
        assert parse(not_sent["reason"])["code"] == "conform_unreachable"
        client.refuse = Unconfirmed("ReadTimeout: no answer")
        unknown = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert unknown["outcome"] == "uncertain" and parse(unknown["reason"])["code"] == "conform_no_answer"
    finally:
        await runner.stop()
    bus = Bus()
    probe = Probe(bus)
    runner = Runner(bus, [Settings(bus), ConformModule(bus, None), probe])
    await runner.start()
    try:
        idle = await probe.request("conform.submit", SUBMIT, timeout=5)
        assert idle["outcome"] == "unavailable" and parse(idle["reason"])["code"] == "conform_not_configured"
        assert idle["retry"] is False
    finally:
        await runner.stop()


def ended(job_id, status="done", **fields):
    return job(job_id, status=status, results=[
        {"dub": "a.mp4", "ok": True, "skipped": False,
         "out_path": "/mnt/meganas/media/torrents/series-tracker/7/s01e01/_aligned/a.flac",
         "audio_resid_ms": 11.0, "suspect": False, "warnings": [], "critical": []}], **fields)


@pytest.mark.asyncio
async def test_client_enqueue_separates_a_final_refusal_from_a_failure_that_proves_nothing():
    answers = iter([httpx.Response(200, json={"id": "j1", "status": "queued"}),
                    httpx.Response(422, json={"detail": "bad path"}),
                    httpx.Response(503, text="busy")])
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/openapi.json":
            return httpx.Response(404)  # a daemon that publishes no schema: the body goes as it is
        seen.append((request.method, request.url.path, json.loads(request.content)))
        return next(answers)

    client = DaemonClient("http://daemon.test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert (await client.enqueue({"ref": "/r", "dubs": []}))["id"] == "j1"
    assert seen[0] == ("POST", "/conform/enqueue", {"ref": "/r", "dubs": []})
    with pytest.raises(DaemonRefused, match="422"):
        await client.enqueue({"ref": "/r", "dubs": []})
    with pytest.raises(Unconfirmed, match="503"):
        await client.enqueue({"ref": "/r", "dubs": []})


@pytest.mark.asyncio
async def test_a_cancel_the_daemon_answered_with_a_failure_is_judged_by_what_the_daemon_holds_after_it():
    """The daemon dropped the record of an ended job and then answered 500: the job is gone, so it is done; a failure that
    left the job as it was is not."""
    client = FakeClient([("conform.snapshot", {"items": []})])
    client.known["j1"] = job("j1")
    client.known["j2"] = job("j2")
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        client.drop_then_fail = True
        assert await probe.request("conform.cancel", {"job_id": "j1"}, timeout=5) == {"done": True}
        client.drop_then_fail = False
        client.refuse = httpx.ConnectError("lost")
        assert await probe.request("conform.cancel", {"job_id": "j2"}, timeout=5) == {"done": False}
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_failure_to_read_the_list_of_jobs_is_no_answer_not_no_job():
    for body in ("{broken", '{"not": "a list"}', '[{"label": "unreadable job"}]'):
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request, body=body: httpx.Response(200, text=body)))
        bridge = ConformModule(Bus(), DaemonClient("http://daemon", http))
        bridge._daemon["up"] = True
        try:
            assert await bridge.on_job_find(Envelope("conform.job.find", "query", {"key": "k1"})) == {
                "reachable": False, "job_id": None}
        finally:
            await http.aclose()


@pytest.mark.asyncio
async def test_only_the_not_found_of_the_contract_says_that_a_job_is_gone():
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(404)))
    bridge = ConformModule(Bus(), DaemonClient("http://daemon", http))
    bridge._daemon["up"] = True
    try:
        reply = await bridge.on_job_get(Envelope("conform.job.get", "query", {"job_id": "lost"}))
        assert reply["status"] == "gone"
    finally:
        await http.aclose()


@pytest.mark.asyncio
async def test_a_plot_is_fetched_by_the_name_of_a_png_file_of_its_job_and_by_nothing_else():
    """A plot is named after the file of its voice-over, which names it for people (spaces, Cyrillic, a dash); a name that
    leaves the folder of the job or is no picture is not asked."""
    client = FakeClient([("conform.snapshot", {"items": []})])
    client.plots[("j1", "a__track.png")] = b"\x89PNG picture"
    human = "JoJo s06e08 — озвучка AniLiberty__track.png"
    client.plots[("j1", human)] = b"\x89PNG human"
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda status: status["daemon"]["up"])
        got = await probe.request("conform.plot", {"job_id": "j1", "name": "a__track.png"}, timeout=5)
        assert got["found"] is True and base64.b64decode(got["data"]) == b"\x89PNG picture"
        got = await probe.request("conform.plot", {"job_id": "j1", "name": human}, timeout=5)
        assert got["found"] is True and base64.b64decode(got["data"]) == b"\x89PNG human"
        for name in ("../secret.png", "a__track.html", "a/b.png", "a\\b.png", "x.png.txt", "a\nb.png", ""):
            assert await probe.request("conform.plot", {"job_id": "j1", "name": name}, timeout=5) == {"found": False}
        assert await probe.request("conform.plot", {"job_id": "../j1", "name": "a__track.png"}, timeout=5) == {"found": False}
        assert await probe.request("conform.plot", {"job_id": "j1", "name": "other.png"}, timeout=5) == {"found": False}
        assert client.asked == [("j1", "a__track.png"), ("j1", human), ("j1", "other.png")]  # only valid names reach the daemon
        client.refuse = httpx.ConnectError("no route")
        with pytest.raises(BusRequestError):
            await probe.request("conform.plot", {"job_id": "j1", "name": "a__track.png"}, timeout=5)
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_client_plot_returns_the_picture_or_none_for_a_plot_the_daemon_does_not_hold():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path.endswith("/a__track.png"):
            return httpx.Response(200, content=b"png")
        return httpx.Response(404, json={"detail": "no such plot"})

    client = DaemonClient("http://daemon.test", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    assert await client.plot("j1", "a__track.png") == b"png"
    assert await client.plot("j1", "b__track.png") is None
    assert seen == ["/conform/plot/j1/a__track.png", "/conform/plot/j1/b__track.png"]


REVERSE = {"compilation": "/nas/torrents/series-tracker/_cuts/7/c1/comp.mp4",
           "episodes": [{"path": "/nas/torrents/series-tracker/_cuts/7/c1/S s01e02 #k2/ep2.mkv", "atrack": 1},
                        {"path": "/nas/torrents/series-tracker/_cuts/7/c1/S s01e03 #k3/ep3.mkv"}],
           "out_dir": "/nas/torrents/series-tracker/_cuts/7/c1/_out", "label": "S 1–13"}


@pytest.mark.asyncio
async def test_an_episode_folder_without_a_key_is_refused_before_anything_is_sent():
    client = FakeClient([("conform.snapshot", {"items": []})])
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        bad = {**REVERSE, "episodes": [{"path": "/nas/torrents/series-tracker/_cuts/7/c1/plain/ep2.mkv"}]}
        reply = await probe.request("conform.reverse", bad, timeout=5)
        assert reply["outcome"] == "rejected" and parse(reply["reason"])["code"] == "conform_reverse_no_key"
        assert client.reversed == []
    finally:
        await runner.stop()


@pytest.mark.asyncio
async def test_a_batch_whose_answer_is_lost_is_uncertain_and_its_jobs_are_known_to_the_daemon():
    client = FakeClient([("conform.snapshot", {"items": []})])
    client.lose_answers = 1
    _, probe, runner = await started(client)
    try:
        await until(probe, lambda s: s["daemon"]["up"])
        reply = await probe.request("conform.reverse", REVERSE, timeout=5)
        assert reply["outcome"] == "uncertain"
        found = await probe.request("conform.job.find", {"key": "k3"}, timeout=5)
        assert found["reachable"] and found["job_id"]
    finally:
        await runner.stop()
