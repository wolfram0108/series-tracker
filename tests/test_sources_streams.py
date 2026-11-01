"""The stream of a candidate link, asked for right before the download: the player that owns the link resolves it."""
import json
from pathlib import Path

import httpx
import pytest

from core.reasons import parse
from modules.sources.players import cdnvideohub
from modules.sources.players.contract import StreamUnavailable
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.streams import resolve_stream
from modules.sources.players.vibix import link_of
from modules.sources.players.vibix_protocol import Episode, Stream, VibixProtocolError, VibixUnavailable, encode_envelope
from sources_support import Web

ALLVIDEO = "https://fsst.online/embed/77/"
EMBED_100 = "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc"
PLAYER_PARAMS = ('<html><head><title>Video embed</title></head><body><script>\nvar playerParams = {"params": [{"vid": 100, '
                 '"mp4_720": "https://cdn.test/720.mp4", "mp4_2160": "https://cdn.test/2160.mp4"}]};\n</script></body></html>')
CLOSED = '<html><head><title>Video embed</title></head><body><div id="video_ext_msg">Access denied</div></body></html>'
AMBIGUOUS = "<html><body>no player data, no refusal</body></html>"
DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
PLAYERJS = '<script>var player = new Playerjs({{file: "{files}"}});</script>'
KEY = b"Z" * 32
PAGE = "https://1.ruchime.org/release/test/episode-1"


def vk_web(page: str) -> Web:
    return Web().route("video_ext.php", page).route("al_video.php", DENIED, method="POST")


async def resolved(url: str, web: Web, max_height: int | None = None, players: PlayerRegistry | None = None):
    async with web.client() as http:
        return await resolve_stream(players or PlayerRegistry.standard(), url, http, max_height)


# --- AllVideo -----------------------------------------------------------------------------------------------------------------

async def test_an_allvideo_embed_resolves_to_its_best_stream_with_the_referer_of_its_host():
    web = Web().route("/embed/", PLAYERJS.format(files="[360p]https://cdn.test/360.mp4/,[1080p]https://cdn.test/1080.mp4/"))

    stream = await resolved(ALLVIDEO, web)

    assert stream == {"stream_url": "https://cdn.test/1080.mp4/", "headers": {"Referer": "https://fsst.online/"}}


async def test_the_best_stream_of_an_embed_is_4k_when_it_lists_one():
    web = Web().route("/embed/", PLAYERJS.format(files="[1080p]https://cdn.test/1080.mp4/,[4k]https://cdn.test/2160.mp4/"))

    assert (await resolved(ALLVIDEO, web))["stream_url"] == "https://cdn.test/2160.mp4/"


async def test_the_referer_is_the_host_the_embed_finally_answers_from():
    web = Web().route("fsst.online", httpx.Response(302, headers={"Location": "https://incvideo1.online/embed/77/"}))
    web.route("incvideo1.online", PLAYERJS.format(files="[1080p]https://cdn.test/1080.mp4/"))

    assert (await resolved(ALLVIDEO, web))["headers"]["Referer"] == "https://incvideo1.online/"


async def test_an_address_no_player_owns_is_not_handled():
    web = vk_web(PLAYER_PARAMS)

    assert await resolved("https://example.test/none", web) is None
    assert await resolved("https://vk.com/video-1_2", web) is None
    assert web.asked == []


# --- VK -----------------------------------------------------------------------------------------------------------------------

async def test_a_player_link_with_a_key_resolves_to_one_fresh_file_asked_for_in_parts():
    stream = await resolved(EMBED_100, vk_web(PLAYER_PARAMS), 1080)

    assert stream == {"stream_url": "https://cdn.test/720.mp4", "headers": {}, "progressive": True}


async def test_without_a_cap_the_best_file_is_resolved():
    assert (await resolved(EMBED_100, vk_web(PLAYER_PARAMS)))["stream_url"] == "https://cdn.test/2160.mp4"


async def test_a_private_video_has_no_stream_and_is_not_asked_again():
    web = vk_web(CLOSED)

    with pytest.raises(StreamUnavailable) as raised:
        await resolved(EMBED_100, web)

    assert raised.value.transient is False and parse(raised.value.reason)["code"] == "video_private"
    assert web.count("video_ext.php") == 1


async def test_a_player_page_that_gives_nothing_is_a_stream_that_may_come_later():
    with pytest.raises(StreamUnavailable) as raised:
        await resolved(EMBED_100, vk_web(AMBIGUOUS))

    assert raised.value.transient is True


async def test_a_link_to_a_video_without_a_key_is_not_a_player_link():
    web = vk_web(PLAYER_PARAMS)

    assert await resolved("https://vkvideo.ru/video-1_100", web) is None
    assert await resolved("https://vkvideo.ru/video_ext.php?oid=-1&id=100", web) is None


# --- Vibix --------------------------------------------------------------------------------------------------------------------

def catalog(registry: PlayerRegistry, *episodes: Episode, failure: Exception | None = None) -> None:
    async def playlist(page_url: str):
        if failure is not None:
            raise failure
        return list(episodes), KEY

    registry.get("vibix").playlist = playlist


def manifests(status: int = 200) -> Web:
    manifest = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\n./720.mp4:hls:seg-1-v1-a1.ts\n#EXT-X-ENDLIST\n"
    return Web().route("cdn.test", (status, encode_envelope(manifest, KEY).decode() if status == 200 else "<html/>"))


async def test_a_vibix_entry_resolves_to_a_manifest_with_absolute_addresses(tmp_path: Path):
    registry = PlayerRegistry.standard(manifest_dir=tmp_path)
    catalog(registry, Episode(2, "Серия 2", "Сезон 1", (Stream(1080, "https://cdn.test/hls/a/b/b_1080p.mp4/index.m3u8?e=1"),)))

    stream = await resolved(link_of(PAGE, 1, 2), manifests(), players=registry)

    path = Path(stream["stream_url"].removeprefix("file://"))
    assert path.is_absolute() and path.name == "test-s1-0002-1080.m3u8"
    assert "https://cdn.test/hls/a/b/b_1080p.mp4/720.mp4:hls:seg-1-v1-a1.ts" in path.read_text(encoding="utf-8")
    assert stream["headers"] == {"Referer": "https://1.ruchime.org/"}


async def test_a_vibix_entry_takes_the_best_manifest_that_is_alive(tmp_path: Path):
    registry = PlayerRegistry.standard(manifest_dir=tmp_path)
    catalog(registry, Episode(1, "Серия 1", "Сезон 1", (Stream(1080, "https://cdn.test/dead.m3u8"),
                                                       Stream(720, "https://cdn.test/live.m3u8"))))
    web = Web().route("dead.m3u8", (503, "<html/>")).route(
        "live.m3u8", (200, encode_envelope(b"#EXTM3U\n#EXTINF:6,\nseg.ts\n", KEY).decode()))

    stream = await resolved(link_of(PAGE, 1, 1), web, players=registry)

    assert stream["stream_url"].endswith("-720.m3u8")


async def test_a_vibix_entry_with_only_dead_manifests_is_an_explicit_error(tmp_path: Path):
    registry = PlayerRegistry.standard(manifest_dir=tmp_path)
    catalog(registry, Episode(1, "Серия 1", "Сезон 1", (Stream(480, "https://cdn.test/dead.m3u8"),)))

    with pytest.raises(StreamUnavailable) as raised:
        await resolved(link_of(PAGE, 1, 1), manifests(400), players=registry)

    assert parse(raised.value.reason)["code"] == "manifest_status" and raised.value.transient is False


@pytest.mark.parametrize("failure, transient", [(VibixUnavailable("no browser"), True), (VibixProtocolError("bad key"), False)])
async def test_a_catalog_that_cannot_be_read_is_an_unavailable_stream_that_may_pass_only_when_the_player_was_down(
        failure, transient):
    registry = PlayerRegistry.standard()
    catalog(registry, failure=failure)

    with pytest.raises(StreamUnavailable) as raised:
        await resolved(link_of(PAGE, 1, 1), manifests(), players=registry)

    assert raised.value.transient is transient and parse(raised.value.reason)["code"] == "player_failed"


async def test_a_page_of_the_site_is_not_a_vibix_link_and_is_left_to_nobody():
    assert await resolved(PAGE, manifests()) is None


# --- CDN VideoHub and Kodik: an item of a list ---------------------------------------------------------------------------------

DUNHUA = "https://dunhuatv.ru/28-x-smotret-onlajn.html"
CVH_SOURCES = {
    "v1s": {"mpegHighUrl": "https://okcdn.test/v1s/720", "mpegFullHdUrl": "https://okcdn.test/v1s/1080"},
    "v2": {"mpegLowUrl": "https://okcdn.test/v2/360"},
    "only4k": {"mpeg4kUrl": "https://okcdn.test/only4k/4k"},
    "v4k": {"mpegHighUrl": "https://okcdn.test/v4k/720", "mpeg4kUrl": "https://okcdn.test/v4k/4k"},
}


def listed_web(failing: dict[str, int] | None = None) -> Web:
    from test_sources_cvh_kodik import IFRAMES, ftor_links

    def video(request):
        vk_id = str(request.url).rsplit("/", 1)[1]
        if vk_id in (failing or {}):
            return failing[vk_id], ""
        return (200, json.dumps({"sources": CVH_SOURCES[vk_id], "duration": 600})) if vk_id in CVH_SOURCES else (404, "")

    web = Web().route("/player/sv/video/", video)
    web.route("kodikplayer.com/serial/", lambda request: IFRAMES.get(str(request.url).split("/serial/")[1].split("/")[0], (404, "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 480, 720)), method="POST")
    return web


async def measured_at_1440(rung) -> int | None:
    return 1440


async def test_a_cvh_item_resolves_to_its_best_rung_with_the_agent_that_signed_it():
    web = listed_web()

    stream = await resolved(f"{DUNHUA}#cvh=v1s", web)

    assert stream["stream_url"] == "https://okcdn.test/v1s/1080"
    assert stream["headers"]["User-Agent"] == cdnvideohub.USER_AGENT and stream["headers"]["Referer"] == cdnvideohub.ORIGIN + "/"
    asked = [request for request in web.requests if "/player/sv/video/" in str(request.url)]
    assert asked and {request.headers["user-agent"] for request in asked} == {cdnvideohub.USER_AGENT}


async def test_a_cvh_stream_takes_the_best_rung_within_the_cap():
    link = f"{DUNHUA}#cvh=v1s"

    assert (await resolved(link, listed_web(), 720))["stream_url"].endswith("/v1s/720")
    # Nothing at or below 480: the lowest rung above it is better than losing the episode.
    assert (await resolved(link, listed_web(), 480))["stream_url"].endswith("/v1s/720")
    assert (await resolved(link, listed_web()))["stream_url"].endswith("/v1s/1080")
    assert (await resolved(link, listed_web(), 2160))["stream_url"].endswith("/v1s/1080")


async def test_unlabelled_rungs_are_measured_only_when_nothing_else_is_left_and_a_rung_is_measured_once():
    calls = []

    async def counting(rung) -> int:
        calls.append(rung.key)
        return 1440

    players = PlayerRegistry.standard(measure=counting)
    await resolved(f"{DUNHUA}#cvh=v4k", listed_web(), 720, players)
    assert calls == []
    stream = await resolved(f"{DUNHUA}#cvh=only4k", listed_web(), 720, players)
    await resolved(f"{DUNHUA}#cvh=only4k", listed_web(), 720, players)

    assert stream["stream_url"].endswith("/only4k/4k") and calls == ["mpeg4kUrl"]


async def test_a_kodik_item_resolves_to_its_best_quality_within_the_cap_with_the_referer_of_the_player():
    link = f"{DUNHUA}#kodik=50228/7bbb4c6b/102"

    heights = {cap: (await resolved(link, listed_web(), cap))["stream_url"] for cap in (480, 300, None)}
    stream = await resolved(link, listed_web())

    assert heights[480].endswith("/480.mp4:hls:manifest.m3u8") and heights[300].endswith("/360.mp4:hls:manifest.m3u8")
    assert heights[None].endswith("/720.mp4:hls:manifest.m3u8") and stream["headers"] == {"Referer": "https://kodikplayer.com/"}


async def test_an_item_the_player_has_no_stream_for_is_an_unavailable_stream_that_may_come_later_only_when_the_host_was_busy():
    link = f"{DUNHUA}#cvh="
    with pytest.raises(StreamUnavailable) as gone:
        await resolved(link + "missing", listed_web())
    with pytest.raises(StreamUnavailable) as busy:
        await resolved(link + "v2", listed_web({"v2": 503}))
    with pytest.raises(StreamUnavailable) as no_item:
        await resolved(f"{DUNHUA}#kodik=50228/7bbb4c6b/999", listed_web())

    assert not gone.value.transient and parse(parse(gone.value.reason)["params"]["cause"])["code"] == "player_video_missing"
    assert busy.value.transient and parse(gone.value.reason)["params"]["player"] == "CVH"
    assert not no_item.value.transient and parse(no_item.value.reason)["params"]["player"] == "Kodik"


async def test_the_old_name_of_the_cvh_player_in_a_link_is_not_a_link_and_a_page_is_nobodys():
    web = listed_web()

    assert await resolved(f"{DUNHUA}#vk=v1s", web) is None
    assert await resolved(DUNHUA, web) is None
