"""Сквозной тест этапа 1: HTTP → query → модуль → reply → HTTP, и шина → SSE."""
import asyncio
import json

import httpx
import pytest

from core.bus import Bus
from core.envelope import Envelope
from core.module import BaseModule
from core.runner import Runner
from modules.gateway import GatewayModule


class EchoModule(BaseModule):
    name = "echo"

    def register(self):
        self.handle("echo.ping", self.on_ping)

    async def on_ping(self, env):
        return {"pong": env.payload}


@pytest.fixture
async def system(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>test</title>")
    bus = Bus()
    gateway = GatewayModule(bus, static_dir=str(tmp_path), auth_required=False,
                            templates_dir=str(tmp_path), web_dist_dir=str(dist), diag=True)
    runner = Runner(bus, [gateway, EchoModule(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, client, gateway
    await runner.stop()


@pytest.mark.asyncio
async def test_http_query_no_handler_returns_502(system):
    _, client, _ = system
    resp = await client.post("/api/_diag/echo",
                             json={"topic": "никого.нет", "timeout": 1})
    assert resp.status_code == 502


async def _collect_sse_events(gateway, bus, envelopes, expected):
    """Прогоняет конверты через живой SSE-генератор, возвращает
    [(имя события, payload)] первых `expected` событий."""
    stream = gateway._sse_stream()
    received: list[str] = []

    async def consume():
        events = 0
        async for chunk in stream:
            received.append(chunk)
            if chunk.startswith("event:"):
                events += 1
                if events >= expected:
                    break

    announced = bus.subscribe("gateway.sse.clients")  # the stream announces itself once it is subscribed
    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(announced.queue.get(), 5)
    bus.unsubscribe(announced)
    for env in envelopes:
        bus.publish(env)
    await asyncio.wait_for(consumer, timeout=5)
    await stream.aclose()

    result = []
    for block in "".join(received).split("\n\n"):
        if not block.startswith("event:"):
            continue
        name_line, data_line = block.split("\n", 1)
        result.append((name_line.split(": ", 1)[1],
                       json.loads(data_line.split("data: ", 1)[1])))
    return result


@pytest.mark.asyncio
async def test_sse_contract_mappings_and_transforms(system):
    """Р-18: формы payload старого SSE-контракта (sse_contract.md)."""
    bus, _, gateway = system
    events = await _collect_sse_events(gateway, bus, [
        Envelope(topic="series.status.changed", kind="event",
                 payload={"series_id": 5, "statuses": ["downloading"],
                          "is_busy": False}),
        Envelope(topic="series.busy.changed", kind="event",
                 payload={"series_id": 5, "is_busy": True,
                          "statuses": ["waiting"]}),
        Envelope(topic="torrents.queue.changed", kind="event",
                 payload={"count": 1, "tasks": [{"hash": "abc"}]}),
        Envelope(topic="library.relocation.finished", kind="event",
                 payload={"series_id": 5, "success": True}),
    ], expected=4)

    # series_updated — дельта {id, statuses, is_busy}, не полный объект
    assert events[0] == ("series_updated", {
        "id": 5, "statuses": ["downloading"], "is_busy": False})
    assert events[1] == ("series_updated", {
        "id": 5, "statuses": ["waiting"], "is_busy": True})
    # очереди — голый массив задач (контракт фронта this.agentQueue = data)
    assert events[2] == ("agent_queue_update", [{"hash": "abc"}])
    # события без трансформации проходят как есть
    assert events[3] == ("relocation_finished", {"series_id": 5, "success": True})
