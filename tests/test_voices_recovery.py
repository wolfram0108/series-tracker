"""Obligations carried to the end: each keeps its own count of attempts and does not wait for an outside event."""
import asyncio
import logging

import pytest

from modules.voices.recovery import Recovery

LOG = logging.getLogger("recovery-test")


def recovery(pause=0.01, report_after=3):
    return Recovery(LOG, pause=lambda failures: min(pause * 2 ** failures, 0.05), report_after=lambda: report_after)


async def until(condition, seconds=3.0):
    end = asyncio.get_running_loop().time() + seconds
    while not condition():
        assert asyncio.get_running_loop().time() < end
        await asyncio.sleep(0.005)


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
    carried = Recovery(LOG, pause=lambda failures: 60.0, report_after=lambda: 99)
    tries = []

    async def attempt():
        tries.append(1)
        return len(tries) == 2

    carried.want("job:1", attempt, now=True)
    await until(lambda: tries)
    carried.poke("cleanup:")
    await asyncio.sleep(0.05)
    assert len(tries) == 1, "only what is under the prefix is woken"
    carried.poke("job:")
    await until(lambda: carried.failures() == {})
    assert len(tries) == 2


@pytest.mark.asyncio
async def test_stopping_cancels_what_is_being_carried():
    carried = Recovery(LOG, pause=lambda failures: 60.0, report_after=lambda: 99)

    async def never():
        return False

    carried.want("k", never, now=True)
    await until(lambda: carried.failures() == {"k": 1})
    await carried.stop()
    assert carried.failures() == {}
