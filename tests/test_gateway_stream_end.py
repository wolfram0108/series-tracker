"""An endless event stream ends when the service is told to stop, so that a restart does not wait for it."""
import asyncio
import signal

import pytest

from core import Bus
from modules.gateway import GatewayModule


def _gateway(tmp_path):
    return GatewayModule(Bus(), static_dir=str(tmp_path), auth_required=False)


async def _open_stream(gateway):
    reading = asyncio.ensure_future(_drain(gateway._sse_stream()))
    while not gateway._sse_subs:
        await asyncio.sleep(0)
    return reading


async def _drain(stream):
    return [chunk async for chunk in stream]


@pytest.mark.asyncio
async def test_an_open_stream_ends_when_the_streams_are_told_to_end(tmp_path):
    gateway = _gateway(tmp_path)
    reading = await asyncio.wait_for(_open_stream(gateway), 5)
    gateway._end_streams()
    await asyncio.wait_for(reading, 5)
    assert gateway._sse_subs == {}


@pytest.mark.asyncio
async def test_a_stop_signal_ends_the_streams_and_still_reaches_the_handler_that_was_there(tmp_path):
    seen = []

    def before(number, frame):
        seen.append(number)

    original = signal.signal(signal.SIGTERM, before)
    try:
        gateway = _gateway(tmp_path)
        await gateway.start()
        reading = await asyncio.wait_for(_open_stream(gateway), 5)
        signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
        await asyncio.wait_for(reading, 5)
        assert seen == [signal.SIGTERM]
        await gateway.stop()
        assert signal.getsignal(signal.SIGTERM) is before
    finally:
        signal.signal(signal.SIGTERM, original)
