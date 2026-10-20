"""Vibix/Kinescope protocol primitives: envelope codec and playlist parsing."""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, urlparse

# Static key from the publisher's embed bundle; override when the bundle rotates it.
DEFAULT_DECODER_KEY = "RySdvcyu5iTUxn97vn4HwoniwgxaCynA"

_QUALITY_CHUNK = re.compile(r"^\[(\d+p|4k)\]\{[^}]*\}(https?://.+)$", re.IGNORECASE)
_EPISODE_NUMBER = re.compile(r"\d+")
_ALL_EPS = re.compile(r"const ALL_EPS\s*=\s*(\[.*?\]);", re.DOTALL)


class VibixProtocolError(RuntimeError):
    """Raised when a Vibix response cannot be decoded or interpreted."""


@dataclass(frozen=True)
class Stream:
    quality: int
    url: str


@dataclass(frozen=True)
class Episode:
    number: int
    title: str
    season: str
    streams: tuple[Stream, ...]

    def pick_stream(self, preferred: int | None = 1080) -> Stream:
        return pick_stream(self.streams, preferred)


def decode_envelope(raw: bytes, key: str = DEFAULT_DECODER_KEY) -> bytes:
    """Decode a Kinescope/Vibix `{"p": "...", "v": 1}` envelope.

    The publisher reverses the base64 text and XORs the decoded bytes with a
    static key. Non-envelope responses are returned unchanged.
    """
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return raw
    if not isinstance(envelope, dict) or "p" not in envelope:
        return raw

    encoded = str(envelope["p"]).replace("-", "+").replace("_", "/")
    encoded = encoded[::-1]
    encoded += "=" * (-len(encoded) % 4)
    try:
        decoded = base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise VibixProtocolError("invalid base64 in Vibix envelope") from exc

    key_bytes = key.encode()
    return bytes(byte ^ key_bytes[index % len(key_bytes)]
                 for index, byte in enumerate(decoded))


def encode_envelope(payload: bytes, key: str = DEFAULT_DECODER_KEY) -> bytes:
    """Inverse of decode_envelope, used by tests and protocol probes."""
    key_bytes = key.encode()
    xored = bytes(byte ^ key_bytes[index % len(key_bytes)]
                  for index, byte in enumerate(payload))
    encoded = base64.b64encode(xored).decode()[::-1]
    return json.dumps({"p": encoded, "v": 1}, separators=(",", ":")).encode()


def parse_manifest(raw: bytes, key: str = DEFAULT_DECODER_KEY) -> str:
    """Return an HLS manifest as text, decoding the envelope when present."""
    decoded = decode_envelope(raw, key)
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VibixProtocolError("manifest is not valid UTF-8") from exc
    if not text.lstrip().startswith("#EXTM3U"):
        raise VibixProtocolError("decoded manifest does not start with #EXTM3U")
    return text


def decode_payload(raw: bytes, key: str = DEFAULT_DECODER_KEY) -> Any:
    """Decode an enveloped API response into JSON."""
    decoded = decode_envelope(raw, key)
    try:
        return json.loads(decoded.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VibixProtocolError("Vibix API response is not valid JSON") from exc


def decode_playlist(raw: bytes, key: str = DEFAULT_DECODER_KEY) -> list[Episode]:
    """Decode an enveloped API response and return its episodes."""
    return parse_playlist(decode_payload(raw, key))


def parse_playlist(payload: Any) -> list[Episode]:
    """Flatten the API's season/folder tree into an ordered episode list."""
    if not isinstance(payload, dict):
        raise VibixProtocolError("Vibix API response is not an object")
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    seasons = data.get("playlist")
    if not isinstance(seasons, list):
        raise VibixProtocolError("playlist is missing from the Vibix API response")

    episodes: list[Episode] = []
    for season_index, season in enumerate(seasons, 1):
        if not isinstance(season, dict):
            continue
        season_title = str(season.get("title") or f"Сезон {season_index}")
        folder = season.get("folder")
        if not isinstance(folder, list):
            continue
        for item_index, item in enumerate(folder, 1):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or f"Серия {item_index}")
            match = _EPISODE_NUMBER.search(title)
            number = int(match.group()) if match else item_index
            streams = _parse_streams(str(item.get("file") or ""))
            episodes.append(Episode(number=number, title=title,
                                    season=season_title, streams=streams))
    return episodes


def _parse_streams(file_value: str) -> tuple[Stream, ...]:
    streams: dict[int, Stream] = {}
    for chunk in file_value.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        match = _QUALITY_CHUNK.match(chunk)
        if match:
            label = match.group(1).lower()
            quality = 2160 if label == "4k" else int(label[:-1])
            streams[quality] = Stream(quality=quality, url=match.group(2))
        elif chunk.startswith("http"):
            streams[0] = Stream(quality=0, url=chunk)
    return tuple(sorted(streams.values(), key=lambda item: item.quality, reverse=True))


def pick_stream(streams: tuple[Stream, ...] | list[Stream],
                preferred: int | None = 1080) -> Stream:
    """Pick the exact quality, else the best lower one, else the lowest higher one."""
    if not streams:
        raise VibixProtocolError("episode has no playable streams")
    ordered = sorted(streams, key=lambda item: item.quality)
    if preferred is None:
        return ordered[-1]
    for stream in reversed(ordered):
        if stream.quality <= preferred:
            return stream
    return ordered[0]


def parse_all_eps(html: str) -> list[dict[str, Any]]:
    """Extract the release page's ALL_EPS array (episode numbers and dates)."""
    match = _ALL_EPS.search(html)
    if not match:
        raise VibixProtocolError("release page has no ALL_EPS array")
    try:
        episodes = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise VibixProtocolError("ALL_EPS is not valid JSON") from exc
    if not isinstance(episodes, list):
        raise VibixProtocolError("ALL_EPS is not a list")
    return episodes


def stream_expires(url: str) -> int | None:
    """Return the signed URL expiry as a Unix timestamp, if present."""
    try:
        values = parse_qs(urlparse(url).query).get("expires")
        return int(values[0]) if values else None
    except (ValueError, TypeError):
        return None
