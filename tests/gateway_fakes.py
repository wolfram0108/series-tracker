"""Stand-ins for the owners a gateway route asks, each a module on the real bus, and a stand that serves the gateway
over them.

`Owners` answers the topics it is given (a value, a function of the payload, or an exception to refuse with) and
keeps what it was asked, so a test checks the exact body the gateway sent. `tap` lists everything the gateway put on
the bus, so a test can also say what it did NOT ask. Nothing waits for time: a command is awaited by the queue of
its topic.
"""
import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager

import httpx

from core.bus import Bus
from core.envelope import Envelope
from core.module import BaseModule
from core.runner import Runner
from modules.gateway import GatewayModule


class Owners(BaseModule):
    name = "owners"

    def __init__(self, bus, answers: dict) -> None:
        self.answers = dict(answers)
        self.calls: list[tuple[str, object]] = []
        self._arrived: dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)
        super().__init__(bus)

    def register(self) -> None:
        for topic in self.answers:
            self.handle(topic, self._answer_of(topic))

    def _answer_of(self, topic: str):
        async def answer(env: Envelope):
            self.calls.append((topic, env.payload))
            self._arrived[topic].put_nowait(env.payload)
            value = self.answers[topic]
            if isinstance(value, Exception):
                raise value
            return value(env.payload) if callable(value) else value

        return answer

    def asked(self, topic: str) -> list:
        """The payloads the topic has been asked with, in order."""
        return [payload for asked, payload in self.calls if asked == topic]

    async def next(self, topic: str):
        """The payload of the next command or query that reaches the topic; the timeout is a safeguard."""
        return await asyncio.wait_for(self._arrived[topic].get(), 5)


def tap(bus: Bus):
    """A subscription to everything on the bus; `published(sub)` lists what was put on it since."""
    return bus.subscribe("#")


def published(sub) -> list[Envelope]:
    """The queries, commands and events put on the bus since the tap began or was last read."""
    out = []
    while not sub.queue.empty():
        env = sub.queue.get_nowait()
        if env.kind != "reply":
            out.append(env)
    return out


@asynccontextmanager
async def serving(tmp_path, answers: dict, modules=(), watch: bool = True, **gateway_options):
    """The gateway over fake owners and any real modules: (client, owners, bus, tap). A tap counts as a subscriber
    of every topic, so a test of a topic nobody handles asks for `watch=False` (the tap is then None)."""
    bus = Bus()
    gateway = GatewayModule(bus, static_dir=str(tmp_path), templates_dir=str(tmp_path), auth_required=False,
                            **gateway_options)
    # A change of a series by the user is admitted by the owner of its runs (scan.edit.begin/end); a test that is not
    # about the admission finds every change admitted, one that is names its own answers.
    owners = Owners(bus, {"scan.edit.begin": {"ok": True}, "scan.edit.end": None, **answers})
    runner = Runner(bus, [gateway, *(make(bus) for make in modules), owners])
    await runner.start()
    spy = tap(bus) if watch else None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway.app), base_url="http://test") as client:
            yield client, owners, bus, spy
    finally:
        await runner.stop()
