"""The stream of a candidate link, asked for right before the download: the player that owns the link resolves it."""
import json

import pytest

from core.reasons import parse
from modules.sources.players.contract import StreamUnavailable
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.streams import resolve_stream
from modules.sources.players.vibix_protocol import Episode, encode_envelope
from sources_support import STEP_LIMIT_S, Web, within

EMBED_100 = "https://vkvideo.ru/video_ext.php?oid=-1&id=100&hash=abc"
PLAYER_PARAMS = ('<html><head><title>Video embed</title></head><body><script>\nvar playerParams = {"params": [{"vid": 100, '
                 '"mp4_720": "https://cdn.test/720.mp4", "mp4_2160": "https://cdn.test/2160.mp4"}]};\n</script></body></html>')
DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
KEY = b"Z" * 32
PAGE = "https://1.ruchime.org/release/test/episode-1"


def vk_web(page: str) -> Web:
    return Web().route("video_ext.php", page).route("al_video.php", DENIED, method="POST")


async def resolved(url: str, web: Web, max_height: int | None = None, players: PlayerRegistry | None = None):
    async with web.client() as http:
        return await within(resolve_stream(players or PlayerRegistry.standard(), url, http, max_height), STEP_LIMIT_S,
                            f"the stream of {url}")


# --- AllVideo -----------------------------------------------------------------------------------------------------------------

# --- a player registered is found, with nothing else named ------------------------------------------------------------------------

# --- VK -----------------------------------------------------------------------------------------------------------------------

async def test_a_player_link_with_a_key_resolves_to_one_fresh_file_asked_for_in_parts():
    stream = await resolved(EMBED_100, vk_web(PLAYER_PARAMS), 1080)

    assert stream == {"stream_url": "https://cdn.test/720.mp4", "headers": {}, "progressive": True}


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


async def test_unlabelled_rungs_are_measured_only_when_one_may_be_the_answer_and_a_rung_is_measured_once():
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

    # A cap above the labelled rungs: the unlabelled one may fit it, so it is measured.
    above = await resolved(f"{DUNHUA}#cvh=v4k", listed_web(), 2160, players)
    assert above["stream_url"].endswith("/v4k/4k") and calls == ["mpeg4kUrl", "mpeg4kUrl"]


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


# --- a link a player owns is never "not handled" ---------------------------------------------------------------------------------
