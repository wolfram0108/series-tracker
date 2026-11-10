"""The frame of an HLS stream, read from its picture: the playlist (the variant of the highest bandwidth when it is a master
one), the head of its first segment, decrypted when the playlist names an AES-128 key, given to ffprobe.

A host may say nothing true of the frame of what it serves (the label of a list, the name of a line of an application), so
the players whose streams are playlists measure it here, one way for all of them.
"""
from __future__ import annotations

import asyncio
import re
import weakref
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.ffprobe import film_stream, streams_of

from .contract import Answer, Kind, Reading
from .http import USER_AGENT, get_answer

REQUEST_TIMEOUT_S = 20.0
# Enough of a segment to hold its first key frame with the parameters of the picture; the rest of it is not fetched.
HEAD_BYTES = 256 * 1024
MEASURE_TIMEOUT_S = 30.0
# The pictures read by ffprobe at once in the whole process, whichever player asks: each is a program of tens of megabytes,
# and a card of thousands of episodes asked at once would run them by the hundred on a server of 4 GB.
MEASURE_GATE = 6
_gates: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = weakref.WeakKeyDictionary()


def ffprobe_gate() -> asyncio.Semaphore:
    """The gate of the pictures read at once in this process (one to the loop that runs it)."""
    loop = asyncio.get_running_loop()
    if loop not in _gates:
        _gates[loop] = asyncio.Semaphore(MEASURE_GATE)
    return _gates[loop]

_ATTRIBUTE = re.compile(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)')


@dataclass(frozen=True)
class Head:
    """What the picture of a playlist is read from: its first segment, how it is encrypted and its initialisation."""
    segment: str
    sequence: int
    key: str = ""
    iv: bytes | None = None
    init: str = ""
    method: str = "NONE"


class MeasureError(RuntimeError):
    """A request of the measure got no picture: the answer it got, for the policy to judge."""

    def __init__(self, answer: Answer) -> None:
        super().__init__(answer.error or str(answer.status))
        self.answer = answer


Measure = Callable[[httpx.AsyncClient, Head, str], Awaitable[int | None]]


def attributes(line: str) -> dict[str, str]:
    return {name: value.strip('"') for name, value in _ATTRIBUTE.findall(line.split(":", 1)[1] if ":" in line else "")}


def is_playlist(text: str) -> bool:
    return text.lstrip("﻿").lstrip().startswith("#EXTM3U")


def best_variant(text: str, base: str) -> str | None:
    """The variant of a master playlist with the highest bandwidth; None for a media playlist."""
    lines = text.splitlines()
    variants = [(int(attributes(line).get("BANDWIDTH") or 0), urljoin(base, uri.strip()))
                for line, uri in zip(lines, lines[1:]) if line.startswith("#EXT-X-STREAM-INF") and uri.strip()
                and not uri.startswith("#")]
    return max(variants)[1] if variants else None


def head_of(text: str, base: str) -> Head | None:
    """The first segment of a media playlist with the key and the initialisation that apply to it; None without one."""
    sequence, key, iv, init, method = 0, "", None, "", "NONE"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1] or 0)
        elif line.startswith("#EXT-X-KEY:"):
            found = attributes(line)
            method = found.get("METHOD", "NONE")
            key = urljoin(base, found["URI"]) if found.get("URI") else ""
            iv = bytes.fromhex(found["IV"][2:].zfill(32)) if found.get("IV", "").lower().startswith("0x") else None
        elif line.startswith("#EXT-X-MAP:"):
            init = urljoin(base, attributes(line).get("URI", ""))
        elif line and not line.startswith("#"):
            return Head(urljoin(base, line), sequence, key, iv, init, method)
    return None


def _headers(referer: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {"User-Agent": USER_AGENT, **({"Referer": referer} if referer else {}), **(extra or {})}


async def _bytes(http: httpx.AsyncClient, url: str, referer: str, limit: int | None = None) -> bytes:
    headers = _headers(referer, {"Range": f"bytes=0-{limit - 1}"} if limit else None)
    try:
        response = await http.get(url, headers=headers, follow_redirects=True, timeout=REQUEST_TIMEOUT_S)
    except httpx.HTTPError as exc:
        raise MeasureError(Answer(None, error=type(exc).__name__)) from exc
    if response.status_code not in (200, 206):
        raise MeasureError(Answer(response.status_code, "", final_url=str(response.url)))
    return response.content[:limit] if limit else response.content


def decrypted(data: bytes, key: bytes, iv: bytes) -> bytes:
    """The head of an AES-128 segment: CBC decrypts any whole blocks from the start without the rest of the segment."""
    whole = data[: len(data) // 16 * 16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(whole) + decryptor.finalize()


async def measure_head(http: httpx.AsyncClient, head: Head, referer: str) -> int | None:
    """The frame height of the film in the head of the first segment; None when the picture cannot be read from it."""
    data = await _bytes(http, head.segment, referer, HEAD_BYTES)
    if head.method == "AES-128":
        key = await _bytes(http, head.key, referer)
        data = decrypted(data, key, head.iv if head.iv is not None else head.sequence.to_bytes(16, "big"))
    if head.init:
        data = await _bytes(http, head.init, referer) + data
    async with ffprobe_gate():
        streams = await streams_of("pipe:0", input_args=[] if head.init else ["-f", "mpegts"], data=data,
                                   timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    try:
        return int(film["height"]) if film is not None else None
    except (KeyError, TypeError, ValueError):
        return None


async def measured(http: httpx.AsyncClient, playlist: str, referer: str, measure: Measure | None,
                   headers: dict[str, str] | None = None) -> Answer:
    """What a playlist is: a stream whose frame is measured (with `measure`; without it only seen to be there), or the
    answer that says it is not. The reading names the playlist as the one file of the stream."""
    answer = await get_answer(http, playlist, referer, headers=_headers(referer, headers), timeout=REQUEST_TIMEOUT_S)
    if answer.status != 200 or not is_playlist(answer.text):
        return answer
    media, base = answer.text, answer.final_url or playlist
    variant = best_variant(media, base)
    if variant is not None:
        answer = await get_answer(http, variant, referer, headers=_headers(referer, headers), timeout=REQUEST_TIMEOUT_S)
        if answer.status != 200 or not is_playlist(answer.text):
            return answer
        media, base = answer.text, answer.final_url or variant
    head = head_of(media, base)
    if head is None:
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="the playlist lists no segment"))
    if head.method not in ("NONE", "AES-128"):
        # A segment whose samples are encrypted (SAMPLE-AES) shows no picture from its head: no file to take.
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=f"encryption {head.method}"))
    if measure is None:
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, playlist),)))
    try:
        height = await measure(http, head, referer)
    except MeasureError as exc:
        return exc.answer
    if not height:
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no picture in the head of the first segment"))
    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (height,), files=((height, playlist),)))
