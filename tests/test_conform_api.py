"""The two routes of the alignment queue: stop or delete a job, and the picture of a plot."""
import base64

import httpx
import pytest

from core import BaseModule, Bus, Runner
from core.reasons import parse, reason
from modules.gateway import GatewayModule

PICTURE = b"\x89PNG\r\n picture"


class Conform(BaseModule):
    """The bridge as far as these routes use it."""

    name = "conform"

    def __init__(self, bus):
        self.cancelled = []
        self.done = True
        self.plot_reply = {"found": True, "data": base64.b64encode(PICTURE).decode("ascii")}
        self.plot_error = None
        super().__init__(bus)

    def register(self):
        self.handle("conform.cancel", self.on_cancel)
        self.handle("conform.plot", self.on_plot)

    async def on_cancel(self, env):
        self.cancelled.append(env.payload["job_id"])
        return {"done": self.done}

    async def on_plot(self, env):
        if self.plot_error:
            raise RuntimeError(self.plot_error)
        return self.plot_reply


@pytest.fixture
async def system(tmp_path):
    bus = Bus()
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False, templates_dir=str(tmp_path))
    conform = Conform(bus)
    runner = Runner(bus, [gateway, conform])
    await runner.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test") as client:
        yield conform, client
    await runner.stop()


@pytest.mark.asyncio
async def test_a_job_the_daemon_could_not_be_asked_to_change_is_a_coded_refusal(system):
    conform, client = system
    conform.done = False
    reply = await client.delete("/api/conform/jobs/cf00004")
    assert reply.status_code == 502 and parse(reply.json()["error"])["code"] == "conform_job_unchanged"


@pytest.mark.asyncio
async def test_a_plot_the_daemon_does_not_hold_is_not_found_and_an_unreachable_daemon_is_a_bad_gateway(system):
    conform, client = system
    conform.plot_reply = {"found": False}
    missing = await client.get("/api/conform/jobs/cf00002/plot/a__track.png")
    assert missing.status_code == 404 and parse(missing.json()["error"])["code"] == "conform_plot_missing"
    conform.plot_error = reason("conform_down", error="refused")
    down = await client.get("/api/conform/jobs/cf00002/plot/a__track.png")
    assert down.status_code == 502 and parse(down.json()["error"])["code"] == "conform_down"
