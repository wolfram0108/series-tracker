from __future__ import annotations

import json

import pytest

from scripts.vibix_stand.protocol import (VibixProtocolError, decode_envelope,
                                          decode_payload, decode_playlist,
                                          encode_envelope, parse_all_eps,
                                          parse_manifest, parse_playlist,
                                          pick_stream, stream_expires)


def test_envelope_roundtrip() -> None:
    payload = b"#EXTM3U\n#EXTINF:12.0,\nhttps://example.test/seg.ts\n"
    encoded = encode_envelope(payload)
    assert decode_envelope(encoded) == payload


def test_plain_response_is_not_modified() -> None:
    raw = b'{"success": true}'
    assert decode_envelope(raw) == raw


def test_parse_manifest_rejects_non_hls() -> None:
    with pytest.raises(VibixProtocolError):
        parse_manifest(b"not a manifest")


def test_parse_playlist_flattens_seasons() -> None:
    payload = {
        "data": {
            "playlist": [
                {
                    "title": "Сезон 1",
                    "folder": [
                        {"title": "Серия 1", "file": (
                            "[480p]{RuChiMe}https://cdn.test/1.m3u8?expires=100,"
                            "[1080p]{RuChiMe}https://cdn.test/1_1080.m3u8?expires=100"
                        )},
                        {"title": "Серия 2", "file": (
                            "[720p]{RuChiMe}https://cdn.test/2.m3u8?expires=100"
                        )},
                    ],
                }
            ]
        }
    }
    episodes = parse_playlist(payload)
    assert [episode.number for episode in episodes] == [1, 2]
    assert [stream.quality for stream in episodes[0].streams] == [1080, 480]
    assert episodes[0].pick_stream(1080).url.endswith("1_1080.m3u8?expires=100")
    assert episodes[0].pick_stream(720).quality == 480
    assert episodes[1].pick_stream(1080).quality == 720


def test_pick_stream_prefers_lowest_when_all_are_higher() -> None:
    payload = {
        "data": {"playlist": [{"title": "Сезон 1", "folder": [
            {"title": "Серия 1", "file": "[1080p]{RuChiMe}https://cdn.test/1.m3u8"}
        ]}]}
    }
    episode = parse_playlist(payload)[0]
    assert episode.pick_stream(480).quality == 1080


def test_parse_streams_accepts_4k_and_plain_urls() -> None:
    payload = {
        "data": {"playlist": [{"title": "Сезон 1", "folder": [
            {"title": "Серия 1", "file": (
                "[4k]{RuChiMe}https://cdn.test/4k.m3u8,"
                "https://cdn.test/plain.m3u8"
            )}
        ]}]}
    }
    episode = parse_playlist(payload)[0]
    assert [stream.quality for stream in episode.streams] == [2160, 0]
    assert episode.pick_stream(None).quality == 2160
    assert episode.pick_stream(1080).quality == 0


def test_parse_playlist_rejects_non_object() -> None:
    with pytest.raises(VibixProtocolError):
        parse_playlist([])


def test_decode_playlist_from_envelope() -> None:
    payload = {"data": {"playlist": [{"title": "Сезон 1", "folder": [
        {"title": "Серия 1", "file": "[720p]{RuChiMe}https://cdn.test/1.m3u8"}
    ]}]}}
    raw = encode_envelope(json.dumps(payload).encode())
    assert decode_payload(raw) == payload
    assert [episode.number for episode in decode_playlist(raw)] == [1]


def test_decode_playlist_rejects_invalid_json() -> None:
    with pytest.raises(VibixProtocolError):
        decode_playlist(encode_envelope(b"not json"))


def test_parse_all_eps() -> None:
    html = '<script>const ALL_EPS = [{"number":1,"title":"Серия 1","date":"01 Jan 2026"}];</script>'
    episodes = parse_all_eps(html)
    assert episodes[0]["number"] == 1


def test_parse_all_eps_requires_array() -> None:
    with pytest.raises(VibixProtocolError):
        parse_all_eps("<html></html>")


def test_stream_expires() -> None:
    assert stream_expires("https://cdn.test/a.m3u8?expires=123&sign=x") == 123
    assert stream_expires("https://cdn.test/a.m3u8") is None


def test_parse_selection() -> None:
    from scripts.vibix_stand.download import parse_selection

    assert parse_selection("all", [3, 1, 2]) == [1, 2, 3]
    assert parse_selection("1-3,5", [1, 2, 3, 4, 5]) == [1, 2, 3, 5]
    with pytest.raises(ValueError):
        parse_selection("9", [1, 2, 3])


def test_encoded_playlist_payload() -> None:
    payload = {"data": {"playlist": []}}
    raw = encode_envelope(json.dumps(payload).encode())
    assert json.loads(decode_envelope(raw)) == payload
