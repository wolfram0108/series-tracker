"""Тесты infohash: bencode-спаны, magnet, гибриды v2.

Дополнительно (не в CI, локально): tests/fixtures/torrents/ может
содержать реальные .torrent-файлы — для них хэши сверяются с
эталонами, записанными из прод-БД (источник истины — сам qBittorrent).
"""

import pytest

from modules.torrents.infohash import (TorrentParseError,
                                       infohash_candidates,
                                       infohash_from_magnet)

# --- синтетические .torrent ----------------------------------------------------

def _bencode(obj) -> bytes:
    if isinstance(obj, int):
        return b"i%de" % obj
    if isinstance(obj, bytes):
        return b"%d:%s" % (len(obj), obj)
    if isinstance(obj, list):
        return b"l" + b"".join(_bencode(x) for x in obj) + b"e"
    if isinstance(obj, dict):
        return b"d" + b"".join(_bencode(k) + _bencode(v)
                               for k, v in sorted(obj.items())) + b"e"
    raise TypeError(type(obj))


def test_garbage_raises():
    with pytest.raises(TorrentParseError):
        infohash_candidates(b"\x00\x01\x02 not bencode")
    with pytest.raises(TorrentParseError):
        infohash_candidates(_bencode({b"announce": b"x"}))  # нет info


# --- magnet --------------------------------------------------------------------

HEX = "0692842f0ca2d33e99aa9fd4ac466ab38cd15da6"


def test_magnet_base32():
    import base64
    b32 = base64.b32encode(bytes.fromhex(HEX)).decode()
    assert infohash_from_magnet(f"magnet:?xt=urn:btih:{b32}") == HEX


# --- реальные файлы (если выложены локально) -----------------------------------
