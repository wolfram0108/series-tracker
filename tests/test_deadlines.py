"""The table of fuses of the requests over the bus (core/deadlines.py): what it says, how fuses nest, and that
`BaseModule.request` takes the fuse from it."""
import asyncio

import pytest

from core import BaseModule, Bus
from core import module as module_base
from core.deadlines import (HANDOVER_S, QUICK_S, RULES_S, STANDARD_S, TABLE_READ_S, TABLE_WAIT_S, deadline_of,
                            topics)


def test_a_topic_that_is_not_listed_has_the_standard_fuse():
    assert deadline_of("nobody.listed.this") == STANDARD_S


def test_a_topic_whose_work_is_a_step_for_each_unit_scales_with_the_units():
    assert deadline_of("composition.recompute") == RULES_S
    assert deadline_of("composition.recompute", units=4) == 4 * RULES_S
    assert deadline_of("composition.recompute", units=0) == RULES_S  # no sources still asks once


def test_a_request_made_of_an_unbounded_number_of_steps_has_no_fuse_of_its_own():
    assert deadline_of("scan.series.run") is None
    assert deadline_of("scan.series.prepare") is None


def test_fuses_nest_the_outer_one_never_fires_before_an_inner_one():
    steps = ("sources.table.get", "rules.apply", "voices.groups.resolve")
    assert deadline_of("composition.preview") >= sum(deadline_of(step) for step in steps)
    assert TABLE_WAIT_S >= 2 * TABLE_READ_S
    assert deadline_of("sources.table.get") < TABLE_READ_S  # the request only starts a read or answers from the store
    assert deadline_of("downloads.dispatch") == deadline_of("voices.dispatch") == HANDOVER_S


def test_a_quick_topic_is_quick_and_no_listed_topic_is_shorter_than_it():
    assert deadline_of("settings.value.set") == QUICK_S
    assert all(deadline_of(topic) is None or deadline_of(topic) >= QUICK_S for topic in topics())


class Slow(BaseModule):
    name = "slow"

    def register(self):
        self.handle("slow.answer", self.on_answer)

    async def on_answer(self, env):
        await asyncio.sleep(env.payload)
        return "answered"


class Asker(BaseModule):
    name = "asker"


@pytest.fixture
async def pair():
    bus = Bus()
    slow, asker = Slow(bus), Asker(bus)
    await slow.start()
    await asker.start()
    yield asker
    await asker.stop()
    await slow.stop()


@pytest.mark.asyncio
async def test_a_request_takes_its_fuse_from_the_table(pair, monkeypatch):
    seen = []

    def table(topic, units=1):
        seen.append((topic, units))
        return 0.05

    monkeypatch.setattr(module_base, "deadline_of", table)
    with pytest.raises(asyncio.TimeoutError):
        await pair.request("slow.answer", 5)
    assert seen == [("slow.answer", 1)]
    with pytest.raises(asyncio.TimeoutError):
        await pair.request("slow.answer", 5, units=3)
    assert seen[-1] == ("slow.answer", 3)


@pytest.mark.asyncio
async def test_a_request_with_no_fuse_waits_for_the_answer(pair, monkeypatch):
    monkeypatch.setattr(module_base, "deadline_of", lambda topic, units=1: None)
    assert await pair.request("slow.answer", 0.1) == "answered"


@pytest.mark.asyncio
async def test_a_fuse_given_by_the_caller_overrides_the_table(pair, monkeypatch):
    monkeypatch.setattr(module_base, "deadline_of", lambda topic, units=1: 5.0)
    with pytest.raises(asyncio.TimeoutError):
        await pair.request("slow.answer", 5, timeout=0.05)
