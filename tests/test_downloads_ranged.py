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


@pytest.mark.parametrize("size", [1, 9, 10, 11, 25, 30, 101])
@pytest.mark.asyncio
async def test_the_bytes_of_the_file_are_the_bytes_of_the_server_at_any_size(tmp_path, size):
    payload = bytes(range(256)) * 2
    payload = payload[:size]

    (ok, error), out = await fetch(Server(payload), tmp_path)

    assert (ok, error) == (True, "")
    assert open(out, "rb").read() == payload


@pytest.mark.asyncio
async def test_the_parts_are_asked_for_with_the_headers_the_caller_gave_and_the_file_is_asked_once_in_each_range(tmp_path):
    server = Server(bytes(25))

    await fetch(server, tmp_path)

    assert sorted(server.seen) == ["bytes=0-0", "bytes=0-9", "bytes=10-19", "bytes=20-24"]


@pytest.mark.asyncio
async def test_a_server_without_ranges_is_told_apart_and_leaves_no_file(tmp_path):
    out = str(tmp_path / "part.mp4")

    with pytest.raises(ranged.RangeUnsupported):
        await ranged.download_ranged(URL, {}, out, AsyncMock(), threads=3, client=Server(bytes(25), ranges=False).client())

    assert not os.path.exists(out)


@pytest.mark.asyncio
async def test_a_part_that_fails_twice_is_asked_again_and_the_file_is_whole(tmp_path):
    payload = bytes(range(25))

    (ok, _), out = await fetch(Server(payload, fails={10: 2}), tmp_path)

    assert ok and open(out, "rb").read() == payload


@pytest.mark.asyncio
async def test_a_part_that_never_comes_ends_the_download_and_leaves_no_file_and_the_error_can_be_retried(tmp_path):
    from modules.downloads.module import _transient_download_error

    (ok, error), out = await fetch(Server(bytes(25), fails={10: 99}), tmp_path)

    assert not ok and "503" in error and _transient_download_error(error)
    assert not os.path.exists(out)


@pytest.mark.asyncio
async def test_a_part_that_comes_short_is_not_taken_for_whole(tmp_path):
    (ok, error), out = await fetch(Server(bytes(25), short=True), tmp_path)

    assert not ok and "из" in error
    assert not os.path.exists(out)


@pytest.mark.asyncio
async def test_a_refused_link_is_not_asked_again_and_a_link_that_expired_can_be_retried_by_the_caller(tmp_path):
    from modules.downloads.module import _transient_download_error

    server = Server(bytes(25), status={10: 403})

    (ok, error), out = await fetch(server, tmp_path)

    assert not ok and "403" in error and _transient_download_error(error)
    assert server.seen.count("bytes=10-19") == 1
    assert not os.path.exists(out)


@pytest.mark.asyncio
async def test_a_cancelled_download_leaves_no_file(tmp_path):
    server = Server(bytes(25))
    server.gate = asyncio.Event()
    out = str(tmp_path / "part.mp4")
    task = asyncio.ensure_future(ranged.download_ranged(URL, {}, out, AsyncMock(), threads=3, client=server.client()))
    await server.until_held(2)  # the parts after the first are in flight
    assert os.path.exists(out)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not os.path.exists(out)


@pytest.mark.asyncio
async def test_progress_grows_to_the_whole_file(tmp_path):
    progress: list[dict] = []

    await fetch(Server(bytes(95)), tmp_path, progress=progress)

    percents = [data["progress"] for data in progress]
    assert percents == sorted(percents) and percents[-1] == 100
    assert all(data["phase"] == "download" for data in progress)


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


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_read_after_the_parts_is_not_published(tmp_path):
    out = str(tmp_path / "ep.mp4")

    async def parts(url, headers, tmp, on_progress, *, threads, client=None):
        open(tmp, "wb").write(b"data")
        return True, ""

    with patch.object(ranged, "download_ranged", parts), patch.object(ytdlp, "_local_duration", AsyncMock(return_value=0.0)), \
            patch.object(ytdlp, "_announced_length", AsyncMock(return_value=60.0)):
        ok, error = await ytdlp.download(URL, out, noop, progressive=True)

    assert not ok and "не читается" in error
    assert not os.path.exists(out) and not os.path.exists(out + ".download.mp4")


@pytest.mark.asyncio
async def test_a_server_without_ranges_falls_back_to_one_stream_with_the_same_headers(tmp_path):
    out = str(tmp_path / "ep.mp4")

    async def published(url, headers, path, on_progress, announced=None):
        open(path, "wb").write(b"data")
        return True, ""

    with patch.object(ranged, "download_ranged", AsyncMock(side_effect=ranged.RangeUnsupported("no"))), \
            patch.object(ytdlp, "_announced_length", AsyncMock(return_value=60.0)), \
            patch.object(ytdlp, "_local_duration", AsyncMock(return_value=60.0)), \
            patch.object(ytdlp, "_download_direct_ffmpeg", AsyncMock(side_effect=published)) as ffmpeg:
        result = await ytdlp.download(URL, out, noop, headers={"Referer": "https://r/"}, progressive=True)

    assert result == (True, "")
    # The length the source announced is asked once and given to the way the file is read.
    ffmpeg.assert_awaited_once_with(URL, {"Referer": "https://r/"}, out, noop, 60.0)


@pytest.mark.asyncio
async def test_a_file_that_is_not_marked_progressive_takes_the_old_way(tmp_path):
    out = str(tmp_path / "ep.mp4")

    with patch.object(ranged, "download_ranged", AsyncMock()) as parts, \
            patch.object(ytdlp, "_download_direct_ffmpeg", AsyncMock(return_value=(True, ""))) as ffmpeg:
        await ytdlp.download("https://cdn.example.com/video.mp4", out, noop, headers={"Referer": "https://r/"})

    parts.assert_not_called()
    ffmpeg.assert_awaited_once()
