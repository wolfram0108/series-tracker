"""An HLS playlist a player resolved, downloaded by its segments (modules/downloads/hls_native.py): our client reads the
playlist, the native downloader of yt-dlp fetches the segments — AES-128 decrypted, each taken from where its transport
stream begins (a host puts the head of a PNG before it) — and ffmpeg copies the stream into the container. A refused
segment is never skipped: the download fails and leaves nothing. Real files on a real HTTP host (tests/plan_disk.py)."""
from __future__ import annotations

import json
import os
import subprocess

import pytest

from modules.downloads import hls_native, ytdlp
from modules.downloads.hls_worker import transport_stream
from plan_disk import Codec, Host, _run

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452") + bytes(200)
KEY = bytes(range(16))


def stream(root: str, *, wrapped: bool = False, encrypted: bool = False, master: bool = False) -> str:
    """A playlist of four one-second segments of a picture 320x240 on the host; its address."""
    folder = os.path.join(root, "show")
    os.makedirs(folder)
    source = os.path.join(folder, "source.mp4")
    Codec().make(source, [1, 2, 3, 4])
    args = []
    if encrypted:
        with open(os.path.join(folder, "enc.key"), "wb") as key:
            key.write(KEY)
        with open(os.path.join(folder, "key.info"), "w") as info:
            info.write(f"enc.key\n{os.path.join(folder, 'enc.key')}\n")
        args = ["-hls_key_info_file", os.path.join(folder, "key.info")]
    # A key frame each second: every segment of a second begins with one.
    _run("-i", source, "-vf", "scale=320:240", "-c:v", "libx264", "-preset", "ultrafast", "-g", "4", "-sc_threshold", "0",
         "-c:a", "aac",
         "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod", *args,
         "-hls_segment_filename", os.path.join(folder, "seg%d.ts"), os.path.join(folder, "media.m3u8"))
    os.remove(source)
    assert sorted(name for name in os.listdir(folder) if name.endswith(".ts")) == [f"seg{n}.ts" for n in range(4)]
    if wrapped:
        for name in os.listdir(folder):
            if name.endswith(".ts"):
                path = os.path.join(folder, name)
                with open(path, "rb") as file:
                    body = file.read()
                with open(path, "wb") as file:
                    file.write(PNG + body)
    if master:
        with open(os.path.join(folder, "index.m3u8"), "w") as index:
            index.write("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=100000,RESOLUTION=160x120\nlow.m3u8\n"
                        "#EXT-X-STREAM-INF:BANDWIDTH=900000,RESOLUTION=320x240\nmedia.m3u8\n")
        return "show/index.m3u8"
    return "show/media.m3u8"


def probe(path: str) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,height:format=duration", "-of", "json",
                          path], capture_output=True, text=True, check=True, timeout=30).stdout
    return json.loads(out)


async def noop(_data: dict) -> None:
    pass


def test_the_transport_stream_of_a_fragment_starts_at_its_first_run_of_packets():
    packets = b"".join(bytes([0x47]) + bytes(187) for _ in range(6))
    assert transport_stream(PNG + packets) == packets
    assert transport_stream(packets) == packets
    fmp4 = b"\x00\x00\x00\x18ftypmp42" + bytes(400)
    assert transport_stream(fmp4) == fmp4, "a fragment with no run of packets is left as it is"


def test_a_playlist_is_saved_with_its_addresses_absolute_and_its_length_and_longest_segment():
    text = ("#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-KEY:METHOD=AES-128,URI=\"k.key\"\n#EXT-X-MAP:URI=\"init.mp4\"\n"
            "#EXTINF:6.0,\na/1.ts\n#EXTINF:4.5,\nhttps://cdn.other/2.ts\n#EXT-X-ENDLIST\n")
    manifest, seconds, longest = hls_native.absolute(text, "https://host.test/show/media.m3u8")
    assert 'URI="https://host.test/show/k.key"' in manifest and 'URI="https://host.test/show/init.mp4"' in manifest
    assert "https://host.test/show/a/1.ts" in manifest and "https://cdn.other/2.ts" in manifest
    assert (seconds, longest) == (10.5, 6.0)
    with pytest.raises(hls_native.PlaylistError):
        hls_native.absolute(text.replace("#EXT-X-ENDLIST\n", ""), "https://host.test/")
    with pytest.raises(hls_native.PlaylistError):
        hls_native.absolute(text.replace("AES-128", "SAMPLE-AES"), "https://host.test/")


@pytest.mark.parametrize(("wrapped", "encrypted", "master"), [(True, False, True), (False, True, False)])
async def test_a_playlist_is_downloaded_whole_by_its_segments(tmp_path, wrapped, encrypted, master):
    host = Host(str(tmp_path / "site"))
    try:
        name = stream(str(tmp_path / "site"), wrapped=wrapped, encrypted=encrypted, master=master)
        out = str(tmp_path / "lib" / "e1.mp4")
        told: list[dict] = []

        async def progress(data: dict) -> None:
            told.append(data)

        ok, why = await ytdlp.download(host.url(name), out, progress, playlist=True, threads=4)
        assert ok, why
        held = probe(out)
        assert {each["codec_type"] for each in held["streams"]} == {"video", "audio"}
        assert [each["height"] for each in held["streams"] if each["codec_type"] == "video"] == [240]
        assert float(held["format"]["duration"]) == pytest.approx(4, abs=0.5)
        assert told[0] == {"method": "hls"} and any(each.get("phase") == "download" for each in told)
        assert sorted(os.listdir(tmp_path / "lib")) == ["e1.mp4"], "no leftover of the download stays"
        assert not any(name.endswith("low.m3u8") for name in host.served), "the variant of the highest frame is taken"
    finally:
        host.close()


async def test_a_refused_segment_is_never_skipped_the_download_fails_and_leaves_nothing(tmp_path):
    host = Host(str(tmp_path / "site"))
    try:
        name = stream(str(tmp_path / "site"))
        host.missing.add("show/seg2.ts")
        out = str(tmp_path / "lib" / "e1.mp4")
        ok, why = await ytdlp.download(host.url(name), out, noop, playlist=True, threads=4)
        assert not ok and "загрузчик сегментов" in why
        assert os.listdir(tmp_path / "lib") == []
    finally:
        host.close()


AD = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:11\n#EXT-X-MEDIA-SEQUENCE:0\n"
      "#EXTINF:10.0,\nhttps://omts.cdn/v/abc/1.ts\n#EXTINF:10.0,\nhttps://omts.cdn/v/abc/2.ts\n"
      "#EXT-X-DISCONTINUITY\n#EXT-X-KEY:METHOD=AES-128,URI=\"https://ads.cdn/k.key\"\n"
      "#EXTINF:3.0,\nhttps://ads.cdn/20260912/x/1000kb/hls/a1.ts\n#EXTINF:3.0,\nhttps://ads.cdn/20260912/x/1000kb/hls/a2.ts\n"
      "#EXT-X-DISCONTINUITY\n#EXTINF:10.0,\nhttps://omts.cdn/v/abc/3.ts\n#EXT-X-ENDLIST\n")


def test_a_block_a_host_put_into_the_stream_from_elsewhere_is_left_out_and_the_stream_keeps_its_own_key():
    kept, dropped = hls_native.without_insertions(AD, "https://omts.cdn/v/abc/index.m3u8")
    assert dropped == [hls_native.Insertion("ads.cdn/20260912/x/1000kb/hls", 2, 6.0)]
    assert "ads.cdn" not in kept and [line for line in kept.splitlines() if line.endswith(".ts")] == \
        ["https://omts.cdn/v/abc/1.ts", "https://omts.cdn/v/abc/2.ts", "https://omts.cdn/v/abc/3.ts"]
    # The advertisement set a key of its own: the stream after it is told again it has none.
    assert "#EXT-X-KEY:METHOD=NONE" in kept and kept.index("#EXT-X-KEY:METHOD=NONE") < kept.index("abc/3.ts")
    _manifest, seconds, _longest = hls_native.absolute(kept, "https://omts.cdn/v/abc/index.m3u8")
    assert seconds == 30.0 and kept.splitlines()[:4] == AD.splitlines()[:4] and kept.rstrip().endswith("#EXT-X-ENDLIST")
    # A discontinuity within the stream's own segments is no insertion.
    own = AD.replace("https://ads.cdn/20260912/x/1000kb/hls/", "https://omts.cdn/v/abc/")
    assert hls_native.without_insertions(own, "https://omts.cdn/v/abc/index.m3u8") == (own, [])


async def test_a_playlist_with_an_advertisement_put_into_it_is_downloaded_without_it(tmp_path):
    host = Host(str(tmp_path / "site"))
    try:
        name = stream(str(tmp_path / "site"))
        ad = os.path.join(str(tmp_path / "site"), "ad")
        os.makedirs(ad)
        source = os.path.join(ad, "source.mp4")
        Codec().make(source, [9, 9])
        _run("-i", source, "-vf", "scale=320:240", "-c:v", "libx264", "-preset", "ultrafast", "-g", "4", "-c:a", "aac",
             "-f", "hls", "-hls_time", "1", "-hls_playlist_type", "vod", "-hls_segment_filename",
             os.path.join(ad, "a%d.ts"), os.path.join(ad, "ad.m3u8"))
        media = os.path.join(str(tmp_path / "site"), "show", "media.m3u8")
        with open(media) as found:
            lines = found.read().splitlines()
        at = lines.index("seg2.ts") - 1  # the #EXTINF of the third segment
        inserted = ["#EXT-X-DISCONTINUITY", "#EXTINF:1.0,", host.url("ad/a0.ts"), "#EXTINF:1.0,", host.url("ad/a1.ts"),
                    "#EXT-X-DISCONTINUITY"]
        with open(media, "w") as written:
            written.write("\n".join(lines[:at] + inserted + lines[at:]) + "\n")
        out = str(tmp_path / "lib" / "e1.mp4")
        ok, why = await ytdlp.download(host.url(name), out, noop, playlist=True, threads=4)
        assert ok, why
        assert float(probe(out)["format"]["duration"]) == pytest.approx(4, abs=0.5), "the advertisement is not in the file"
        assert not any(served.startswith("ad/") for served in host.served), "nothing of the advertisement was asked"
    finally:
        host.close()
