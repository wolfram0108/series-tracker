"""The frame of an HLS stream, read from its picture: the playlist (the variant of the highest bandwidth when it is a master
one), the head of the first segment of its film (a block a host put before it from elsewhere is no part of the film, as
the downloader has it: core/hls_playlist.py), decrypted when the playlist names an AES-128 key, given to ffprobe. A
segment that is a whole file (a playlist of one MP4) is read as a file, by its address. A film whose first segment shows
no picture is a file its store has damaged: the answer says what the host served for it.

A host may say nothing true of the frame of what it serves (the label of a list, the name of a line of an application), so
the players whose streams are playlists measure it here, one way for all of them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlsplit

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.ffprobe import film_stream, probe, streams_of
from core.hls_playlist import without_insertions

from .contract import Answer, Kind, Reading
from .http import USER_AGENT, get_answer, retry_after

REQUEST_TIMEOUT_S = 20.0
# A host that takes no connection within this long takes none, however long the answer of one that did may be waited for:
# a silent host costs a read this much an ask, not the whole wait for an answer.
CONNECT_TIMEOUT_S = 8.0


def waiting(seconds: float) -> httpx.Timeout:
    """The wait for an answer of a host of a stream: `seconds` for what it sends, `CONNECT_TIMEOUT_S` for its connection."""
    return httpx.Timeout(seconds, connect=CONNECT_TIMEOUT_S)


# Enough of a segment to hold its first key frame with the parameters of the picture; the rest of it is not fetched.
HEAD_BYTES = 256 * 1024
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


async def _served(http: httpx.AsyncClient, url: str, referer: str, limit: int | None = None,
                  headers: dict[str, str] | None = None) -> tuple[bytes, str]:
    """The body a host serves at an address (its head when `limit` is given) and the type it announces. `headers` are
    those of the stream: its segments, its key and its initialisation are asked as its playlist is."""
    sent = _headers(referer, {**(headers or {}), **({"Range": f"bytes=0-{limit - 1}"} if limit else {})})
    try:
        response = await http.get(url, headers=sent, follow_redirects=True, timeout=waiting(REQUEST_TIMEOUT_S))
    except httpx.HTTPError as exc:
        raise MeasureError(Answer(None, error=f"{type(exc).__name__}: {urlsplit(url).hostname}")) from exc
    if response.status_code not in (200, 206):
        raise MeasureError(Answer(response.status_code, "", retry_after(response), final_url=str(response.url)))
    return (response.content[:limit] if limit else response.content), response.headers.get("content-type", "")


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
    data, kind = await _served(http, head.segment, referer, HEAD_BYTES, headers)
    size = len(data)
    if not head.init and container_of(data) not in (None, "mpegts"):
        # A playlist may list a whole file as its segment (an MP4, its index at its end): its picture is read as a
        # file's, by its address, not from the head of a transport stream.
        sent = _headers(referer, headers)
        agent = next(value for name, value in sent.items() if name.lower() == "user-agent")
        lines = "".join(f"{name}: {value}\r\n" for name, value in sent.items() if name.lower() != "user-agent")
        streams, _said = await probe(head.segment, input_args=[
            "-user_agent", agent, *(["-headers", lines] if lines else [])], timeout=MEASURE_TIMEOUT_S)
        film = film_stream(streams or [])
        if film is not None and film.get("height"):
            return int(film["height"])
    if head.method == "AES-128":
        key = await _bytes(http, head.key, referer, headers=headers)
        data = decrypted(data, key, head.iv if head.iv is not None else head.sequence.to_bytes(16, "big"))
    if head.init:
        data = await _bytes(http, head.init, referer, headers=headers) + data
    streams = await streams_of("pipe:0", input_args=[] if head.init else ["-f", "mpegts"], data=data,
                               timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    try:
        height = int(film["height"]) if film is not None else None
    except (KeyError, TypeError, ValueError):
        height = None
    if not height:
        # The host answered and what it serves is no video: its final word on the file, the same at every ask.
        raise MeasureError(Answer(200, reading=Reading(Kind.NOT_FOUND, 200, message=(
            f"the host {urlsplit(head.segment).hostname} served no picture in the segment: {kind or 'no type'}, "
            f"{size} bytes"))))
    return height


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
