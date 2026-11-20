from __future__ import annotations



from scripts.vibix_stand.protocol import parse_playlist

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
