"""Waiting in a test is waiting for a signal of the system, not for a pause.

`settle` waits for quiet: the bus has taken apart every envelope of its modules, and the modules have nothing running in
the background. A test that has to show that something did not happen, or to see the end of what an event began, waits
for this. `wait_until` waits for a condition: it is looked at again after every step of the bus (an envelope published
or taken apart) and after every end of a task of the modules, because in a system that works by messages nothing else
changes it. A condition that does not come true is a stuck test, and the one number here only ends it.
"""
import asyncio

SAFEGUARD_S = 20.0


async def settle(bus, *modules, seconds: float = SAFEGUARD_S) -> None:
    async def quiet() -> None:
        while True:
            await bus.settled()
            running = {task for each in modules for task in each.background() if not task.done()}
            if not running:
                return
            await asyncio.wait(running)

    await asyncio.wait_for(quiet(), seconds)


async def wait_until(predicate, bus, *modules, seconds: float = SAFEGUARD_S) -> bool:
    """True when `predicate()` holds; False when it did not within the safeguard."""
    async def holds() -> None:
        while not predicate():
            step = bus.next_step()  # taken at the moment the condition was looked at
            waiting = [asyncio.ensure_future(step.wait())]
            running = {task for each in modules for task in each.background() if not task.done()}
            if running:
                waiting.append(asyncio.ensure_future(asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)))
            try:
                await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for each in waiting:
                    each.cancel()

    try:
        await asyncio.wait_for(holds(), seconds)
    except asyncio.TimeoutError:
        return False  # TEMPORARY strict check
    return True
