"""The frame of an HLS stream, read from its picture: the playlist (the variant of the highest bandwidth when it is a master
one), the head of the first segment of its film (a block a host put before it from elsewhere is no part of the film, as
the downloader has it: core/hls_playlist.py), decrypted when the playlist names an AES-128 key, given to ffprobe. A
segment that is a whole file (a playlist of one MP4) is measured from its bounded header first, with a seekable network
probe as a fallback. When a prefix has no picture, a larger bounded prefix is tried. An exhausted prefix or a failed probe is inconclusive; a complete small body with no
picture is reported as missing media.

A host may say nothing true of the frame of what it serves (the label of a list, the name of a line), so
the players whose streams are playlists measure it here, one way for all of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.ffprobe import film_stream, probe
from core.hls_playlist import without_insertions

from .contract import Answer, Kind, Reading
from .http import USER_AGENT, failure_words, get_answer, retry_after

REQUEST_TIMEOUT_S = 20.0
# A host that takes no connection within this long takes none, however long the answer of one that did may be waited for:
# a silent host costs a read this much an ask, not the whole wait for an answer.
CONNECT_TIMEOUT_S = 8.0


def waiting(seconds: float) -> httpx.Timeout:
    """The wait for an answer of a host of a stream: `seconds` for what it sends, `CONNECT_TIMEOUT_S` for its connection."""
    return httpx.Timeout(seconds, connect=CONNECT_TIMEOUT_S)


# Enough of a segment to hold its first key frame with the parameters of the picture; the rest of it is not fetched.
HEAD_BYTES = 256 * 1024
MAX_HEAD_BYTES = 1024 * 1024
# Leading MP4 track metadata may be larger than a TS prefix, but never read an arbitrary movie-sized box.
FILE_HEADER_BYTES = 8 * 1024 * 1024
MEASURE_TIMEOUT_S = 30.0

TS_PACKET = 188


def container_of(head: bytes) -> str | None:
    """The container a file starts with, by its signature; None when the bytes start no media file."""
    if head[4:8] == b"ftyp":
        return "mp4"
    if head[:1] == b"\x47" and (len(head) <= TS_PACKET or head[TS_PACKET:TS_PACKET + 1] == b"\x47"):
        return "mpegts"
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"
    if head[:3] == b"FLV":
        return "flv"
    return None


def file_head_size(head: bytes, minimum: int) -> int:
    """A bounded file prefix: a leading MP4 index, or enough Matroska/TS data to reach the video headers."""
    container = container_of(head)
    if container in ("matroska", "mpegts"):
        return max(minimum, MAX_HEAD_BYTES)
    if container != "mp4":
        return minimum
    pos = 0
    while pos + 8 <= len(head):
        size, kind = int.from_bytes(head[pos:pos + 4], "big"), head[pos + 4:pos + 8]
        header = 8
        if size == 1:
            if pos + 16 > len(head):
                break
            size, header = int.from_bytes(head[pos + 8:pos + 16], "big"), 16
        if size < header:
            break
        end = pos + size
        if kind == b"moov":
            return max(minimum, end) if end <= FILE_HEADER_BYTES else minimum
        if kind == b"mdat" or end > len(head):
            break
        pos = end
    return minimum


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


# The measure of a head of a segment: (client, head, referer, the headers of the stream) -> the height of its frame.
Measure = Callable[[httpx.AsyncClient, Head, str, "dict[str, str] | None"], Awaitable[int | None]]


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


def heads(text: str, base: str) -> list[Head]:
    """Every segment of a media playlist with the key, the initialisation and the number that apply to it."""
    sequence, key, iv, init, method, found = 0, "", None, "", "NONE", []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            sequence = int(line.split(":", 1)[1] or 0)
        elif line.startswith("#EXT-X-KEY:"):
            named = attributes(line)
            method = named.get("METHOD", "NONE")
            key = urljoin(base, named["URI"]) if named.get("URI") else ""
            iv = bytes.fromhex(named["IV"][2:].zfill(32)) if named.get("IV", "").lower().startswith("0x") else None
        elif line.startswith("#EXT-X-MAP:"):
            init = urljoin(base, attributes(line).get("URI", ""))
        elif line and not line.startswith("#"):
            found.append(Head(urljoin(base, line), sequence + len(found), key, iv, init, method))
    return found


def head_of(text: str, base: str) -> Head | None:
    """The first segment of a media playlist with the key and the initialisation that apply to it; None without one."""
    return next(iter(heads(text, base)), None)


def _headers(referer: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Ours, and over them those of the stream: a name is one header whatever its case."""
    sent = {"User-Agent": USER_AGENT, **({"Referer": referer} if referer else {})}
    for name, value in (extra or {}).items():
        sent = {held: each for held, each in sent.items() if held.lower() != name.lower()}
        sent[name] = value
    return sent


def probe_args(headers: dict[str, str]) -> list[str]:
    """The headers of a stream as ffprobe sends them: its agent once (the stream's own, else ours), the rest as lines."""
    agent = next((value for name, value in headers.items() if name.lower() == "user-agent"), USER_AGENT)
    lines = "".join(f"{name}: {value}\r\n" for name, value in headers.items() if name.lower() != "user-agent")
    return ["-user_agent", agent, *(["-headers", lines] if lines else [])]


async def _served(http: httpx.AsyncClient, url: str, referer: str, limit: int | None = None,
                  headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    """The body a host serves at an address (its head when `limit` is given) and the type it announces. `headers` are
    those of the stream: its segments, its key and its initialisation are asked as its playlist is."""
    sent = _headers(referer, {**(headers or {}), **({"Range": f"bytes=0-{limit - 1}"} if limit else {})})
    try:
        async with http.stream("GET", url, headers=sent, follow_redirects=True,
                               timeout=waiting(REQUEST_TIMEOUT_S)) as response:
            if response.status_code not in (200, 206):
                raise MeasureError(Answer(response.status_code, "", retry_after(response), final_url=str(response.url)))
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk if limit is None else chunk[:max(0, limit - len(body))])
                if limit is not None and len(body) >= limit:
                    break
            return bytes(body), response.headers.get("content-type", "")
    except httpx.HTTPError as exc:
        raise MeasureError(Answer(None, error=failure_words(exc, url))) from exc


async def _bytes(http: httpx.AsyncClient, url: str, referer: str, limit: int | None = None,
                 headers: dict[str, str] | None = None) -> bytes:
    return (await _served(http, url, referer, limit, headers))[0]


def decrypted(data: bytes, key: bytes, iv: bytes) -> bytes:
    """The head of an AES-128 segment: CBC decrypts any whole blocks from the start without the rest of the segment."""
    whole = data[: len(data) // 16 * 16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(whole) + decryptor.finalize()


async def measure_head(http: httpx.AsyncClient, head: Head, referer: str,
                       headers: dict[str, str] | None = None) -> int | None:
    """The frame height of the film in the head of a segment. A segment that holds no picture is the host's answer about
    the video (a store that took a part of a file away serves a stub of a few hundred bytes for it): the error says what
    the host served. Every request of the measure carries the headers of the stream, as the download of it does: a host
    that wants them for the playlist wants them for the segment."""
    key, init = None, None
    for limit in (HEAD_BYTES, MAX_HEAD_BYTES):
        data, kind = await _served(http, head.segment, referer, limit, headers)
        size = len(data)
        whole_file = head.method == "NONE" and not head.init and container_of(data) not in (None, "mpegts")
        if whole_file:
            # Some playlists contain a complete MP4 rather than TS fragments. Its leading index can be measured
            # locally too; the network probe is only needed when that bounded prefix cannot describe the video.
            wanted = file_head_size(data, len(data))
            if wanted > len(data):
                extra = 65536 if container_of(data) == "mp4" else 0
                data, _kind = await _served(http, head.segment, referer, min(FILE_HEADER_BYTES, wanted + extra), headers)
            streams, said = await probe("pipe:0", data=data, timeout=MEASURE_TIMEOUT_S)
            film = film_stream(streams or [])
            if film is not None and film.get("height"):
                return int(film["height"])
            # A complete file can have its index at the end: preserve the seekable network fallback.
            streams, said = await probe(head.segment, input_args=probe_args(_headers(referer, headers)),
                                        timeout=MEASURE_TIMEOUT_S)
        else:
            if head.method == "AES-128":
                if key is None:
                    key = await _bytes(http, head.key, referer, headers=headers)
                if len(key) != 16:
                    raise MeasureError(Answer(200, reading=Reading(Kind.NO_FILE, 200, message="invalid AES-128 key")))
                data = decrypted(data, key, head.iv if head.iv is not None else head.sequence.to_bytes(16, "big"))
            if head.init:
                if init is None:
                    init = await _bytes(http, head.init, referer, headers=headers)
                data = init + data
            streams, said = await probe("pipe:0", input_args=[] if head.init else ["-f", "mpegts"], data=data,
                                        timeout=MEASURE_TIMEOUT_S)
        film = film_stream(streams or [])
        if film is not None and film.get("height"):
            return int(film["height"])
        if streams is None:
            # A failed probe is no proof that the host's video is absent.
            raise MeasureError(Answer(None, error=said.replace(head.segment, urlsplit(head.segment).hostname or "")))
        if whole_file:
            raise MeasureError(Answer(200, reading=Reading(Kind.NO_FILE, 200, message=(
                said.replace(head.segment, urlsplit(head.segment).hostname or "") or "no picture in the file"))))
        if size < limit:
            break
        # A full prefix without a video header is inconclusive: ask for a larger, still bounded prefix of this segment.
    state = Kind.NOT_FOUND if size < limit else Kind.NO_FILE
    raise MeasureError(Answer(200, reading=Reading(state, 200, message=(
        f"the host {urlsplit(head.segment).hostname} served no picture in the segment: {kind or 'no type'}, "
        f"{size} bytes"))))


async def measured(http: httpx.AsyncClient, playlist: str, referer: str, measure: Measure | None,
                   headers: dict[str, str] | None = None, fetched: Answer | None = None) -> Answer:
    """What a playlist is: a stream whose frame is measured (with `measure`; without it only seen to be there), or the
    answer that says it is not. The reading names the playlist as the one file of the stream. `fetched` is the playlist
    when the caller already has it: a host that makes the playlist anew at every request is asked for it once."""
    answer = fetched if fetched is not None else await get_answer(
        http, playlist, referer, headers=_headers(referer, headers), timeout=waiting(REQUEST_TIMEOUT_S))
    if answer.status != 200 or not is_playlist(answer.text):
        return answer
    media, base = answer.text, answer.final_url or playlist
    variant = best_variant(media, base)
    if variant is not None:
        answer = await get_answer(http, variant, referer, headers=_headers(referer, headers), timeout=waiting(REQUEST_TIMEOUT_S))
        if answer.status != 200 or not is_playlist(answer.text):
            return answer
        media, base = answer.text, answer.final_url or variant
    # The film is what the downloader would save: a block a host put before it from elsewhere is not measured for it.
    found = heads(without_insertions(media, base)[0], base)
    if not found:
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="the playlist lists no segment"))
    if found[0].method not in ("NONE", "AES-128"):
        # A segment whose samples are encrypted (SAMPLE-AES) shows no picture from its head: no file to take.
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message=f"encryption {found[0].method}"))
    if measure is None:
        return Answer(200, reading=Reading(Kind.PLAYABLE, 200, files=((0, playlist),)))
    try:
        height = await measure(http, found[0], referer, headers)
    except MeasureError as exc:
        return exc.answer
    if not height:
        return Answer(200, reading=Reading(Kind.NO_FILE, 200, message="no picture in the head of the first segment"))
    return Answer(200, reading=Reading(Kind.PLAYABLE, 200, (height,), files=((height, playlist),)))
