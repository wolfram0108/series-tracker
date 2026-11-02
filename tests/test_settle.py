"""The waiters of tests (tests/settle.py): they wait for a step of the system, and what never comes ends at the safeguard."""
import asyncio

import pytest

from core import BaseModule, Bus, Envelope
from settle import settle, wait_until


class Worker(BaseModule):
    name = "worker"

    def __init__(self, bus):
        super().__init__(bus)
        self.state = 0
        self.release = asyncio.Event()

    def register(self):
        self.handle("work.begin", self.on_begin)

    async def on_begin(self, env):
        self.spawn = asyncio.create_task(self._work())
        self._bg.add(self.spawn)
        self.spawn.add_done_callback(self._bg.discard)

    async def _work(self):
        await self.release.wait()
        self.state = 1
        self.publish_event("work.done")


@pytest.fixture
async def worker():
    bus = Bus()
    each = Worker(bus)
    await each.start()
    yield bus, each
    await each.stop()


@pytest.mark.asyncio
async def test_settle_waits_for_the_background_work_of_the_modules(worker):
    bus, each = worker
    bus.publish(Envelope(topic="work.begin", kind="event"))
    waiting = asyncio.create_task(settle(bus, each))
    for _ in range(10):
        await asyncio.sleep(0)
    assert not waiting.done()
    each.release.set()
    await asyncio.wait_for(waiting, 2)
    assert each.state == 1


@pytest.mark.asyncio
async def test_wait_until_looks_again_after_a_step_and_returns_when_the_condition_holds(worker):
    bus, each = worker
    bus.publish(Envelope(topic="work.begin", kind="event"))
    loop = asyncio.get_running_loop()
    loop.call_soon(each.release.set)
    assert await wait_until(lambda: each.state == 1, bus, each, seconds=2)


@pytest.mark.asyncio
async def test_wait_until_ends_at_the_safeguard_when_nothing_ever_changes(worker):
    bus, each = worker
    assert not await wait_until(lambda: each.state == 1, bus, each, seconds=0.2)
