"""Сквозной тест этапа 1: HTTP → query → модуль → reply → HTTP, и шина → SSE."""
import asyncio
import json

import httpx
import pytest

from core import BaseModule, Bus, Envelope, Runner
from modules.gateway import GatewayModule
from modules.gateway import module as gateway_module


class EchoModule(BaseModule):
    name = "echo"

    def register(self):
        self.handle("echo.ping", self.on_ping)

    async def on_ping(self, env):
        return {"pong": env.payload}


@pytest.fixture
async def system(tmp_path):
    bus = Bus()
    gateway = GatewayModule(bus, static_dir=str(tmp_path),
                            templates_dir=str(tmp_path), diag=True)
    runner = Runner(bus, [gateway, EchoModule(bus)])
    await runner.start()
    transport = httpx.ASGITransport(app=gateway.app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as client:
        yield bus, client, gateway
    await runner.stop()


@pytest.mark.asyncio
async def test_http_query_reply_roundtrip(system):
    _, client, _ = system
    resp = await client.post("/api/_diag/echo",
                             json={"topic": "echo.ping", "payload": {"x": 1}})
    assert resp.status_code == 200
    assert resp.json() == {"reply": {"pong": {"x": 1}}}


@pytest.mark.asyncio
async def test_http_query_no_handler_returns_502(system):
    _, client, _ = system
    resp = await client.post("/api/_diag/echo",
                             json={"topic": "никого.нет", "timeout": 1})
    assert resp.status_code == 502


@pytest.mark.asyncio
async def test_bus_event_reaches_sse_with_mapped_name(system, monkeypatch):
    # httpx.ASGITransport буферизует ответ целиком и не годится для
    # бесконечного SSE — поэтому потребляем генератор потока напрямую;
    # транспортный уровень проверяется смоук-тестом под живым uvicorn.
    bus, _, gateway = system
    monkeypatch.setitem(gateway_module.SSE_MAP,
                        "demo.things.changed", ("things_changed", None))

    stream = gateway._sse_stream()
    received: list[str] = []

    async def consume():
        async for chunk in stream:
            received.append(chunk)
            if chunk.startswith("event:"):
                break

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # дать генератору подписаться на шину
    bus.publish(Envelope(topic="внутренний.топик", kind="event",
                         payload={"секрет": True}))
    bus.publish(Envelope(topic="demo.things.changed", kind="event",
                         payload={"id": 7}))
    await asyncio.wait_for(consumer, timeout=5)
    await stream.aclose()

    text = "".join(received)
    # просочиться должно только событие из SSE_MAP, под старым SSE-именем
    assert text.startswith("event: things_changed\n")
    assert json.loads(text.split("data:", 1)[1].strip()) == {"id": 7}
    assert "секрет" not in text


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

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
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
        Envelope(topic="renaming.finished", kind="event",
                 payload={"series_id": 5}),
    ], expected=4)

    # series_updated — дельта {id, statuses, is_busy}, не полный объект
    assert events[0] == ("series_updated", {
        "id": 5, "statuses": ["downloading"], "is_busy": False})
    assert events[1] == ("series_updated", {
        "id": 5, "statuses": ["waiting"], "is_busy": True})
    # очереди — голый массив задач (контракт фронта this.agentQueue = data)
    assert events[2] == ("agent_queue_update", [{"hash": "abc"}])
    # события без трансформации проходят как есть
    assert events[3] == ("renaming_complete", {"series_id": 5})
