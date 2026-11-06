"""Сквозной тест этапа 1: HTTP → query → модуль → reply → HTTP, и шина → SSE."""
import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest

from core import BaseModule, Bus, Envelope, Runner
from core import logging as core_logging
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

    announced = bus.subscribe("gateway.sse.clients")  # the stream announces itself once it is subscribed
    consumer = asyncio.create_task(consume())
    await asyncio.wait_for(announced.queue.get(), 5)
    bus.unsubscribe(announced)
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


@pytest.mark.asyncio
async def test_a_burst_goes_as_one_frame_of_its_events_in_order_when_the_stream_is_read_by_turns(system, monkeypatch):
    # A burst published at once is one turn: the interface takes it in one task and counts its screen once.
    bus, _, gateway = system
    monkeypatch.setitem(gateway_module.SSE_MAP, "demo.things.changed", ("things_changed", None))
    monkeypatch.setitem(gateway_module.SSE_MAP, "demo.other.changed", ("other_changed", None))

    stream = gateway._sse_stream("batch")
    announced = bus.subscribe("gateway.sse.clients")
    first = asyncio.ensure_future(stream.__anext__())
    await asyncio.wait_for(announced.queue.get(), 5)
    bus.unsubscribe(announced)
    for each in range(200):
        bus.publish(Envelope(topic="demo.things.changed", kind="event", payload={"id": each}))
    bus.publish(Envelope(topic="внутренний.топик", kind="event", payload={"секрет": True}))
    bus.publish(Envelope(topic="demo.other.changed", kind="event", payload={"id": "last"}))
    frame = await asyncio.wait_for(first, 5)
    await stream.aclose()

    name, data = frame.split("\n", 1)
    assert name == "event: batch" and frame.endswith("\n\n")
    turn = json.loads(data.removeprefix("data: "))
    assert turn == [{"event": "things_changed", "data": {"id": each}} for each in range(200)] + [
        {"event": "other_changed", "data": {"id": "last"}}]


@pytest.mark.asyncio
async def test_a_stream_read_by_turns_ends_its_subscription_when_closed(system, monkeypatch):
    # Starlette closes the stream at a frame when the client goes: the turns under it must let the bus go too.
    bus, _, gateway = system
    monkeypatch.setitem(gateway_module.SSE_MAP, "demo.things.changed", ("things_changed", None))
    stream = gateway._sse_stream("batch")
    announced = bus.subscribe("gateway.sse.clients")
    first = asyncio.ensure_future(stream.__anext__())
    assert (await asyncio.wait_for(announced.queue.get(), 5)).payload == {"count": 1}
    bus.publish(Envelope(topic="demo.things.changed", kind="event", payload={"id": 1}))
    await asyncio.wait_for(first, 5)
    await stream.aclose()
    assert (await asyncio.wait_for(announced.queue.get(), 5)).payload == {"count": 0}
    assert gateway._sse_subs == {}


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


@pytest.mark.asyncio
async def test_security_headers_present(system):
    """Этап 4: защитные заголовки на ответах."""
    _, client, _ = system
    resp = await client.get("/")
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["referrer-policy"] == "same-origin"


@pytest.mark.asyncio
async def test_docs_disabled_by_default(system):
    """Этап 4: /docs и /openapi.json выключены (docs_enabled=False)."""
    _, client, _ = system
    assert (await client.get("/docs")).status_code == 404
    assert (await client.get("/openapi.json")).status_code == 404


@pytest.mark.asyncio
async def test_voice_dub_events_reach_the_interface_whole(system):
    """Этап 8б: пакет озвучки самодостаточен, интерфейс ничего не доспрашивает."""
    bus, _, gateway = system
    payload = {"series_id": 2, "season": 1, "episode": 3, "voice_key": "blackcat",
               "voice_label": "BlackCat", "status": "rejected", "state": "error",
               "detail": "критично: нет общего звука", "epoch": 3, "rev": 1}
    events = await _collect_sse_events(gateway, bus, [
        Envelope(topic="voices.dub.changed", kind="event", payload=payload)], 1)
    assert events == [("voice_dub_update", payload)]


@pytest.mark.asyncio
async def test_voice_dubs_snapshot_reaches_the_interface_whole(system):
    bus, _, gateway = system
    item = {"series_id": 2, "season": 1, "episode": 3, "voice_key": "blackcat", "voice_label": "BlackCat",
            "status": "placed", "state": "done", "detail": None}
    payload = {"epoch": 3, "rev": 2, "items": [item]}
    events = await _collect_sse_events(gateway, bus, [
        Envelope(topic="voices.dubs.snapshot", kind="event", payload=payload)], 1)
    assert events == [("voice_dubs_snapshot", payload)]


NEW_PACKETS = [
    ("sources.table.progress", "sources_table_progress",
     {"url": "https://x.test/p", "phase": "pages", "current": 3, "total": 10}),
    ("sources.table.ready", "sources_table_ready", {"url": "https://x.test/p", "read_at": "2026-11-01T10:00:00"}),
    ("sources.table.failed", "sources_table_failed", {"url": "https://x.test/p", "reason": "{\"code\":\"x\"}"}),
    ("composition.changed", "composition_changed",
     {"series_id": 1, "rev": 4, "episodes": [{"season": 1, "episode": 2}], "summary_only": True}),
    ("composition.failed", "composition_failed", {"series_id": 1, "reason": "{\"code\":\"composition_error\"}"}),
    ("scan.reference.changed", "reference_changed",
     {"series_id": 1, "season": 1, "episode": 2, "part": None, "mark": "other_player", "source_id": 4,
      "row_key": "r1", "player": "kodik", "voice_group": "Group", "height": 720,
      "reason": "{\"code\":\"reference_other_player\"}"}),
]


@pytest.mark.asyncio
async def test_the_events_of_the_tables_the_layout_and_the_references_reach_the_interface_whole(system):
    """Stage 10: each packet is whole for its part of the screen, `summary_only` and all, so nothing is asked again."""
    bus, _, gateway = system
    events = await _collect_sse_events(gateway, bus, [
        Envelope(topic=topic, kind="event", payload=payload) for topic, _, payload in NEW_PACKETS], len(NEW_PACKETS))
    assert events == [(name, payload) for _, name, payload in NEW_PACKETS]


def test_the_progress_events_of_the_old_reading_are_not_in_the_map():
    assert "sources.video.progress" not in gateway_module.SSE_MAP
    assert "sources.coverage.progress" not in gateway_module.SSE_MAP
    names = {name for name, _ in gateway_module.SSE_MAP.values()}
    assert not {"video_scan_progress", "coverage_progress"} & names


def test_every_topic_of_the_map_is_named_by_a_module_that_publishes_it():
    """A topic renamed or removed in its owner must not stay in the map unnoticed."""
    root = Path(__file__).resolve().parent.parent / "modules"
    published = set()
    for source in root.rglob("*.py"):
        if source.parent.name != "gateway":
            published |= set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', source.read_text(encoding="utf-8")))
    assert [topic for topic in gateway_module.SSE_MAP if topic not in published] == []


@pytest.mark.asyncio
async def test_the_debug_groups_of_logging_are_read_when_the_system_says_it_is_up(tmp_path, monkeypatch):
    """Settings starts after the gateway, so the groups are read on the signal of the whole system, not by retries."""
    from gateway_fakes import serving
    read = asyncio.Event()
    seen: list[set] = []
    monkeypatch.setattr(core_logging, "set_debug_groups", lambda groups: (seen.append(groups), read.set()))
    answers = {"settings.values.by_prefix": {"values": {"debug_enabled_scan": "true", "debug_enabled_sources": "false"}}}
    async with serving(tmp_path, answers) as (_, owners, bus, _):
        assert owners.asked("settings.values.by_prefix") == []
        bus.publish(Envelope(topic="system.started", kind="event"))
        await asyncio.wait_for(read.wait(), 5)
    assert seen == [{"scan"}]
    assert owners.asked("settings.values.by_prefix") == [{"prefix": "debug_enabled_"}]


@pytest.mark.asyncio
async def test_settings_that_does_not_answer_is_a_warning_and_not_a_reason_to_wait(tmp_path):
    from gateway_fakes import serving
    async with serving(tmp_path, {"settings.values.by_prefix": RuntimeError("down")}) as (_, owners, bus, _):
        bus.publish(Envelope(topic="system.started", kind="event"))
        await owners.next("settings.values.by_prefix")
    assert len(owners.asked("settings.values.by_prefix")) == 1
