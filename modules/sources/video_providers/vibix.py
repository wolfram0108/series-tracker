"""Vibix/Kinescope protocol: envelope codec, key store and browser capture.

The publisher wraps API responses and HLS manifests into a reversed-base64,
XOR-encrypted envelope. The decryptor key is stored globally and recovered from
a manifest when it rotates, so a bundle update does not stop downloads.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

from core.reasons import reason

if TYPE_CHECKING:
    from playwright.async_api import Browser

logger = logging.getLogger("sources.video.vibix")

# The key is not shipped with the code: it lives in the shared store and is
# recovered from an encrypted manifest when the publisher rotates it.
KEY_LENGTH = 32
KEY_TTL_S = 12 * 3600
API_PATH = "/api/v1/embed-serials/"
_REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_KEY_PATH = Path(os.environ.get(
    "VIBIX_KEY_STORE", str(_REPO_ROOT / "torrent_cache" / "vibix-key.json")))

_QUALITY_CHUNK = re.compile(r"^\[(\d+p|4k)\]\{[^}]*\}(https?://.+)$", re.IGNORECASE)
_EPISODE_NUMBER = re.compile(r"\d+")
_HLS_HEADER = ("#EXTM3U\n#EXT-X-TARGETDURATION:{duration}\n{blank}"
               "#EXT-X-ALLOW-CACHE:YES\n#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-VERSION:3\n")
_BLOCKED_HOSTS = (
    "googlesyndication.com",
    "doubleclick.net",
    "adfinity.pro",
    "umami.is",
    "mc.yandex.ru",
    "google-analytics.com",
)


class VibixProtocolError(RuntimeError):
    """Raised when a Vibix response cannot be decoded or interpreted."""


class VibixUnavailable(RuntimeError):
    """Raised when a page has no Vibix player or the API never responds."""


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


@dataclass(frozen=True)
class Capture:
    page_url: str
    api_url: str
    body: bytes
    manifest_url: str = ""
    manifest_body: bytes | None = None


@dataclass(frozen=True)
class KeyState:
    key: bytes
    obtained_at: int
    source: str = "recovered"


def _key_bytes(key: str | bytes) -> bytes:
    data = key.encode("latin-1") if isinstance(key, str) else key
    if not data:
        raise VibixProtocolError("decoder key is empty")
    return data


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))


def envelope_bytes(raw: bytes) -> bytes | None:
    """Return the reversed-base64 payload of an envelope, or None for plain data."""
    try:
        envelope = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(envelope, dict) or "p" not in envelope:
        return None

    encoded = str(envelope["p"]).replace("-", "+").replace("_", "/")[::-1]
    encoded += "=" * (-len(encoded) % 4)
    try:
        return base64.b64decode(encoded)
    except (ValueError, TypeError) as exc:
        raise VibixProtocolError("invalid base64 in Vibix envelope") from exc


def decode_envelope(raw: bytes, key: str | bytes) -> bytes:
    """Decode a Kinescope/Vibix `{"p": "...", "v": 1}` envelope."""
    cipher = envelope_bytes(raw)
    if cipher is None:
        return raw
    return _xor(cipher, _key_bytes(key))


def encode_envelope(payload: bytes, key: str | bytes) -> bytes:
    """Inverse of decode_envelope, used by tests and protocol probes."""
    encoded = base64.b64encode(_xor(payload, _key_bytes(key))).decode()[::-1]
    return json.dumps({"p": encoded, "v": 1}, separators=(",", ":")).encode()


def recover_key(manifest_body: bytes, api_body: bytes | None = None) -> bytes:
    """Recover the keystream from an encrypted manifest with a known HLS header."""
    cipher = envelope_bytes(manifest_body)
    if cipher is None or len(cipher) < KEY_LENGTH:
        raise VibixProtocolError("manifest is not an encrypted Kinescope envelope")

    seen: set[bytes] = set()
    for duration in range(1, 61):
        for blank in ("", "\n"):
            prefix = _HLS_HEADER.format(duration=duration, blank=blank)[:KEY_LENGTH]
            key = bytes(a ^ b for a, b in zip(cipher[:KEY_LENGTH], prefix.encode()))
            if key in seen:
                continue
            seen.add(key)
            if not _is_manifest(cipher, key):
                continue
            if api_body is not None and not _is_payload(api_body, key):
                continue
            return key
    raise VibixProtocolError("cannot recover Vibix decoder key from the manifest")


def _is_manifest(cipher: bytes, key: bytes) -> bool:
    try:
        text = _xor(cipher, key).decode("utf-8")
    except UnicodeDecodeError:
        return False
    if not text.startswith("#EXTM3U") or "#EXTINF" not in text:
        return False
    return all(not line.strip() or line.startswith("#") or ".ts" in line
               for line in text.splitlines())


def _is_payload(body: bytes, key: bytes) -> bool:
    try:
        payload = json.loads(decode_envelope(body, key).decode("utf-8"))
    except (VibixProtocolError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    data = payload.get("data") if isinstance(payload, dict) else None
    return isinstance(data, dict) and isinstance(data.get("playlist"), list)


def parse_manifest(raw: bytes, key: str | bytes) -> str:
    """Return an HLS manifest as text, decoding the envelope when present."""
    decoded = decode_envelope(raw, key)
    try:
        text = decoded.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise VibixProtocolError("manifest is not valid UTF-8") from exc
    if not text.lstrip().startswith("#EXTM3U"):
        raise VibixProtocolError("decoded manifest does not start with #EXTM3U")
    return text


def decode_payload(raw: bytes, key: str | bytes) -> Any:
    """Decode an enveloped API response into JSON."""
    decoded = decode_envelope(raw, key)
    try:
        return json.loads(decoded.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VibixProtocolError("Vibix API response is not valid JSON") from exc


def decode_playlist(raw: bytes, key: str | bytes) -> list[Episode]:
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


def stream_expires(url: str) -> int | None:
    """Return the signed URL expiry as a Unix timestamp, if present."""
    try:
        values = parse_qs(urlparse(url).query).get("expires")
        return int(values[0]) if values else None
    except (ValueError, TypeError):
        return None


class KeyStore:
    """One shared key file per machine; readers never block on the network."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_KEY_PATH

    def load(self) -> KeyState | None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return KeyState(key=bytes.fromhex(str(data["key_hex"])),
                            obtained_at=int(data["obtained_at"]),
                            source=str(data.get("source", "unknown")))
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key: bytes, now: int, source: str = "recovered") -> KeyState:
        state = KeyState(key=key, obtained_at=now, source=source)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        temp.write_text(json.dumps({"key_hex": key.hex(), "obtained_at": now,
                                    "source": source}), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.path)
        return state

    def resolve(self, *, api_body: bytes, manifest_body: bytes | None,
                now: int | None = None) -> tuple[bytes, bool]:
        """Return (key, refreshed): reuse last key, refresh on TTL or failure."""
        moment = int(time.time()) if now is None else now
        state = self.load()
        if state is not None and moment - state.obtained_at < KEY_TTL_S:
            if _decodes(api_body, state.key):
                return state.key, False
            logger.warning("cached Vibix key stopped decoding; recovering")

        if manifest_body is not None:
            try:
                key = recover_key(manifest_body, api_body)
            except VibixProtocolError as exc:
                logger.warning("Vibix key recovery failed: %s", exc)
            else:
                self.save(key, moment)
                return key, True

        if state is not None and _decodes(api_body, state.key):
            logger.warning("using stale Vibix key obtained at %s", state.obtained_at)
            return state.key, False
        raise VibixProtocolError(
            "cannot obtain Vibix decoder key: no encrypted manifest in the capture")


def _decodes(body: bytes, key: bytes) -> bool:
    try:
        decode_payload(body, key)
    except VibixProtocolError:
        return False
    return True


async def capture_with_browser(browser: Browser, page_url: str, *,
                               timeout_s: float = 60.0) -> Capture:
    """Open an episode page in the shared browser and capture API and manifest."""
    context = await browser.new_context(locale="ru-RU",
                                        viewport={"width": 1366, "height": 900})
    await context.route("**/*", _block_ads)
    page = await context.new_page()
    api_future: asyncio.Future = asyncio.get_running_loop().create_future()
    manifest_future: asyncio.Future = asyncio.get_running_loop().create_future()

    def on_response(response) -> None:
        if API_PATH in response.url and not api_future.done():
            api_future.set_result(response)
        elif ".m3u8" in response.url and not manifest_future.done():
            manifest_future.set_result(response)

    page.on("response", on_response)
    try:
        await page.goto(page_url, wait_until="domcontentloaded", timeout=timeout_s * 1000)
        try:
            button = await page.wait_for_selector(
                '.source-btn[data-player="vibix"]', timeout=15_000)
        except Exception as exc:
            raise VibixUnavailable(reason("vibix_no_button")) from exc
        await button.click(timeout=15_000)
        response = await asyncio.wait_for(api_future, timeout=timeout_s)
        body = await response.body()
        manifest_url, manifest_body = await _manifest(manifest_future)
        return Capture(page_url=page_url, api_url=response.url, body=body,
                       manifest_url=manifest_url, manifest_body=manifest_body)
    except asyncio.TimeoutError as exc:
        raise VibixUnavailable(reason("vibix_api_timeout")) from exc
    finally:
        await context.close()


async def _manifest(future: asyncio.Future) -> tuple[str, bytes | None]:
    """Best-effort manifest capture: the key is recoverable only from it."""
    try:
        response = await asyncio.wait_for(future, timeout=20)
        return response.url, await response.body()
    except Exception:  # noqa: BLE001 - a missing manifest must not fail the capture
        return "", None


async def _block_ads(route) -> None:
    url = route.request.url
    if any(host in url for host in _BLOCKED_HOSTS):
        await route.abort()
    else:
        await route.continue_()
