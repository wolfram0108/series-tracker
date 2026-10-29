"""Storage calls from async code wait for a limited time and never take the process's default pool."""
import asyncio
import os
import time

import pytest

from core import storage


@pytest.mark.asyncio
async def test_a_call_returns_its_result():
    assert await storage.call(lambda a, b: a + b, 2, 3, timeout=5) == 5


@pytest.mark.asyncio
async def test_a_stalled_call_ends_the_wait_with_a_timeout_while_others_still_run():
    started = time.monotonic()
    stalled = asyncio.create_task(storage.call(time.sleep, 1.5, timeout=0.2))
    assert await storage.call(lambda: "fast", timeout=5) == "fast"
    with pytest.raises(storage.StorageTimeout, match="хранилище не ответило"):
        await stalled
    assert time.monotonic() - started < 1.0


@pytest.mark.asyncio
async def test_the_errors_of_the_call_reach_the_caller_as_they_are():
    def refuse():
        raise PermissionError("read-only")

    with pytest.raises(PermissionError):
        await storage.call(refuse, timeout=5)


@pytest.mark.asyncio
async def test_keyword_arguments_reach_the_call(tmp_path):
    target = tmp_path / "a" / "b"
    await storage.call(os.makedirs, str(target), exist_ok=True)
    assert target.is_dir()


def stalled(release):
    release.wait(5)


@pytest.mark.asyncio
async def test_what_a_call_owns_stays_owned_after_the_wait_ends_until_the_call_returns(tmp_path):
    import threading
    release = threading.Event()
    path = str(tmp_path / "track.flac")
    with pytest.raises(storage.StorageTimeout):
        await storage.call(stalled, release, timeout=0.1, owns=path)
    assert storage.busy(path) and storage.busy(path + ".part") and storage.busy(str(tmp_path / "track.flac" / "x"))
    assert storage.busy(str(tmp_path)) and not storage.busy(str(tmp_path / "other.flac"))
    release.set()
    await asyncio.sleep(0.1)
    assert not storage.busy(path)


@pytest.mark.asyncio
async def test_a_change_on_an_owned_path_is_refused_not_queued(tmp_path):
    import threading
    release = threading.Event()
    path = str(tmp_path / "track.flac")
    with pytest.raises(storage.StorageTimeout):
        await storage.call(stalled, release, timeout=0.1, owns=path)
    with pytest.raises(storage.StorageBusy):
        await storage.call(lambda: None, owns=path)
    assert await storage.call(lambda: "reading is not a change") == "reading is not a change"
    release.set()
    await asyncio.sleep(0.1)
    await storage.call(lambda: None, owns=path)


@pytest.mark.asyncio
async def test_whoever_needs_the_path_is_told_when_it_is_free(tmp_path):
    import threading
    release = threading.Event()
    path = str(tmp_path / "track.flac")
    told = []
    with pytest.raises(storage.StorageTimeout):
        await storage.call(stalled, release, timeout=0.1, owns=path)
    storage.when_free(path, lambda: told.append("free"))
    assert told == []
    release.set()
    await asyncio.sleep(0.1)
    assert told == ["free"]
    storage.when_free(path, lambda: told.append("at once"))
    assert told == ["free", "at once"]


@pytest.mark.asyncio
async def test_a_cancelled_wait_does_not_free_what_the_call_owns(tmp_path):
    import threading
    release = threading.Event()
    path = str(tmp_path / "track.flac")
    task = asyncio.create_task(storage.call(stalled, release, timeout=5, owns=path))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert storage.busy(path)
    release.set()
    await asyncio.sleep(0.1)
    assert not storage.busy(path)
