"""Asking again lives in one place: the budget and the loop of core.retry, and no module keeps a loop of its own."""
import re
from pathlib import Path

import pytest

from core.retry import UNTIMED, Budget, retrying

ROOT = Path(__file__).resolve().parent.parent
# Where asking again is decided by `core.retry`; a loop with its own attempts or its own sleep would be a second place.
GUARDED = ["modules/downloads", "modules/sources/players", "modules/sources/policy.py", "modules/sources/sites", "modules/sources/table.py", "modules/torrents/qbt_client.py"]
# The torrents module also keeps periodic monitors, which wait on a timer by design; only its loops of attempts are guarded.
LOOP_GUARDED = GUARDED + ["modules/torrents/module.py"]


class FakeTime:
    """A clock that only moves when something sleeps, so no test waits."""

    def __init__(self) -> None:
        self.now, self.slept = 0.0, []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def scripted(*results):
    """A call that answers from a script; the last answer repeats."""
    state = {"calls": 0}

    async def call():
        state["calls"] += 1
        return results[min(state["calls"] - 1, len(results) - 1)]

    return call, state


async def run(call, budget, time, **kwargs):
    return await retrying(call, budget=budget, clock=time.clock, sleep=time.sleep, **kwargs)


@pytest.mark.asyncio
async def test_a_final_result_is_taken_at_once_without_a_wait():
    time = FakeTime()
    call, state = scripted("done")

    result = await run(call, Budget(), time, final=lambda value: value == "done")

    assert (result, state["calls"], time.slept) == (("done", 1, False), 1, [])


@pytest.mark.asyncio
async def test_an_unsure_result_is_asked_again_after_the_scheduled_pause():
    time = FakeTime()
    call, _ = scripted("unsure", "done")

    result = await run(call, Budget(), time, final=lambda value: value == "done")

    assert (result, time.slept) == (("done", 2, False), [2.0])


@pytest.mark.asyncio
async def test_what_stays_unsure_is_given_up_after_the_attempts_and_says_so():
    time = FakeTime()
    call, state = scripted("unsure")

    result = await run(call, Budget(), time, final=lambda value: value == "done")

    assert (result, state["calls"], time.slept) == (("unsure", 3, True), 3, [2.0, 6.0])


@pytest.mark.asyncio
async def test_the_time_limit_stops_the_asking_before_the_attempts_run_out():
    time = FakeTime()
    call, _ = scripted("unsure")

    result = await run(call, Budget(attempts=5, pauses=(5.0,), limit_s=8.0), time, final=lambda value: False)

    assert (result, time.slept) == (("unsure", 2, True), [5.0])


@pytest.mark.asyncio
async def test_a_budget_without_a_time_limit_is_bound_by_its_attempts_alone():
    time = FakeTime()
    call, state = scripted("unsure")

    result = await run(call, Budget(attempts=4, pauses=(1000.0,), limit_s=UNTIMED), time, final=lambda value: False)

    assert (result[1:], state["calls"], len(time.slept)) == ((4, True), 4, 3)


@pytest.mark.asyncio
async def test_the_wait_a_host_asks_for_outlasts_the_scheduled_pause():
    time = FakeTime()
    call, _ = scripted(10.0, 0.0)

    await run(call, Budget(), time, final=lambda value: value == 0.0, retry_after=lambda value: value)

    assert time.slept == [10.0]


@pytest.mark.asyncio
async def test_each_wait_is_told_to_the_caller_before_it_is_made():
    time, told = FakeTime(), []
    call, _ = scripted("unsure", "unsure", "done")

    await run(call, Budget(), time, final=lambda value: value == "done", on_retry=lambda value, attempt, pause: told.append((value, attempt, pause, list(time.slept))))

    assert told == [("unsure", 1, 2.0, []), ("unsure", 2, 6.0, [2.0])]


@pytest.mark.asyncio
async def test_a_failure_of_the_call_is_not_asked_again():
    time = FakeTime()

    async def call():
        raise ValueError("refused")

    with pytest.raises(ValueError):
        await run(call, Budget(), time, final=lambda value: True)

    assert time.slept == []


def test_the_pause_follows_the_schedule_and_repeats_its_last_value():
    budget = Budget(attempts=5, pauses=(1.0, 3.0))

    assert [budget.pause(attempt) for attempt in (1, 2, 3, 4)] == [1.0, 3.0, 3.0, 3.0]
    assert budget.pause(1, retry_after=9.0) == 9.0 and Budget(pauses=()).pause(1) == 0.0


def test_another_attempt_is_allowed_only_below_the_number_of_attempts():
    assert [Budget(attempts=3).allows(attempt) for attempt in (1, 2, 3)] == [True, True, False]


def _sources(entries):
    for entry in entries:
        path = ROOT / entry
        yield from (sorted(path.rglob("*.py")) if path.is_dir() else [path])


def test_no_module_keeps_a_loop_of_attempts_of_its_own():
    own_loop = re.compile(r"for\s+\w+\s+in\s+range\(.*(?:ATTEMPT|attempt|RETRIES|retries)")
    found = [f"{path.relative_to(ROOT)}:{number}" for path in _sources(LOOP_GUARDED)
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1) if own_loop.search(line)]

    assert found == []


def test_a_wait_in_these_modules_is_always_a_pause_the_budget_gave():
    sleeping = re.compile(r"asyncio\.sleep\((.*)\)")
    found = [f"{path.relative_to(ROOT)}:{number}: {line.strip()}" for path in _sources(GUARDED)
             if path.name != "policy.py"
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if (match := sleeping.search(line)) and match.group(1) != "pause"]

    assert found == []


def test_the_budget_is_defined_once():
    definitions = [str(path.relative_to(ROOT)) for path in sorted((ROOT / "modules").rglob("*.py"))
                   if re.search(r"^class Budget\b", path.read_text(encoding="utf-8"), re.MULTILINE)]

    assert definitions == []
