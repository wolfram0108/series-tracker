"""Local computation of a torrent's infohash (decision Р-2).

Infohash v1 is the SHA1 of the bytes of the `info` section of a .torrent
file; qBittorrent uses it as the torrent's primary key. It is computed over
the original file bytes (span approach), not by rebuilding the dictionary,
so the result does not depend on any assumption about canonical key order.

Magnet: the hash is in the link itself (`xt=urn:btih:...`), as hex (40
characters) or base32 (32 characters). The new v2 format (`urn:btmh:`) is
deliberately unsupported — an explicit error instead of a silent one (v2
magnets are still exotic on the Runet; see contracts/revision.md Р-2).
"""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

from core.reasons import reason


class TorrentParseError(ValueError):
    """Invalid .torrent / magnet, or an unsupported format."""


# --- bencode: decoder with spans -------------------------------------------

def _decode(data: bytes, pos: int) -> tuple[object, int]:
    """Decodes a value starting at pos; returns (value, end)."""
    if pos >= len(data):
        raise TorrentParseError(reason("torrent_truncated"))
    ch = data[pos:pos + 1]
    if ch == b"i":  # integer: i<number>e
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if ch == b"l":  # list: l<values>e
        items, pos = [], pos + 1
        while data[pos:pos + 1] != b"e":
            item, pos = _decode(data, pos)
            items.append(item)
        return items, pos + 1
    if ch == b"d":  # dict: d<key><value>...e
        result, pos = {}, pos + 1
        while data[pos:pos + 1] != b"e":
            key, pos = _decode(data, pos)
            if not isinstance(key, bytes):
                raise TorrentParseError(reason("torrent_bad_key"))
            value_start = pos
            value, pos = _decode(data, pos)
            result[key] = (value, value_start, pos)  # value + span
        return result, pos + 1
    if ch.isdigit():  # byte string: <length>:<bytes>
        colon = data.index(b":", pos)
        length = int(data[pos:colon])
        start = colon + 1
        if start + length > len(data):
            raise TorrentParseError(reason("torrent_string_overflow"))
        return data[start:start + length], start + length
    raise TorrentParseError(reason("torrent_bad_marker", marker=ch.decode("latin-1")))


def infohash_candidates(content: bytes) -> list[str]:
    """Infohash candidates (hex, lowercase) from a .torrent file.

    A classic torrent gives one candidate: SHA1 of the info section (v1).
    A hybrid/v2 (`meta version: 2`) gives TWO candidates, the first being the
    truncated SHA256: libtorrent 2.x (qBittorrent ≥4.4) keys hybrids by it.
    Confirmed on production data: a torrent with meta version 2 was stored
    in the DB under sha256(info)[:40], the other 169 under sha1 (see
    revision.md Р-2). Which candidate qBit actually uses is settled by a
    control query after adding.
    """
    try:
        top, _ = _decode(content, 0)
    except (ValueError, IndexError) as exc:
        raise TorrentParseError(reason("torrent_bencode_broken", detail=str(exc))) from exc
    if not isinstance(top, dict) or b"info" not in top:
        raise TorrentParseError(reason("torrent_no_info"))
    info, start, end = top[b"info"]
    info_bytes = content[start:end]
    v1 = hashlib.sha1(info_bytes).hexdigest()
    meta_version = info.get(b"meta version", (None,))[0]
    if meta_version == 2:
        v2_truncated = hashlib.sha256(info_bytes).hexdigest()[:40]
        return [v2_truncated, v1]
    return [v1]


# --- file list (naming preview on add) -------------------------------------

def torrent_file_list(content: bytes) -> tuple[str | None, list[dict]]:
    """File list from a .torrent — for the naming preview on add.

    Built on the same span decoder as the infohash (no network needed). The
    `name.utf-8`/`path.utf-8` keys are preferred: Russian trackers put the main
    path in cp1251, while qBittorrent reports the utf-8 variant — so the preview
    matches the actual rename. Multi-file torrent -> (folder_name, [{path, size}]);
    single-file -> (None, [{path=name, size}]).
    """
    try:
        top, _ = _decode(content, 0)
    except (ValueError, IndexError) as exc:
        raise TorrentParseError(reason("torrent_bencode_broken", detail=str(exc))) from exc
    if not isinstance(top, dict) or b"info" not in top:
        raise TorrentParseError(reason("torrent_no_info"))
    info = top[b"info"][0]  # _decode stores a dict value as (value, start, end)

    def _val(d: dict, key: bytes):
        item = d.get(key)
        return item[0] if item is not None else None

    def _text(b) -> str:
        return (b.decode("utf-8", "replace")
                if isinstance(b, (bytes, bytearray)) else str(b))

    name = _val(info, b"name.utf-8") or _val(info, b"name")
    files_raw = _val(info, b"files")
    if files_raw is not None:  # multi-file
        files = []
        for entry in files_raw:  # list items are raw dicts with spans
            if not isinstance(entry, dict):
                continue
            parts = _val(entry, b"path.utf-8") or _val(entry, b"path") or []
            files.append({"path": "/".join(_text(p) for p in parts),
                          "size": _val(entry, b"length")})
        return (_text(name) if name is not None else None), files
    return None, [{"path": _text(name) if name is not None else "",
                   "size": _val(info, b"length")}]


# --- magnet ------------------------------------------------------------------

def infohash_from_magnet(link: str) -> str:
    """Infohash v1 (hex, lowercase) from a magnet link."""
    parsed = urlparse(link)
    if parsed.scheme != "magnet":
        raise TorrentParseError(reason("magnet_not_magnet"))
    for xt in parse_qs(parsed.query).get("xt", []):
        if xt.startswith("urn:btmh:"):
            raise TorrentParseError(reason("magnet_v2_unsupported"))
        if not xt.startswith("urn:btih:"):
            continue
        value = xt[len("urn:btih:"):]
        if len(value) == 40:  # hex
            try:
                bytes.fromhex(value)
            except ValueError as exc:
                raise TorrentParseError(reason("magnet_hash_invalid", value=value)) from exc
            return value.lower()
        if len(value) == 32:  # base32 (old links)
            try:
                raw = base64.b32decode(value.upper())
            except Exception as exc:
                raise TorrentParseError(reason("magnet_hash_invalid", value=value)) from exc
            return raw.hex()
        raise TorrentParseError(reason("magnet_hash_length", length=len(value)))
    raise TorrentParseError(reason("magnet_no_hash"))
