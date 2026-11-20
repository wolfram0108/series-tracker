"""The MacCMS collection sites (Jisu, Xinlang, Lzi, Subo) and their player, on answers made the way the sites give them
(2026-11-09): a JSON record of a title with its lists of episodes, HLS playlists of one rendition, segments of real video
(made by ffmpeg here), encrypted with AES-128 as Jisu and Xinlang encrypt theirs."""
from __future__ import annotations

import json
import shutil
import subprocess

import httpx
import pytest
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from core.contracts import RowState
from modules.sources.players import hls
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.streams import resolve_stream
from modules.sources.sites import ReadContext
from modules.sources.sites.maccms import MaccmsSite
from sources_support import READ_LIMIT_S, STEP_LIMIT_S, Web, within

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg is needed")

JISU = "https://jszyapi.com/api.php/provide/vod/?ac=detail&ids=4794"
LZI = "https://cj.lziapi.com/api.php/provide/vod/?ac=detail&ids=47727"
KEY = bytes(range(16))


def segment(width: int, height: int) -> bytes:
    """A second of real video in MPEG-TS, the container of the segments of these sites."""
    return subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={width}x{height}:rate=25",
                           "-t", "1", "-c:v", "mpeg2video", "-f", "mpegts", "pipe:1"],
                          capture_output=True, check=True, timeout=60).stdout


def encrypted(data: bytes, iv: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    plain = padder.update(data) + padder.finalize()
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(iv)).encryptor()
    return encryptor.update(plain) + encryptor.finalize()


@pytest.fixture(scope="module")
def pictures():
    return {720: segment(1280, 720), 1080: segment(1920, 1080)}


def record(lists: dict[str, list[tuple[str, str]]], **fields) -> str:
    """A title as the API gives it: the names of the lists and their items `<label>$<address>`, lists apart by `$$$`."""
    return json.dumps({"code": 1, "msg": "数据列表", "list": [{
        "vod_id": 4794, "vod_name": "师兄啊师兄", "vod_sub": "我师兄实在太稳健了,My Senior Brother is Too Steady",
        "vod_play_from": "$$$".join(lists), **fields,
        "vod_play_url": "$$$".join("#".join(f"{label}${address}" for label, address in items) for items in lists.values())}]},
        ensure_ascii=False)


def media(segment_name: str, key: str = "", iv: str = "", sequence: int = 0) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:10", f"#EXT-X-MEDIA-SEQUENCE:{sequence}"]
    if key:
        lines.append(f'#EXT-X-KEY:METHOD=AES-128,URI="{key}"' + (f",IV=0x{iv}" if iv else ""))
    return "\n".join([*lines, "#EXTINF:10.0,", segment_name, "#EXTINF:10.0,", "next.ts", "#EXT-X-ENDLIST", ""])


def ts(data: bytes) -> httpx.Response:
    return httpx.Response(206, content=data[: hls.HEAD_BYTES])


def jisu_web(pictures) -> Web:
    """Two episodes of Jisu, each encrypted, one with the IV in its playlist and one with the IV of its sequence number;
    the list of the web player beside them."""
    iv = bytes.fromhex("0123456789abcdef0123456789abcdef")
    web = Web().route("jszyapi.com/api.php", record({
        "jsyun": [("第1集", "https://vv.jisuzyv.test/play/A"), ("第2集", "https://vv.jisuzyv.test/play/B")],
        "jsm3u8": [("第1集", "https://vv.jisuzyv.test/play/A/index.m3u8"),
                   ("第2集", "https://vv.jisuzyv.test/play/B/index.m3u8")]}))
    web.route("/play/A/index.m3u8", media("https://p.jisuts.test/A/plist0.ts", "enc.key", iv.hex()))
    web.route("/play/B/index.m3u8", media("plist7.ts", "enc.key", sequence=7))
    web.route("/play/A/enc.key", httpx.Response(200, content=KEY))
    web.route("/play/B/enc.key", httpx.Response(200, content=KEY))
    web.route("/A/plist0.ts", lambda request: ts(encrypted(pictures[1080], iv)))
    web.route("/play/B/plist7.ts", lambda request: ts(encrypted(pictures[1080], (7).to_bytes(16, "big"))))
    return web


def lzi_web(pictures) -> Web:
    """An episode of Lzi: a master playlist of one variant labelled 1080x608 whose picture is 1280x720, not encrypted."""
    web = Web().route("lziapi.com/api.php", record({
        "liangzi": [("第01集", "https://vip.lz-cdn14.test/share/x")],
        "lzm3u8": [("第01集", "https://vip.lz-cdn14.test/20230119/17597/index.m3u8")]}))
    web.route("/17597/index.m3u8", "#EXTM3U\n#EXT-X-STREAM-INF:PROGRAM-ID=1,BANDWIDTH=800000,RESOLUTION=1080x608\n"
                                   "2000k/hls/mixed.m3u8\n")
    web.route("/17597/2000k/hls/mixed.m3u8", media("6db159511ac000000.ts"))
    web.route("/2000k/hls/6db159511ac000000.ts", lambda request: ts(pictures[720]))
    return web


async def read(web: Web, url: str = JISU, players: PlayerRegistry | None = None, **context):
    site = MaccmsSite(players or PlayerRegistry.standard())
    async with web.client() as http:
        return await within(site.read(url, ReadContext(http=http, progress=lambda *_: None, **context)), READ_LIMIT_S,
                            "a read of a title")


# --- the address of a title -----------------------------------------------------------------------------------------------

# --- the table of a title -------------------------------------------------------------------------------------------------

async def test_the_label_of_a_master_playlist_is_not_taken_for_the_frame(pictures):
    draft = await read(lzi_web(pictures), LZI)

    assert [(row.text, row.state, row.heights) for row in draft.rows] == [("第01集", RowState.PLAYABLE, (720,))]


async def test_a_row_the_last_table_could_not_measure_is_asked_again(pictures):
    web = jisu_web(pictures)
    web.route("/A/plist0.ts", (503, "busy"), first=True)
    first = await read(web)
    assert [row.state for row in first.rows] == [RowState.NOT_OBTAINED, RowState.PLAYABLE]

    web.route("/A/plist0.ts", lambda request: ts(encrypted(pictures[1080], bytes.fromhex("0123456789abcdef" * 2))),
              first=True)
    again = await read(web, last_rows=tuple(first.rows))
    assert [(row.state, row.heights) for row in again.rows] == [(RowState.PLAYABLE, (1080,)), (RowState.PLAYABLE, (1080,))]


# --- the stream of an episode ---------------------------------------------------------------------------------------------

async def test_the_stream_of_an_episode_is_its_playlist_with_no_headers_for_a_download_in_parallel_segments(pictures):
    web = jisu_web(pictures)
    draft = await read(web)
    heads = web.count(".ts")
    async with web.client() as http:
        stream = await within(resolve_stream(PlayerRegistry.standard(), draft.rows[1].link, http, 720), STEP_LIMIT_S,
                              "the stream of an episode")

    # One rendition: a cap has nothing to choose, and the downloader is handed the playlist itself; its frame, measured on
    # the table, is not measured again.
    assert stream == {"stream_url": "https://vv.jisuzyv.test/play/B/index.m3u8", "headers": {}, "progressive": False,
                      "playlist": True}
    assert web.count(".ts") == heads
