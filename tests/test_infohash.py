"""Тесты infohash: bencode-спаны, magnet, гибриды v2.

Дополнительно (не в CI, локально): tests/fixtures/torrents/ может
содержать реальные .torrent-файлы — для них хэши сверяются с
эталонами, записанными из прод-БД (источник истины — сам qBittorrent).
"""
import json
import os

import pytest

from modules.torrents.infohash import (TorrentParseError,
                                       infohash_candidates,
                                       infohash_from_magnet)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "torrents")


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


def _make_torrent(info: dict) -> bytes:
    return _bencode({b"announce": b"http://t.example/ann", b"info": info})


def test_v1_torrent_single_candidate():
    import hashlib
    info = {b"name": b"file.mkv", b"piece length": 16384, b"pieces": b"\x00" * 20,
            b"length": 100}
    content = _make_torrent(info)
    expected = hashlib.sha1(_bencode(info)).hexdigest()
    assert infohash_candidates(content) == [expected]


def test_hybrid_v2_two_candidates_sha256_first():
    import hashlib
    info = {b"meta version": 2, b"name": b"x", b"piece length": 16384,
            b"pieces": b"\x00" * 20, b"file tree": {}, b"length": 1}
    content = _make_torrent(info)
    raw = _bencode(info)
    assert infohash_candidates(content) == [
        hashlib.sha256(raw).hexdigest()[:40],
        hashlib.sha1(raw).hexdigest(),
    ]


def test_garbage_raises():
    with pytest.raises(TorrentParseError):
        infohash_candidates(b"\x00\x01\x02 not bencode")
    with pytest.raises(TorrentParseError):
        infohash_candidates(_bencode({b"announce": b"x"}))  # нет info


# --- magnet --------------------------------------------------------------------

HEX = "0692842f0ca2d33e99aa9fd4ac466ab38cd15da6"


def test_magnet_hex():
    assert infohash_from_magnet(
        f"magnet:?xt=urn:btih:{HEX.upper()}&dn=x") == HEX


def test_magnet_base32():
    import base64
    b32 = base64.b32encode(bytes.fromhex(HEX)).decode()
    assert infohash_from_magnet(f"magnet:?xt=urn:btih:{b32}") == HEX


def test_magnet_v2_rejected_explicitly():
    with pytest.raises(TorrentParseError, match="btmh"):
        infohash_from_magnet("magnet:?xt=urn:btmh:1220" + "a" * 64)


def test_magnet_without_hash():
    with pytest.raises(TorrentParseError):
        infohash_from_magnet("magnet:?dn=пусто")


# --- реальные файлы (если выложены локально) -----------------------------------

@pytest.mark.skipif(not os.path.isdir(FIXTURES),
                    reason="локальные фикстуры .torrent не выложены")
def test_real_torrents_match_qbittorrent_hashes():
    expected = json.load(open(os.path.join(FIXTURES, "expected_hashes.json")))
    assert expected, "expected_hashes.json пуст"
    for fname, qb_hash in expected.items():
        content = open(os.path.join(FIXTURES, fname), "rb").read()
        assert qb_hash.lower() in infohash_candidates(content), \
            f"{fname}: кандидаты не содержат hash из qBittorrent"
