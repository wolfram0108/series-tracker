"""The watch of the event loop names the work that held it, and stays silent while the loop moves."""
import asyncio
import time

from core.loopwatch import LoopWatch


def _holds_the_loop(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        sum(range(1000))  # pure work, as a count over rows is


async def _watched(work) -> list:
    reports = []
    watch = LoopWatch(asyncio.get_running_loop(), lambda *told: reports.append(told))
    watch.start()
    try:
        await asyncio.sleep(0.1)
        await work()
        await asyncio.sleep(0.2)
    finally:
        watch.stop()
    return reports


def test_a_stall_is_told_with_the_function_that_held_the_loop():
    async def work():
        _holds_the_loop(0.4)

    reports = asyncio.run(_watched(work))
    assert len(reports) == 1
    held, chain, count, samples, other = reports[0]
    assert 0.3 <= held <= 0.6
    assert chain and "_holds_the_loop" in chain[0] and chain[0].startswith("tests/test_loopwatch.py:")
    assert count >= samples // 2 > 0
    assert other == ()  # the loop ran its own code: no other thread is blamed


def test_a_thread_that_keeps_the_interpreter_in_one_call_of_c_code_is_named():
    import threading

    def held_by_another_thread():
        sum(range(60_000_000))  # one call of C code that keeps the interpreter for its whole length

    async def work():
        worker = threading.Thread(target=held_by_another_thread, name="compute_0")
        worker.start()
        await asyncio.get_running_loop().run_in_executor(None, worker.join)

    reports = asyncio.run(_watched(work))
    assert reports, "the sum holds the interpreter for longer than a stall"
    assert any(other[:1] == ("поток compute_0",) and "held_by_another_thread" in other[1] for *_, other in reports)


def test_a_loop_that_waits_is_not_a_stall():
    async def work():
        await asyncio.sleep(0.4)  # the loop serves others while it waits
        _holds_the_loop(0.03)

    assert asyncio.run(_watched(work)) == []
