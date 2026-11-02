"""Obligations carried to the end: each keeps its own count of attempts and does not wait for an outside event."""
import asyncio
import logging

import pytest

from core import Bus, Envelope
from modules.voices.recovery import Recovery
from settle import wait_until

LOG = logging.getLogger("recovery-test")


class Stand:
    """The bus of the running test: the attempts below announce themselves on it, and the waiter reads it."""
    bus: Bus | None = None


@pytest.fixture(autouse=True)
def a_bus_for_the_test():
    Stand.bus = Bus()
    yield
    Stand.bus = None


class Stepped(Recovery):
    """A recovery whose every start and end of an attempt, and end of a report, is a step on the bus of the test.

    The state of an obligation changes only inside these (the rest between them is the thing under test, and it is
    short), so a waiter that looks again after each step looks exactly when the state could have changed. An attempt
    that has ended is also counted: the obligation has by then been put to rest or ended."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ended = 0

    @staticmethod
    def _step() -> None:
        Stand.bus.publish(Envelope("recovery.step", "event"))

    def want(self, key, attempt, report=None, *, now=False):
        async def stepped():
            self._step()
            try:
                return await attempt()
            finally:
                self.ended += 1
                self._step()

        async def reported():
            try:
                return await report()
            finally:
                self._step()

        super().want(key, stepped, reported if report is not None else None, now=now)


def recovery(pause=0.01, report_after=3):
    return Stepped(LOG, pause=lambda failures: min(pause * 2 ** failures, 0.05), report_after=lambda: report_after)


async def until(condition, seconds=3.0):
    assert await wait_until(condition, Stand.bus, seconds=seconds)


@pytest.mark.asyncio
async def test_an_obligation_is_tried_again_until_it_is_over_and_then_forgotten():
    carried, tries = recovery(), []

    async def attempt():
        tries.append(1)
        return len(tries) == 4

    carried.want("job:1", attempt)
    await until(lambda: carried.failures() == {} and len(tries) == 4)


@pytest.mark.asyncio
async def test_asking_twice_for_the_same_obligation_changes_nothing():
    carried, tries = recovery(), []
    gate = asyncio.Event()

    async def attempt():
        tries.append(1)
        await gate.wait()
        return True

    carried.want("k", attempt, now=True)
    carried.want("k", attempt, now=True)
    await until(lambda: tries)
    gate.set()
    await until(lambda: carried.failures() == {})
    assert len(tries) == 1


@pytest.mark.asyncio
async def test_the_owner_is_told_once_and_may_end_the_obligation(caplog):
    carried, told = recovery(report_after=2), []

    async def never():
        return False

    async def report():
        told.append(1)
        return True

    with caplog.at_level(logging.ERROR, logger=LOG.name):
        carried.want("k", never, report, now=True)
        await until(lambda: carried.failures() == {})
    assert told == [1] and sum("попыток без результата" in r.message for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_an_obligation_the_owner_does_not_end_goes_on_after_the_report():
    carried, tries, told = recovery(report_after=2), [], []

    async def attempt():
        tries.append(1)
        return len(tries) >= 8

    async def report():
        told.append(len(tries))
        return False

    carried.want("k", attempt, report, now=True)
    await until(lambda: carried.failures() == {})
    assert told == [2] and len(tries) == 8


@pytest.mark.asyncio
async def test_a_failing_attempt_does_not_end_the_obligation():
    carried, tries = recovery(), []

    async def attempt():
        tries.append(1)
        if len(tries) < 3:
            raise RuntimeError("the service answered nonsense")
        return True

    carried.want("k", attempt, now=True)
    await until(lambda: carried.failures() == {} and len(tries) == 3)


@pytest.mark.asyncio
async def test_a_poke_brings_the_next_attempt_forward():
    carried = Stepped(LOG, pause=lambda failures: 60.0, report_after=lambda: 99)
    job_tries, cleanup_tries = [], []

    async def job():
        job_tries.append(1)
        return len(job_tries) == 2

    async def cleanup():
        cleanup_tries.append(1)
        return len(cleanup_tries) == 2

    carried.want("job:1", job, now=True)
    carried.want("cleanup:1", cleanup, now=True)
    await until(lambda: carried.ended == 2)
    carried.poke("cleanup:")
    # What the poke woke runs in the same turn of the loop whichever obligation it is: when the one under the prefix has
    # been tried again, the other would have been too if it had been woken.
    await until(lambda: len(cleanup_tries) == 2 and "cleanup:1" not in carried.failures())
    assert len(job_tries) == 1, "only what is under the prefix is woken"
    carried.poke("job:")
    await until(lambda: carried.failures() == {})
    assert len(job_tries) == 2


@pytest.mark.asyncio
async def test_stopping_cancels_what_is_being_carried():
    carried = Stepped(LOG, pause=lambda failures: 60.0, report_after=lambda: 99)

    async def never():
        return False

    carried.want("k", never, now=True)
    await until(lambda: carried.failures() == {"k": 1})
    await carried.stop()
    assert carried.failures() == {}


@pytest.mark.asyncio
async def test_a_pause_that_cannot_be_computed_does_not_end_the_obligation():
    def broken(failures):
        raise OverflowError("int too large to convert to float")

    carried = Stepped(LOG, pause=broken, report_after=lambda: 3)

    async def attempt():
        return False

    carried.want("cleanup:needed", attempt, now=True)
    await until(lambda: carried.ended)  # the pause is asked for in the step that follows the attempt
    assert "cleanup:needed" in carried.failures()
    await carried.stop()


def test_the_pause_has_a_ceiling_whatever_the_number_of_failures():
    from modules.voices import module
    assert module._pause(0) == module.SUBMIT_RETRY_S
    assert module._pause(1024) == module._pause(10**9) == module.RETRY_CEILING_S


@pytest.mark.asyncio
async def test_the_real_pause_keeps_an_obligation_after_a_thousand_failures():
    from modules.voices import module
    carried = Stepped(LOG, pause=module._pause, report_after=lambda: 6)
    gate = asyncio.Event()

    async def attempt():
        await gate.wait()
        return False

    carried.want("cleanup:needed", attempt, now=True)
    await until(lambda: "cleanup:needed" in carried.failures())
    carried._items["cleanup:needed"].failures = 1023
    gate.set()
    await until(lambda: carried.ended)  # the pause after the thousandth failure is asked for in the step after the attempt
    assert "cleanup:needed" in carried.failures()
    await carried.stop()
