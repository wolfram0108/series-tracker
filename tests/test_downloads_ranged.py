"""A ready file downloaded in parts at once: the bytes, the failures of a part, the server without ranges, the route."""
import asyncio
import os
import re
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from modules.downloads import ranged, ytdlp

URL = "https://cdn.test/file?sig=1"


@pytest.fixture(autouse=True)
def small_parts_and_no_pauses(monkeypatch):
    monkeypatch.setattr(ranged, "PART_BYTES", 10)
    monkeypatch.setattr(ranged, "PART_PAUSES_S", (0.0,))
    monkeypatch.setattr(ranged, "PROGRESS_INTERVAL_S", 0.0)


class Server:
    """A CDN that serves `payload` by ranges; `fails` says how many answers a part gets wrong before it is served."""

    def __init__(self, payload: bytes, *, ranges: bool = True, fails: dict[int, int] | None = None,
                 short: bool = False, status: dict[int, int] | None = None) -> None:
        self.payload, self.ranges, self.fails, self.short, self.status = payload, ranges, dict(fails or {}), short, dict(status or {})
        self.seen: list[str | None] = []
        self.gate: asyncio.Event | None = None
        self.held = 0  # requests waiting at the gate
        self._held_changed = asyncio.Event()

    async def until_held(self, count: int) -> None:
        """Returns when `count` requests wait at the gate; the CDN says so itself, nothing is waited for by time."""
        while self.held < count:
            self._held_changed.clear()
            await asyncio.wait_for(self._held_changed.wait(), 5)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        header = request.headers.get("range")
        self.seen.append(header)
        if not self.ranges:
            return httpx.Response(200, content=self.payload)
        first, last = (int(number) for number in re.fullmatch(r"bytes=(\d+)-(\d+)", header).groups())
        if self.gate is not None and first > 0:
            self.held += 1
            self._held_changed.set()
            await self.gate.wait()
        if first in self.status:
            return httpx.Response(self.status[first])
        if self.fails.get(first, 0) > 0:
            self.fails[first] -= 1
            return httpx.Response(503)
        last = min(last, len(self.payload) - 1)
        body = self.payload[first:last + 1]
        return httpx.Response(206, content=body[:-1] if self.short and first > 0 else body,
                              headers={"content-range": f"bytes {first}-{last}/{len(self.payload)}"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def fetch(server: Server, tmp_path, threads: int = 3, progress: list | None = None):
    async def on_progress(data):
        if progress is not None:
            progress.append(data)

    out = str(tmp_path / "part.mp4")
    result = await ranged.download_ranged(URL, {"X-Test": "1"}, out, on_progress, threads=threads, client=server.client())
    return result, out


@pytest.mark.asyncio
async def test_a_part_that_fails_twice_is_asked_again_and_the_file_is_whole(tmp_path):
    payload = bytes(range(25))

    (ok, _), out = await fetch(Server(payload, fails={10: 2}), tmp_path)

    assert ok and open(out, "rb").read() == payload


@pytest.mark.asyncio
async def test_a_refused_link_is_not_asked_again_and_a_link_that_expired_can_be_retried_by_the_caller(tmp_path):
    from modules.downloads.failure import Failed, kind_of

    server = Server(bytes(25), status={10: 403})

    (ok, error), out = await fetch(server, tmp_path)

    assert not ok and "403" in error and kind_of(error) is Failed.BROKEN
    assert server.seen.count("bytes=10-19") == 1
    assert not os.path.exists(out)


# --- the route ------------------------------------------------------------------------------------------------------

async def noop(_data):
    pass


@pytest.mark.asyncio
async def test_a_progressive_file_is_downloaded_in_parts_and_published_under_its_name(tmp_path):
    out = str(tmp_path / "ep.mp4")

    async def parts(url, headers, tmp, on_progress, *, threads, client=None):
        assert (url, headers, tmp, threads) == (URL, {}, out + ".download.mp4", 4)
        open(tmp, "wb").write(b"data")
        return True, ""

    with patch.object(ranged, "download_ranged", parts), patch.object(ytdlp, "_local_duration", AsyncMock(return_value=60.0)), \
            patch.object(ytdlp, "_announced_length", AsyncMock(return_value=60.0)), \
            patch.object(ytdlp, "_download_direct_ffmpeg") as ffmpeg:
        result = await ytdlp.download(URL, out, noop, threads=4, progressive=True)

    assert result == (True, "")
    assert open(out, "rb").read() == b"data" and not os.path.exists(out + ".download.mp4")
    ffmpeg.assert_not_called()
