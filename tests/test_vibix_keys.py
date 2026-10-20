from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.vibix_stand.keys import KEY_TTL_S, KeyStore
from scripts.vibix_stand.protocol import (VibixProtocolError, decode_payload,
                                          encode_envelope, recover_key)

KEY = b"0123456789abcdefghijklmnopqrstuv"
OTHER_KEY = b"ZYXWVUTSRQPONMLKJIHGFEDCBA987654"
MANIFEST = (b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n\n#EXT-X-ALLOW-CACHE:YES\n"
            b"#EXT-X-PLAYLIST-TYPE:VOD\n#EXT-X-VERSION:3\n"
            b"#EXT-X-MEDIA-SEQUENCE:1\n#EXTINF:6.000,\n"
            b"./720.mp4:hls:seg-1-v1-a1.ts\n#EXT-X-ENDLIST\n")
PAYLOAD = {"data": {"playlist": [{"title": "Сезон 1", "folder": [
    {"title": "Серия 1", "file": "[720p]{RuChiMe}https://cdn.test/1.m3u8"}]}]}}


def api_body(key: bytes = KEY) -> bytes:
    return encode_envelope(json.dumps(PAYLOAD, ensure_ascii=False).encode(), key)


def manifest_body(key: bytes = KEY, duration: int = 6, blank: str = "\n") -> bytes:
    text = MANIFEST.decode().replace(
        "#EXT-X-TARGETDURATION:6\n\n", f"#EXT-X-TARGETDURATION:{duration}\n{blank}"
    )
    return encode_envelope(text.encode(), key)


def test_recover_key_from_manifest() -> None:
    assert recover_key(manifest_body(), api_body()) == KEY


def test_recover_key_handles_other_duration_and_blank_line() -> None:
    assert recover_key(manifest_body(duration=12, blank=""), api_body()) == KEY
    assert recover_key(manifest_body(duration=4, blank="\n"), api_body()) == KEY


def test_recover_key_rejects_plain_manifest() -> None:
    with pytest.raises(VibixProtocolError):
        recover_key(MANIFEST, api_body())


def test_recover_key_rejects_unknown_header() -> None:
    body = encode_envelope(b"#EXTM3U\n#EXT-X-SOMETHING:1\n#EXTINF:1,\n./1.ts\n", KEY)
    with pytest.raises(VibixProtocolError):
        recover_key(body, api_body())


def test_recover_key_rejects_payload_with_other_key() -> None:
    with pytest.raises(VibixProtocolError):
        recover_key(manifest_body(), api_body(OTHER_KEY))


def test_store_reuses_fresh_key(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "key.json")
    store.save(KEY, now=1_000)
    key, refreshed = store.resolve(api_body=api_body(), manifest_body=None,
                                   now=1_000 + KEY_TTL_S - 1)
    assert (key, refreshed) == (KEY, False)


def test_store_refreshes_after_ttl(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "key.json")
    store.save(OTHER_KEY, now=1_000)
    key, refreshed = store.resolve(api_body=api_body(), manifest_body=manifest_body(),
                                   now=1_000 + KEY_TTL_S)
    assert (key, refreshed) == (KEY, True)
    assert store.load().key == KEY


def test_store_recovers_when_cached_key_fails(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "key.json")
    store.save(OTHER_KEY, now=1_000)
    key, refreshed = store.resolve(api_body=api_body(), manifest_body=manifest_body(),
                                   now=1_001)
    assert (key, refreshed) == (KEY, True)


def test_store_falls_back_to_stale_key(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "key.json")
    store.save(KEY, now=1_000)
    key, refreshed = store.resolve(api_body=api_body(), manifest_body=MANIFEST,
                                   now=1_000 + KEY_TTL_S * 2)
    assert (key, refreshed) == (KEY, False)


def test_store_raises_without_key_and_manifest(tmp_path: Path) -> None:
    store = KeyStore(tmp_path / "key.json")
    with pytest.raises(VibixProtocolError):
        store.resolve(api_body=api_body(), manifest_body=None)


def test_store_survives_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "key.json"
    path.write_text("{broken", encoding="utf-8")
    assert KeyStore(path).load() is None


def test_decoded_payload_uses_bytes_key() -> None:
    assert decode_payload(api_body(), KEY) == PAYLOAD
