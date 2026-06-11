"""Тесты ядра: шина, шаблоны топиков, query/reply через BaseModule."""
import asyncio

import pytest

from core import BaseModule, Bus, BusRequestError, Envelope, topic_matches


# --- сопоставление топиков ---------------------------------------------------

@pytest.mark.parametrize("pattern,topic,expected", [
    ("a.b.c", "a.b.c", True),
    ("a.b.c", "a.b.x", False),
    ("a.*.c", "a.b.c", True),
    ("a.*", "a.b.c", False),
    ("a.#", "a.b.c", True),
    ("#", "что.угодно", True),
    ("a.b", "a.b.c", False),
])
def test_topic_matches(pattern, topic, expected):
    assert topic_matches(pattern, topic) is expected


# --- pub/sub -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_publish_delivers_to_matching_subscribers():
    bus = Bus()
    sub_exact = bus.subscribe("downloads.task.finished")
    sub_wild = bus.subscribe("downloads.#")
    sub_other = bus.subscribe("slicing.#")

    n = bus.publish(Envelope(topic="downloads.task.finished", kind="event",
                             payload={"uid": "x"}))
    assert n == 2
    assert (await sub_exact.queue.get()).payload == {"uid": "x"}
    assert (await sub_wild.queue.get()).payload == {"uid": "x"}
    assert sub_other.queue.empty()


@pytest.mark.asyncio
async def test_unsubscribe_stops_delivery():
    bus = Bus()
    sub = bus.subscribe("a.b")
    bus.unsubscribe(sub)
    assert bus.publish(Envelope(topic="a.b", kind="event")) == 0


# --- query/reply через BaseModule ---------------------------------------------

class EchoModule(BaseModule):
    name = "echo"

    def register(self):
        self.handle("echo.ping", self.on_ping)
        self.handle("echo.fail", self.on_fail)

    async def on_ping(self, env):
        return {"pong": env.payload}

    async def on_fail(self, env):
        raise ValueError("намеренная ошибка")


class ClientModule(BaseModule):
    name = "client"


@pytest.mark.asyncio
async def test_request_reply_roundtrip():
    bus = Bus()
    echo, client = EchoModule(bus), ClientModule(bus)
    await echo.start()
    await client.start()
    try:
        result = await client.request("echo.ping", {"msg": "привет"}, timeout=2)
        assert result == {"pong": {"msg": "привет"}}
    finally:
        await client.stop()
        await echo.stop()


@pytest.mark.asyncio
async def test_request_handler_error_raises_on_caller_side():
    bus = Bus()
    echo, client = EchoModule(bus), ClientModule(bus)
    await echo.start()
    await client.start()
    try:
        with pytest.raises(BusRequestError, match="намеренная ошибка"):
            await client.request("echo.fail", timeout=2)
    finally:
        await client.stop()
        await echo.stop()


@pytest.mark.asyncio
async def test_request_without_handler_fails_fast():
    bus = Bus()
    client = ClientModule(bus)
    await client.start()
    try:
        with pytest.raises(BusRequestError, match="нет обработчика"):
            await client.request("никого.нет.дома", timeout=2)
    finally:
        await client.stop()


@pytest.mark.asyncio
async def test_parallel_requests_do_not_mix_replies():
    bus = Bus()
    echo, client = EchoModule(bus), ClientModule(bus)
    await echo.start()
    await client.start()
    try:
        results = await asyncio.gather(*[
            client.request("echo.ping", i, timeout=2) for i in range(20)])
        assert [r["pong"] for r in results] == list(range(20))
    finally:
        await client.stop()
        await echo.stop()
