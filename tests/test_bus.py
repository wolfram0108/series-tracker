"""Тесты ядра: шина, шаблоны топиков, query/reply через BaseModule."""
import asyncio
import json

import pytest

from core import BaseModule, Bus, BusRequestError, Envelope, topic_matches
from core.reasons import reason


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
async def test_one_subscription_takes_several_exact_topics_in_the_order_they_were_published():
    bus = Bus()
    sub = bus.subscribe("a.one", "b.two")
    for topic in ("a.one", "c.three", "b.two", "a.one", "a.one.more"):
        bus.publish(Envelope(topic=topic, kind="event", payload=topic))
    got = [sub.queue.get_nowait().payload for _ in range(sub.queue.qsize())]
    assert got == ["a.one", "b.two", "a.one"]


def test_a_subscription_names_its_topics():
    with pytest.raises(ValueError):
        Bus().subscribe()


@pytest.mark.parametrize("pattern", ["a.*", "a.#", "#", "*.b"])
def test_a_module_handles_exact_topics_only(pattern):
    """A family of topics would bring a module envelopes it does not handle (and make it answer queries of any of them):
    a module is subscribed to what it handles and to nothing else."""
    class Wide(BaseModule):
        name = "wide"

        def register(self):
            self.handle(pattern, lambda env: None)

    with pytest.raises(ValueError, match="не точный топик"):
        Wide(Bus())


@pytest.mark.asyncio
async def test_a_module_relays_the_refusal_of_another_with_its_kind():
    """A "not found" that a module got from another goes on as a "not found": the caller tells a refusal from a failure
    by the kind, however many modules it went through."""
    bus = Bus()

    class Owner(BaseModule):
        name = "owner"

        def register(self):
            self.handle("owner.get", self.get)

        async def get(self, env):
            raise LookupError("нет такого")

    class Middle(BaseModule):
        name = "middle"

        def register(self):
            self.handle("middle.get", self.get)

        async def get(self, env):
            return await self.request("owner.get", {}, timeout=5)

    caller = BaseModule(bus)
    modules = [Owner(bus), Middle(bus), caller]
    for module in modules:
        await module.start()
    try:
        with pytest.raises(BusRequestError) as refused:
            await caller.request("middle.get", {}, timeout=5)
        assert str(refused.value) == "LookupError: нет такого"
    finally:
        for module in modules:
            await module.stop()


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
        self.handle("echo.sync", self.on_sync)

    async def on_ping(self, env):
        return {"pong": env.payload}

    async def on_fail(self, env):
        raise ValueError("намеренная ошибка")

    def on_sync(self, env):
        return {"sync": env.payload}


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
async def test_sync_handler_is_supported():
    bus = Bus()
    echo, client = EchoModule(bus), ClientModule(bus)
    await echo.start()
    await client.start()
    try:
        result = await client.request("echo.sync", {"msg": "x"}, timeout=2)
        assert result == {"sync": {"msg": "x"}}
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


# --- quiet of the bus: a signal, not a pause ------------------------------------

class Chain(BaseModule):
    """`chain.first` порождает `chain.second`; второй обработчик медленный и записывает конец своей работы."""
    name = "chain"

    def __init__(self, bus):
        super().__init__(bus)
        self.done: list[str] = []
        self.release = asyncio.Event()

    def register(self):
        self.handle("chain.first", self.on_first)
        self.handle("chain.second", self.on_second)
        self.handle("chain.parallel", self.on_parallel, concurrent=True)

    async def on_first(self, env):
        self.publish_event("chain.second", env.payload)

    async def on_second(self, env):
        await self.release.wait()
        self.done.append("second")

    async def on_parallel(self, env):
        await self.release.wait()
        self.done.append("parallel")


@pytest.mark.asyncio
async def test_a_bus_with_nothing_in_flight_is_settled_at_once():
    bus = Bus()
    await asyncio.wait_for(bus.settled(), 1)


@pytest.mark.asyncio
async def test_settled_waits_for_what_the_handlers_published_on_the_way():
    bus = Bus()
    chain = Chain(bus)
    await chain.start()
    try:
        bus.publish(Envelope(topic="chain.first", kind="event", payload=1))
        waiting = asyncio.create_task(bus.settled())
        await asyncio.sleep(0)  # the first handler has run, the second is held
        for _ in range(5):
            await asyncio.sleep(0)
        assert not waiting.done() and chain.done == []
        chain.release.set()
        await asyncio.wait_for(waiting, 2)
        assert chain.done == ["second"]
    finally:
        await chain.stop()


@pytest.mark.asyncio
async def test_a_concurrent_handler_counts_until_its_task_ends():
    bus = Bus()
    chain = Chain(bus)
    await chain.start()
    try:
        bus.publish(Envelope(topic="chain.parallel", kind="event"))
        for _ in range(5):
            await asyncio.sleep(0)
        waiting = asyncio.create_task(bus.settled())
        for _ in range(5):
            await asyncio.sleep(0)
        assert not waiting.done()
        chain.release.set()
        await asyncio.wait_for(waiting, 2)
        assert chain.done == ["parallel"]
    finally:
        await chain.stop()


@pytest.mark.asyncio
async def test_a_subscription_that_does_not_report_is_not_waited_for():
    bus = Bus()
    raw = bus.subscribe("chain.first")  # a test or a stream of events: nobody reports its envelopes
    bus.publish(Envelope(topic="chain.first", kind="event"))
    await asyncio.wait_for(bus.settled(), 1)
    assert raw.queue.qsize() == 1


@pytest.mark.asyncio
async def test_a_module_that_stops_with_work_in_hand_does_not_hold_the_bus_for_ever():
    bus = Bus()
    chain = Chain(bus)
    await chain.start()
    bus.publish(Envelope(topic="chain.first", kind="event"))
    bus.publish(Envelope(topic="chain.parallel", kind="event"))
    for _ in range(5):
        await asyncio.sleep(0)
    await chain.stop()
    await asyncio.wait_for(bus.settled(), 1)


@pytest.mark.asyncio
async def test_a_waiter_that_took_the_step_signal_loses_no_step_between_the_look_and_the_wait():
    bus = Bus()
    step = bus.next_step()
    bus.publish(Envelope(topic="a.b", kind="event"))
    await asyncio.wait_for(step.wait(), 1)  # the step happened after the signal was taken, so it is not missed
    assert bus.next_step() is not step



class Refuser(BaseModule):
    name = "refuser"

    def register(self):
        self.handle("refuser.edit", self.on_edit)
        self.handle("refuser.raise", self.on_raise)

    async def on_edit(self, env):
        from core.contracts.availability import Action, require
        require(Action.EDIT, "scanning")

    async def on_raise(self, env):
        RAISED[env.payload["kind"]]()


def _raise(exc: Exception):
    raise exc


# What a handler may raise: the answers a module gives (a «not found», a value refused with a coded reason) and the slips of
# code and libraries, which raise the same kinds or their kin.
RAISED = {
    "lookup": lambda: _raise(LookupError("сериал 7 не найден")),
    "coded": lambda: _raise(ValueError(reason("voice_order_mismatch", season=6))),
    "plain_value": lambda: int("слово"),
    "key": lambda: {}["missing"],
    "json": lambda: json.loads("<html>"),
    "runtime": lambda: _raise(RuntimeError("сломалось")),
}


@pytest.mark.asyncio
async def test_a_refusal_is_the_answer_of_the_module_and_is_logged_without_a_trace(caplog):
    bus = Bus()
    refuser, client = Refuser(bus), ClientModule(bus)
    await refuser.start()
    await client.start()
    try:
        with caplog.at_level("INFO"), pytest.raises(BusRequestError, match="NotAllowed"):
            await client.request("refuser.edit", {}, timeout=2)
    finally:
        await refuser.stop()
        await client.stop()
    found = [record for record in caplog.records if record.name == "refuser" and "refuser.edit" in record.getMessage()]
    assert found and all(record.levelname == "INFO" and record.exc_info is None for record in found)


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "refused"), [("lookup", True), ("coded", True), ("plain_value", False), ("key", False),
                                               ("json", False), ("runtime", False)])
async def test_a_no_the_module_gives_is_a_refusal_and_a_slip_of_code_is_an_error_with_its_trace(caplog, kind, refused):
    bus = Bus()
    refuser, client = Refuser(bus), ClientModule(bus)
    await refuser.start()
    await client.start()
    try:
        with caplog.at_level("INFO"), pytest.raises(BusRequestError):
            await client.request("refuser.raise", {"kind": kind}, timeout=2)
    finally:
        await refuser.stop()
        await client.stop()
    found = [record for record in caplog.records if record.name == "refuser" and "refuser.raise" in record.getMessage()]
    assert len(found) == 1
    if refused:
        assert found[0].levelname == "INFO" and found[0].exc_info is None
    else:
        assert found[0].levelname == "ERROR" and found[0].exc_info is not None


def test_a_process_without_a_folder_of_logs_writes_no_log_files(tmp_path):
    """A script run from the folder of the project must not write into the logs of the service (start.sh names them)."""
    import os
    import subprocess
    import sys

    env = {key: value for key, value in os.environ.items() if key != "ST_LOG_DIR"}
    env["PYTHONPATH"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    code = "from core.logging import get_logger, log_file; get_logger('probe').error('строка'); print(log_file('x.log'))"
    done = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)

    assert done.returncode == 0 and done.stdout.strip() == "None"
    assert "строка" in done.stderr and not (tmp_path / "logs").exists()


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


def test_a_burst_of_states_goes_as_the_last_of_each_and_other_events_all_in_order():
    from modules.gateway.module import latest_only

    def event(topic, payload):
        return Envelope(topic=topic, kind="event", payload=payload)

    batch = [event("sources.table.progress", {"url": "a", "current": 1}), event("voices.dub.changed", {"n": 1}),
             event("sources.table.progress", {"url": "b", "current": 1}),
             event("sources.table.progress", {"url": "a", "current": 2}), event("voices.dub.changed", {"n": 2}),
             event("downloads.queue.changed", {"tasks": [1]}), event("downloads.queue.changed", {"tasks": []})]

    kept = [(env.topic, env.payload) for env in latest_only(batch)]

    assert kept == [("voices.dub.changed", {"n": 1}), ("sources.table.progress", {"url": "b", "current": 1}),
                    ("sources.table.progress", {"url": "a", "current": 2}), ("voices.dub.changed", {"n": 2}),
                    ("downloads.queue.changed", {"tasks": []})]


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



async def test_a_module_that_stops_stops_the_work_its_cancelled_tasks_start_on_their_way_out():
    """A queue that moves on when its job is cancelled starts the next job while the module stops: that job is stopped
    before its first step and never runs, and nothing of the module is left to the end of the loop."""
    ran, made = [], []
    started = asyncio.Event()

    class Queue(BaseModule):
        name = "queue"

        def take(self, job: int) -> None:
            made.append(self.spawn(self.work(job)))

        async def work(self, job: int) -> None:
            ran.append(job)
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.take(job + 1)

    module = Queue(Bus())
    await module.start()
    module.take(1)
    await started.wait()
    await module.stop()
    assert ran == [1] and len(made) == 2 and all(task.cancelled() for task in made)
