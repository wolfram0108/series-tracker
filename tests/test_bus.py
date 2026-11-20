"""Тесты ядра: шина, шаблоны топиков, query/reply через BaseModule."""

import pytest

from core.bus import Bus
from core.envelope import Envelope
from core.module import BaseModule


# --- сопоставление топиков ---------------------------------------------------

# --- pub/sub -----------------------------------------------------------------

# --- query/reply через BaseModule ---------------------------------------------

class EchoModule(BaseModule):
    name = "echo"

    def register(self):
        self.handle("echo.ping", self.on_ping)
        self.handle("echo.fail", self.on_fail)
        self.handle("echo.sync", self.on_sync)

    async def on_ping(self, env):
        return {"pong": env.payload}

    async def on_fail(self, env):
        raise ValueError("намеренная ошибка")

    def on_sync(self, env):
        return {"sync": env.payload}


# --- quiet of the bus: a signal, not a pause ------------------------------------

# What a handler may raise: the answers a module gives (a «not found», a value refused with a coded reason) and the slips of
# code and libraries, which raise the same kinds or their kin.
def test_a_queue_that_grows_is_told_once_per_burst(caplog):
    from core import bus as bus_module

    bus = Bus()
    sub = bus.subscribe("#")
    with caplog.at_level("WARNING"):
        for number in range(bus_module._QUEUE_WARN_SIZE + 50):
            bus.publish(Envelope(topic="x.y", kind="event", payload=number))
    assert len([r for r in caplog.records if "разрослась" in r.getMessage()]) == 1
    while not sub.queue.empty():
        sub.queue.get_nowait()
    caplog.clear()
    with caplog.at_level("WARNING"):
        for number in range(bus_module._QUEUE_WARN_SIZE + 2):
            bus.publish(Envelope(topic="x.y", kind="event", payload=number))
    assert len([r for r in caplog.records if "разрослась" in r.getMessage()]) == 1  # a new burst is told again


@pytest.mark.asyncio
async def test_a_change_whose_caller_stopped_waiting_is_told_and_its_late_end_too(caplog):
    import logging
    from core.admission import Admission

    released = []
    admission = Admission("t1", lambda: released.append(True), log=logging.getLogger("probe"))
    admission.sent("r1")
    with caplog.at_level("INFO"):
        admission.close()
        assert not released and any("ждут ещё 1" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)
        admission.answered("r1")
    assert released == [True] and any("поздние ответы пришли" in r.getMessage() for r in caplog.records)
