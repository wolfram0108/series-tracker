"""An HLS playlist a player resolved, downloaded the way the prototypes of the video applications do it: our client reads
the playlist (a host that refuses the requests of yt-dlp gives it to us), the variant of the highest frame, then of the
highest bandwidth, is taken, its addresses made absolute and the playlist saved on disk; the native HLS downloader of
yt-dlp then fetches its segments from that file (modules/downloads/hls_worker.py) in a process of its own.

The playlist must be whole (#EXT-X-ENDLIST) and its encryption one that is decrypted (none or AES-128). The length of the
stream is the sum of the durations it states, and a file may fall short of it by up to the longest segment.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urljoin

import httpx

from core import storage
from core.http_limits import bounded_client
from core.proc import exited, spawn, stop

ProgressCb = Callable[[dict], Awaitable[None]]

REQUEST_TIMEOUT_S = 30.0
# A worker that prints nothing for this long is stuck (a fragment is retried well within it).
SILENCE_S = 180.0
MB = 1024 * 1024
_URI = re.compile(r'URI="([^"]*)"')
_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')
ROOT = str(Path(__file__).resolve().parents[2])


class PlaylistError(Exception):
    """Why the playlist cannot be downloaded, in words for the card and the log."""


@dataclass(frozen=True)
class Snapshot:
    manifest: str   # the file:// address of the playlist saved on disk
    seconds: float  # the length the playlist states
    tolerance: float  # the longest segment: what a whole file may fall short of the length by


def _attributes(line: str) -> dict[str, str]:
    return {name: value.strip('"') for name, value in _ATTRIBUTE.findall(line.split(":", 1)[1] if ":" in line else "")}


def _variants(text: str, base: str) -> list[tuple[int, int, str]]:
    """(height, bandwidth, address) of each variant of a master playlist; empty for a media playlist."""
    lines = [line.strip() for line in text.splitlines()]
    found = []
    for line, uri in zip(lines, lines[1:]):
        if line.startswith("#EXT-X-STREAM-INF") and uri and not uri.startswith("#"):
            found_attributes = _attributes(line)
            resolution = found_attributes.get("RESOLUTION", "0x0").lower().split("x")
            height = int(resolution[1]) if len(resolution) == 2 and resolution[1].isdigit() else 0
            found.append((height, int(found_attributes.get("BANDWIDTH") or 0), urljoin(base, uri)))
    return found


def absolute(text: str, base: str) -> tuple[str, float, float]:
    """A media playlist with every address absolute, the length it states and its longest segment."""
    out, seconds, longest, methods, segments = [], 0.0, 0.0, set(), 0
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            duration = float(line.split(":", 1)[1].split(",")[0] or 0)
            seconds += duration
            longest = max(longest, duration)
        elif line.startswith("#EXT-X-KEY:"):
            methods.add(_attributes(line).get("METHOD", "NONE"))
        if line.startswith(("#EXT-X-KEY:", "#EXT-X-MAP:")):
            line = _URI.sub(lambda found: f'URI="{urljoin(base, found.group(1))}"', line)
        elif line and not line.startswith("#"):
            line = urljoin(base, line)
            segments += 1
        out.append(line)
    if not segments:
        raise PlaylistError("плейлист: в нём нет сегментов")
    if "#EXT-X-ENDLIST" not in out:
        raise PlaylistError("плейлист не закончен (нет #EXT-X-ENDLIST): это не запись, а трансляция")
    unsupported = methods - {"NONE", "AES-128"}
    if unsupported:
        raise PlaylistError(f"плейлист: шифрование {', '.join(sorted(unsupported))} не расшифровывается")
    return "\n".join(out) + "\n", seconds, longest


async def snapshot(url: str, headers: dict, path: str) -> Snapshot:
    """The playlist of the stream read by our client and saved next to the file it becomes (`<path>.download.m3u8`)."""
    async with bounded_client(follow_redirects=True, timeout=REQUEST_TIMEOUT_S) as http:
        base = url
        for _ in range(4):
            try:
                response = await http.get(base, headers=headers)
            except httpx.HTTPError as exc:
                raise PlaylistError(f"плейлист не получен: {type(exc).__name__}") from exc
            if response.status_code != 200:
                raise PlaylistError(f"плейлист не получен: статус {response.status_code}")
            text, base = response.text, str(response.url)
            if not text.lstrip("﻿").lstrip().startswith("#EXTM3U"):
                raise PlaylistError("по адресу потока не плейлист HLS")
            variants = _variants(text, base)
            if not variants:
                break
            base = max(variants)[2]
        else:
            raise PlaylistError("плейлист: вложенность вариантов глубже четырёх")
    manifest, seconds, longest = absolute(text, base)
    file = path + ".download.m3u8"
    await storage.call(_write, file, manifest, owns=file)
    return Snapshot(Path(file).as_uri(), seconds, longest)


def _write(path: str, text: str) -> None:
    with open(path, "w", encoding="utf-8") as written:
        written.write(text)


async def fetch(snap: Snapshot, headers: dict, target: str, fragments: int, on_progress: ProgressCb) -> tuple[bool, str]:
    """The segments of the saved playlist into `target` by the worker; (False, why) when it did not take them all."""
    proc = await spawn(sys.executable, "-m", "modules.downloads.hls_worker", cwd=ROOT,
                       stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    proc.stdin.write((json.dumps({"url": snap.manifest, "target": target, "headers": headers,
                                  "fragments": fragments}) + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()
    failure, errors, code, transferred = "", [], None, False
    started = time.monotonic()
    try:
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), SILENCE_S)
            except asyncio.TimeoutError:
                failure = f"загрузчик сегментов: нет прогресса {SILENCE_S:.0f} с (timeout)"
                break
            if not line:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("type") == "progress":
                await on_progress(_figures(event, time.monotonic() - started))
            elif event.get("type") == "error":
                errors.append(event.get("message", ""))
            elif event.get("type") == "failed":
                failure = event.get("error", "")
            elif event.get("type") == "transferred":
                transferred = True
        if not failure:
            code = await exited(proc)
    finally:
        if code is None:
            code = await stop(proc)
    # The worker tells that every segment is in the file; its code is no proof alone (a lost status says nothing).
    if failure or not transferred:
        return False, "загрузчик сегментов: " + ("; ".join(errors[-2:]) or failure or f"код {code}")
    return True, ""


def _figures(event: dict, seconds: float) -> dict:
    index, count = event.get("fragment_index") or 0, event.get("fragment_count") or 0
    got, speed = event.get("downloaded_bytes") or 0, event.get("speed") or 0
    progress = int(index * 100 / count) if count else 0
    total = got * count / index if index else 0
    return {"phase": "download", "progress": progress, "total_size_mb": round(total / MB, 2) if total else 0,
            "dlspeed": int(speed), "eta": int((total - got) / speed) if speed and total > got else 0}
