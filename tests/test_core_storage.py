"""Storage calls from async code wait for a limited time and never take the process's default pool."""
import asyncio
import os
import time

import pytest

from core import storage
from settle import path_free


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
    await path_free(path)
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
    await path_free(path)
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
    await path_free(path)
    assert told == ["free"]
    storage.when_free(path, lambda: told.append("at once"))
    assert told == ["free", "at once"]


@pytest.mark.asyncio
async def test_a_cancelled_wait_does_not_free_what_the_call_owns(tmp_path):
    import threading
    release = threading.Event()
    path = str(tmp_path / "track.flac")
    task = asyncio.create_task(storage.call(stalled, release, timeout=5, owns=path))
    await asyncio.sleep(0)  # the call takes its hold in its first step
    assert storage.busy(path)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert storage.busy(path)
    release.set()
    await path_free(path)
    assert not storage.busy(path)


@pytest.mark.asyncio
async def test_a_hold_is_taken_whole_or_refused_and_is_the_holders_own(tmp_path):
    path = str(tmp_path / "e.mp4")
    seen = {}
    go = asyncio.Event()

    async def another_task():
        await go.wait()
        seen["busy"] = storage.busy(path)
        with pytest.raises(storage.StorageBusy):
            storage.acquire(str(tmp_path / "free.mp4"), path)
        seen["partial"] = storage.busy(str(tmp_path / "free.mp4"))
        with pytest.raises(storage.StorageBusy):
            await storage.call(lambda: None, owns=str(tmp_path))

    other = asyncio.create_task(another_task())  # made before the hold: its context has none
    with storage.hold(path):
        assert not storage.busy(path), "the holder is not refused by its own hold"
        await storage.call(lambda: None, owns=(path + ".part", path))
        go.set()
        await other
    assert seen == {"busy": True, "partial": False}, "a refused claim takes none of its paths"
    assert not storage.busy(path)


@pytest.mark.asyncio
async def test_a_hold_lasts_until_what_was_attached_to_it_has_ended(tmp_path):
    path = str(tmp_path / "e.mp4")
    told = []
    ending = asyncio.get_running_loop().create_future()
    with storage.hold(path) as taken:
        taken.attach(ending)
    assert storage.busy(path), "the owner left, the writing did not"
    storage.when_free(path, lambda: told.append("free"))
    assert told == []
    ending.set_result(None)
    await path_free(path)
    assert not storage.busy(path) and told == ["free"]


@pytest.mark.asyncio
async def test_a_hold_is_released_when_its_block_fails(tmp_path):
    path = str(tmp_path / "e.mp4")
    with pytest.raises(RuntimeError):
        with storage.hold(path):
            raise RuntimeError("boom")
    assert not storage.busy(path)


@pytest.mark.asyncio
async def test_a_file_that_is_not_wanted_is_removed_and_a_failure_is_named_not_swallowed(tmp_path, monkeypatch):
    import threading
    told = []
    monkeypatch.setattr(storage._log, "warning", lambda message, *args: told.append(message % args))
    unwanted = tmp_path / "a.part"
    unwanted.write_text("x")
    assert await storage.discard(str(unwanted)) is True and not unwanted.exists()
    assert await storage.discard(str(tmp_path / "never-was")) is True and told == []
    kept = tmp_path / "b.part"
    kept.write_text("x")

    def refuse(path):
        raise PermissionError("read-only")

    monkeypatch.setattr(os, "remove", refuse)
    assert await storage.discard(str(kept)) is False and kept.exists()
    assert len(told) == 1 and str(kept) in told[0]
    monkeypatch.undo()
    release = threading.Event()
    owned = str(kept)
    with pytest.raises(storage.StorageTimeout):
        await storage.call(stalled, release, timeout=0.05, owns=owned)
    try:
        assert await storage.discard(owned) is False and kept.exists(), "a name another call owns is not touched"
    finally:
        release.set()


def test_no_module_swallows_the_failure_of_a_file_removal():
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    silent = re.compile(r"os\.(remove|unlink)\([^)\n]*\)\s*\n\s*except OSError:\s*\n\s*pass")
    offenders = [str(path.relative_to(root)) for folder in ("core", "modules") for path in (root / folder).rglob("*.py")
                 if silent.search(path.read_text(encoding="utf-8"))]
    assert offenders == [], "use core.storage.remove_file or discard: a leftover must be named"
