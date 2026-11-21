"""Download of one ready file in parts at once: the parts are asked for with HTTP Range and written in place.

A player hands out some files as a bare address of its CDN, which a downloader cannot open by the page, and one
connection to such a CDN is several times slower than a few at once.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Awaitable, Callable

import httpx

from calc.downloads.failure import Final
from core import storage
from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from .failure import GONE

ProgressCb = Callable[[dict], Awaitable[None]]

PART_BYTES = 8 * 1024 * 1024
MAX_WORKERS = 16
PART_ATTEMPTS = 3
PART_PAUSES_S = (1.0, 3.0)
READ_TIMEOUT_S = 30.0
CONNECT_TIMEOUT_S = 10.0
PROGRESS_INTERVAL_S = 0.5
_TRANSIENT_STATUSES = frozenset({408, 425, 429})
_CONTENT_RANGE = re.compile(r"bytes 0-0/(\d+)")


class RangeUnsupported(Exception):
    """The server does not serve the file by ranges, so another way of downloading it is left."""


class _Failed(Exception):
    pass


def _allocate(path: str, size: int) -> None:
    with open(path, "wb") as handle:
        handle.truncate(size)


def _write_at(path: str, offset: int, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY)
    try:
        view = memoryview(data)
        while view:
            written = os.pwrite(descriptor, view, offset)
            view, offset = view[written:], offset + written
    finally:
        os.close(descriptor)


async def _probe(client: httpx.AsyncClient, url: str, headers: dict) -> int:
    """The size of the file, from a one-byte range; a server that sends the whole file instead is never read to the end."""
    try:
        async with client.stream("GET", url, headers={**headers, "Range": "bytes=0-0"}) as response:
            if response.status_code != 206:
                raise RangeUnsupported(reason("range_status", status=response.status_code))
            found = _CONTENT_RANGE.fullmatch(response.headers.get("content-range", "").strip())
            if found is None or int(found.group(1)) <= 0:
                raise RangeUnsupported(reason("range_no_size"))
            return int(found.group(1))
    except httpx.HTTPError as exc:
        raise _Failed(reason("network_error", cause=type(exc).__name__)) from exc


async def _fetch_part(client: httpx.AsyncClient, url: str, headers: dict, first: int, last: int,
                      progress: "_Progress") -> bytes:
    expected = last - first + 1

    async def ask() -> tuple[bytes | None, str]:
        """The part, or what went wrong with a try that may pass; a refusal that cannot pass ends the download at once."""
        got = 0
        try:
            async with client.stream("GET", url, headers={**headers, "Range": f"bytes={first}-{last}"}) as response:
                status = response.status_code
                if status == 206:
                    chunks = []
                    async for chunk in response.aiter_bytes():
                        chunks.append(chunk)
                        got += len(chunk)
                        await progress.add(len(chunk))
                    if got == expected:
                        return b"".join(chunks), ""
                    if got:
                        await progress.add(-got)
                    return None, reason("part_short", first=first, last=last, got=got, expected=expected)
                if status in _TRANSIENT_STATUSES or status >= 500:
                    return None, reason("part_refused", status=status, first=first, last=last)
                # A host that says a part is not there for good ends the download as the source's last word.
                refused = reason("part_not_served", status=status, first=first, last=last)
                raise _Failed(Final(refused) if status in GONE else refused)
        except httpx.HTTPError as exc:
            if got:
                await progress.add(-got)
            return None, reason("network_error", cause=type(exc).__name__)

    (data, problem), _attempts, _exhausted = await retrying(
        ask, final=lambda got: got[0] is not None,
        budget=Budget(attempts=PART_ATTEMPTS, pauses=PART_PAUSES_S, limit_s=UNTIMED))
    if data is None:
        raise _Failed(problem)
    return data


class _Progress:
    def __init__(self, total: int, on_progress: ProgressCb) -> None:
        self._total, self._on_progress = total, on_progress
        self._done, self._started, self._last = 0, time.monotonic(), 0.0

    async def add(self, size: int) -> None:
        self._done += size
        now = time.monotonic()
        if self._done < self._total and now - self._last < PROGRESS_INTERVAL_S:
            return
        self._last = now
        speed = self._done / max(now - self._started, 1e-6)
        await self._on_progress({"phase": "download", "progress": int(self._done * 100 / self._total),
                                 "eta": int((self._total - self._done) / speed) if speed > 0 else 0,
                                 "speed": 0.0, "dlspeed": int(speed),
                                 "total_size_mb": round(self._total / (1024 ** 2), 2)})


async def download_ranged(url: str, headers: dict, tmp_path: str, on_progress: ProgressCb, *, threads: int,
                          client: httpx.AsyncClient | None = None) -> tuple[bool, str]:
    """(True, "") with the whole file at `tmp_path`, or (False, why) with nothing left there; RangeUnsupported when the
    server cannot serve parts. The caller publishes the file."""
    owned = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                                         follow_redirects=True)
    try:
        with storage.hold(tmp_path):
            return await _fetch_all(client, url, headers, tmp_path, on_progress, threads)
    except _Failed as exc:
        await storage.discard(tmp_path)
        return False, exc.args[0]  # the typed words: a Final one must keep its kind
    except OSError as exc:  # the disk or the share refused a write
        await storage.discard(tmp_path)
        return False, reason("file_write_failed", cause=str(exc))
    except (RangeUnsupported, asyncio.CancelledError):
        await storage.discard(tmp_path)
        raise
    finally:
        if owned:
            await client.aclose()


async def _fetch_all(client: httpx.AsyncClient, url: str, headers: dict, tmp_path: str, on_progress: ProgressCb,
                     threads: int) -> tuple[bool, str]:
    total = await _probe(client, url, headers)
    await storage.call(_allocate, tmp_path, total)
    # The host answered with the size, so the link is good: a host that then sends no part fails the download, not the link.
    await on_progress({"phase": "download", "progress": 0, "eta": 0, "speed": 0.0,
                       "total_size_mb": round(total / (1024 ** 2), 2)})
    queue: asyncio.Queue[int] = asyncio.Queue()
    for first in range(0, total, PART_BYTES):
        queue.put_nowait(first)
    progress = _Progress(total, on_progress)
    writes: set[asyncio.Future] = set()

    async def write(first: int, data: bytes) -> None:
        # The hold of the whole block owns the name; a write cut short by a cancellation still runs to its end.
        job = asyncio.ensure_future(storage.call(_write_at, tmp_path, first, data))
        writes.add(job)
        job.add_done_callback(writes.discard)
        await asyncio.shield(job)

    async def work() -> None:
        while True:
            try:
                first = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            data = await _fetch_part(client, url, headers, first, min(first + PART_BYTES, total) - 1, progress)
            await write(first, data)

    tasks = [asyncio.ensure_future(work()) for _ in range(max(1, min(threads, MAX_WORKERS)))]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        # Every part is written once its worker ended without an error: the file was allocated at its whole size, so
        # its size tells nothing, and the length of the video is checked by the caller (ytdlp.download).
        for finished in done:
            if finished.exception() is not None:
                raise finished.exception()
        return True, ""
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(*writes, return_exceptions=True)
