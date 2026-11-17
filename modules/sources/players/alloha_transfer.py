"""The segments of an episode of Alloha taken by its open session (alloha_session.py) into one file, in the order of the
playlist (prototype download.py): the playlist of the stream (its variant of the highest bandwidth), its init segment
and its segments by `connections` at once, each request with the headers the session holds at that moment.

The playlist must be whole (#EXT-X-ENDLIST), unencrypted and of whole segments; a segment must be media of the length the
host announced. A refusal ends the transfer at once, with what it says of the source: a segment or a playlist the host
has not (404, 410) is its last word, any other refusal is of the way. The file is written in order as the segments come;
no more than `WINDOW` segments are held ahead of the one the file waits for.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin

from core import storage
from core.reasons import reason

from .alloha_session import AllohaRefused, AllohaSession

ProgressCb = Callable[[dict], Awaitable[None]]
MB = 1024 * 1024
# Segments taken ahead of the one the file waits for, per connection: a slow segment does not hold the memory of an episode.
WINDOW_PER_CONNECTION = 4
_TEXT_TYPES = ("text/html", "application/json")
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass(frozen=True)
class Playlist:
    init: str | None
    segments: tuple[tuple[str, float], ...]  # (address, seconds)

    @property
    def seconds(self) -> float:
        return sum(each[1] for each in self.segments)

    @property
    def longest(self) -> float:
        return max((each[1] for each in self.segments), default=0.0)


def _attributes(line: str) -> dict[str, str]:
    return {key: value.strip('"') for key, value in _ATTRIBUTE.findall(line.split(":", 1)[1] if ":" in line else "")}


def variant_of(text: str, base: str) -> str | None:
    """The address of the variant of the highest bandwidth of a master playlist; None for a playlist of segments."""
    best, lines = None, text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("#EXT-X-STREAM-INF"):
            bandwidth = int(_attributes(line).get("BANDWIDTH", "0") or 0)
            uri = next((each.strip() for each in lines[index + 1:] if each.strip() and not each.startswith("#")), None)
            if uri and (best is None or bandwidth > best[0]):
                best = (bandwidth, urljoin(base, uri))
    return best[1] if best else None


def playlist_of(text: str, base: str) -> Playlist:
    """The init and the segments of a playlist of segments; a playlist this transfer cannot take is refused as final."""
    init, segments, duration, ended = None, [], None, False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXT-X-KEY") and _attributes(line).get("METHOD", "NONE") != "NONE":
            raise AllohaRefused(reason("alloha_playlist_encrypted"), final=True)
        if line.startswith("#EXT-X-BYTERANGE"):
            raise AllohaRefused(reason("alloha_playlist_parts"), final=True)
        if line.startswith("#EXT-X-MAP"):
            uri = urljoin(base, _attributes(line).get("URI", ""))
            if init is not None and uri != init:
                raise AllohaRefused(reason("alloha_playlist_init_changed"), final=True)
            init = uri
        elif line.startswith("#EXTINF"):
            duration = float(line.split(":", 1)[1].split(",", 1)[0] or 0)
        elif line.startswith("#EXT-X-ENDLIST"):
            ended = True
        elif line and not line.startswith("#"):
            segments.append((urljoin(base, line), duration or 0.0))
            duration = None
    if not ended or not segments:
        raise AllohaRefused(reason("alloha_playlist_unfinished"))
    return Playlist(init, tuple(segments))


async def _media(session: AllohaSession, url: str, client=None) -> bytes:
    response = await session._ok("GET", url, headers=session.media_headers(), client=client)
    kind = (response.headers.get("Content-Type") or "").lower()
    if any(each in kind for each in _TEXT_TYPES):
        raise AllohaRefused(reason("alloha_segment_answer", kind=kind))
    body = response.content
    announced = response.headers.get("Content-Length")
    if not body or (announced and not response.headers.get("Content-Encoding") and len(body) != int(announced)):
        raise AllohaRefused(reason("alloha_segment_length"))
    return body


def _append(path: str, chunks: list[bytes]) -> None:
    with open(path, "ab") as file:
        for chunk in chunks:
            file.write(chunk)


async def playlist(session: AllohaSession, url: str) -> Playlist:
    """The playlist of the stream as the session reads it, its variant of the highest bandwidth when it is a master."""
    for _ in range(4):
        response = await session._ok("GET", url, headers=session.media_headers())
        text = response.content.decode("utf-8-sig", errors="replace")
        if not text.lstrip().startswith("#EXTM3U"):
            raise AllohaRefused(reason("alloha_not_hls"))
        child = variant_of(text, str(response.url))
        if child is None:
            return playlist_of(text, str(response.url))
        url = child
    raise AllohaRefused(reason("alloha_playlist_nested"), final=True)


async def transfer(session: AllohaSession, listed: Playlist, target: str, connections: int,
                   on_progress: ProgressCb) -> None:
    """Every segment of the playlist into `target` (which must not exist), in order. Raises AllohaRefused."""
    count, started = len(listed.segments), time.monotonic()
    if session.error:
        raise AllohaRefused(session.error, profile_rejected=session.profile_rejected)
    if listed.init:
        await storage.call(_append, target, [await _media(session, listed.init)], owns=target)
    else:
        await storage.call(_append, target, [], owns=target)
    held: dict[int, bytes] = {}
    flushed, taken, got = 0, 0, 0
    turn = asyncio.Condition()
    window = max(1, connections) * WINDOW_PER_CONNECTION

    async def worker() -> None:
        client = session.media_client()  # one connection of the download, as the player's own are
        try:
            await take(client)
        finally:
            if client is not session.http:
                await client.close()

    async def take(client) -> None:
        nonlocal taken, got
        while True:
            async with turn:
                await turn.wait_for(lambda: taken >= count or taken < flushed + window)
                if taken >= count:
                    return
                index = taken
                taken += 1
            if session.error:
                raise AllohaRefused(session.error, profile_rejected=session.profile_rejected)
            body = await _media(session, listed.segments[index][0], client)
            session.bytes_loaded += len(body)
            got += len(body)
            async with turn:
                held[index] = body
                turn.notify_all()

    async def writer() -> None:
        nonlocal flushed
        while flushed < count:
            async with turn:
                await turn.wait_for(lambda: flushed in held)
                ready = []
                while flushed + len(ready) in held:
                    ready.append(held.pop(flushed + len(ready)))
            await storage.call(_append, target, ready, owns=target)
            async with turn:
                flushed += len(ready)
                turn.notify_all()
            seconds = time.monotonic() - started
            speed = got / seconds if seconds > 0 else 0.0
            total = got * count / taken if taken else 0
            await on_progress({"phase": "download", "progress": int(flushed * 100 / count),
                               "total_size_mb": round(total / MB, 2), "dlspeed": int(speed),
                               "eta": int((total - got) / speed) if speed and total > got else 0})

    tasks = [asyncio.ensure_future(worker()) for _ in range(max(1, min(connections, count)))]
    tasks.append(asyncio.ensure_future(writer()))
    try:
        # The first refusal ends the transfer: the others are stopped, not waited out.
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            if task.exception() is not None:
                raise task.exception()
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    if flushed != count:
        raise AllohaRefused(reason("alloha_segments_taken", taken=flushed, count=count))

