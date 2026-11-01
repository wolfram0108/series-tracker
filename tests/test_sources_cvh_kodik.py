"""The players CDN VideoHub and Kodik: what they find on a page, the lists they give and the files of one item.

The shapes of the answers are those of the snapshots (`test_sources_dunhuatv_site` reads the snapshots whole); here the lists
are small, so that each rule of the players is seen on its own: the item has no number of the player's making, a season the
player does not give is not made up, a voice-over an address limits the iframe to is the only one read.
"""
import base64
import json

import httpx
import pytest

from core.reasons import parse
from modules.sources.players import cdnvideohub, kodik
from modules.sources.players.catalog import CatalogError, read_catalog
from modules.sources.players.cdnvideohub import CdnVideoHubError, CdnVideoHubPlayer, Rung
from modules.sources.players.contract import Kind, PlayerRef
from sources_support import Web

PAGE = "https://dunhuatv.ru/28-x-smotret-onlajn.html"
WIDGET = '<video-player id="myplayer" data-title-id="54437" data-publisher-id="1110"></video-player>'
VIDEO_SOURCES = {
    "v1": {"mpegLowUrl": "https://okcdn.test/v1/360", "mpegHighUrl": "https://okcdn.test/v1/720"},
    "v4k": {"mpegHighUrl": "https://okcdn.test/v4k/720", "mpeg4kUrl": "https://okcdn.test/v4k/4k"},
}


async def measured(rung: Rung) -> int | None:
    return {"mpeg4kUrl": 1440}.get(rung.key)


def signed(domain: str = "dunhuatv.ru") -> str:
    return (f'<script>var serialId = Number(50228); var domain = "{domain}"; var d_sign = "ds"; var pd = "kodikplayer.com";'
            ' var pd_sign = "ps"; var ref = "https://dunhuatv.ru/"; var ref_sign = "rs";</script>')


def voices(*pairs: tuple[str, str, str, str]) -> str:
    options = "".join(f'<option value="{tid}" data-id="{tid}" data-translation-type="voice" data-media-id="{mid}" '
                      f'data-media-hash="{mhash}" data-title="{title}">{title}</option>' for tid, mid, mhash, title in pairs)
    return f'<div class="serial-translations-box"><select>{options}</select></div>'


def seasons(*triples: tuple[str, str, str, bool]) -> str:
    options = "".join(f'<option value="{index}" data-serial-id="{mid}" data-serial-hash="{mhash}" data-title="{title}"'
                      f'{" selected=\"selected\"" if chosen else ""}>{title}</option>'
                      for index, (title, mid, mhash, chosen) in enumerate(triples, 1))
    return f'<div class="serial-seasons-box"><select>{options}</select></div>'


def items(*triples: tuple[str, str, str, str]) -> str:
    options = "".join(f'<option value="{value}" data-id="{item}" data-hash="h{item}" data-title="{text}">{text}</option>'
                      for item, value, text, _ in triples)
    return f'<div class="serial-series-box"><select>{options}</select></div>'


ANIMY = ("1043", "50228", "7bbb4c6b", "Animy")
ANISTAR = ("910", "49002", "b1b36deb", "AniStar")
SEASON_1, SEASON_2 = ("1 сезон", "50228", "7bbb4c6b", True), ("2 сезон", "50229", "7bbb4c6c", False)
IFRAMES = {
    "50228": signed() + voices(ANIMY, ANISTAR) + seasons(SEASON_1, SEASON_2) + items(
        ("101", "1", "1 серия", ""), ("102", "3", "2-3 серия", ""), ("103", "4", "4 серия", "")),
    "50229": signed() + voices(ANIMY, ANISTAR) + seasons(("1 сезон", "50228", "7bbb4c6b", False),
                                                          ("2 сезон", "50229", "7bbb4c6c", True))
    + items(("201", "1", "1 серия", ""), ("202", "2", "2 серия", "")),
    "49002": signed() + voices(ANIMY, ANISTAR) + seasons(("1 сезон", "49002", "b1b36deb", True)) + items(
        ("301", "1", "1 серия", ""), ("302", "2", "2 серия", "")),
}


def obfuscate(url: str) -> str:
    """Inverse of Kodik's cipher: base64, then every letter shifted back by 18."""
    plain = base64.b64encode(url.encode()).decode()

    def shift(char: str) -> str:
        if not char.isalpha():
            return char
        first = ord("A") if char <= "Z" else ord("a")
        return chr(first + (ord(char) - first - 18) % 26)

    return "".join(shift(char) for char in plain)


def ftor_links(*heights: int) -> dict:
    return {"links": {str(h): [{"src": obfuscate(f"//cloud.kodik.test/{h}.mp4:hls:manifest.m3u8"),
                                "type": "application/x-mpegURL"}] for h in heights}}


def kodik_web(iframes: dict[str, str] | None = None) -> Web:
    pages = IFRAMES if iframes is None else iframes
    web = Web()
    web.route("kodikplayer.com/serial/", lambda request: pages.get(str(request.url).split("/serial/")[1].split("/")[0],
                                                                   (404, "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 720)), method="POST")
    return web


async def entries_of(source_url: str, web: Web | None = None):
    player, web = kodik.KodikPlayer(), web or kodik_web()
    async with web.client() as http:
        return await read_catalog(player, PlayerRef("kodik", source_url), http, PAGE), web


# --- Kodik: the list of an iframe ---------------------------------------------------------------------------------------------

def test_the_parts_of_an_iframe_are_its_voices_its_seasons_and_the_items_of_the_list_as_they_are_worded():
    page = IFRAMES["50228"]

    assert [(v.translation_id, v.studio, v.media_id) for v in kodik.parse_voices(page)] == [
        ("1043", "Animy", "50228"), ("910", "AniStar", "49002")]
    assert [(s.title, s.media_id, s.selected) for s in kodik.parse_seasons(page)] == [
        ("1 сезон", "50228", True), ("2 сезон", "50229", False)]
    listed = kodik.parse_items(page)
    assert list(listed) == ["101", "102", "103"]
    # The value of an item is the last number of a compilation; what the list says of the item is its text.
    assert (listed["102"].text, listed["102"].value, listed["102"].hash) == ("2-3 серия", "3", "h102")
    assert kodik.parse_signatures(page)["ref"] == "https://dunhuatv.ru/"
    with pytest.raises(kodik.KodikError):
        kodik.parse_signatures("<html></html>")


def test_a_page_embeds_kodik_by_an_iframe_or_by_a_button_that_switches_to_it():
    player = kodik.KodikPlayer()
    frame = '<iframe data-src="//kodikplayer.com/serial/1/ab/720p?only_episode=true&amp;x=1" loading="lazy"></iframe>'
    button = ("<button data-player=\"kodik\" onclick=\"switchSource('https://kodikplayer.com/serial/50228/7bbb/720p"
              "?only_translations=1043', this)\">Kodik</button>")

    assert player.find(frame, PAGE).url == "https://kodikplayer.com/serial/1/ab/720p?x=1"
    assert player.find(button, PAGE).url == "https://kodikplayer.com/serial/50228/7bbb/720p?only_translations=1043"
    assert player.find("<p>nothing</p>", PAGE) is None


async def test_every_item_of_every_season_of_every_voice_is_an_entry_with_the_text_of_the_list():
    found, web = await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p")

    assert [(e.key, e.group, e.voice.name, e.text) for e in found] == [
        ("1043:101", "1 сезон", "Animy", "1 серия"), ("1043:102", "1 сезон", "Animy", "2-3 серия"),
        ("1043:103", "1 сезон", "Animy", "4 серия"), ("1043:201", "2 сезон", "Animy", "1 серия"),
        ("1043:202", "2 сезон", "Animy", "2 серия"),
        ("910:301", "1 сезон", "AniStar", "1 серия"), ("910:302", "1 сезон", "AniStar", "2 серия")]
    assert found[0].ref.url == f"{PAGE}#kodik=50228/7bbb4c6b/101" and found[3].ref.url == f"{PAGE}#kodik=50229/7bbb4c6c/201"
    assert found[1].site_fields == {"value": "3", "translation_id": "1043"} and found[0].voice.kind == "voice"
    # The iframe the page names is the one of Animy, and it is not opened again for its own voice-over.
    assert web.count("/serial/50228/") == 1 and web.count("/serial/50229/") == 1 and web.count("/serial/49002/") == 1


async def test_an_address_that_limits_the_iframe_to_a_voice_reads_that_voice_alone():
    found, web = await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p?only_translations=910")

    assert {e.voice.name for e in found} == {"AniStar"} and len(found) == 2
    assert web.count("/serial/49002/") == 1


async def test_an_iframe_that_lists_no_voices_is_the_one_voice_its_address_names():
    page = signed() + items(("401", "1", "1 серия", ""))
    found, _web = await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p?only_translations=1043",
                                   kodik_web({"50228": page}))

    assert [(e.key, e.voice.name, e.group) for e in found] == [("1043:401", "", "")]


@pytest.mark.parametrize("pages, code", [({}, "player_status"), ({"50228": (500, "")}, "player_status"),
                                         ({"50228": "<html></html>"}, "kodik_no_episode_list")])
async def test_an_iframe_that_does_not_open_is_a_catalog_that_cannot_be_read(pages, code):
    with pytest.raises(CatalogError) as refused:
        await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p", kodik_web(pages))

    assert parse(refused.value.reason)["code"] == code


# --- Kodik: one item ----------------------------------------------------------------------------------------------------------

async def test_an_item_reads_by_its_id_and_the_iframe_is_opened_once_for_all_the_items_of_it():
    player, web = kodik.KodikPlayer(), kodik_web()
    async with web.client() as http:
        readings = [player.read(await player.fetch(player.ref_of(PAGE, "50228", "7bbb4c6b", item), http, PAGE))
                    for item in ("101", "102", "103")]
        absent = player.read(await player.fetch(player.ref_of(PAGE, "50228", "7bbb4c6b", "999"), http, PAGE))

    assert [dict(r.files) for r in readings] == [{360: "https://cloud.kodik.test/360.mp4:hls:manifest.m3u8",
                                                  720: "https://cloud.kodik.test/720.mp4:hls:manifest.m3u8"}] * 3
    assert absent.kind is Kind.NOT_FOUND and web.count("/serial/") == 1
    hashes = {form["hash"] for form in web.forms("/ftor")}
    assert hashes == {"h101", "h102", "h103"}


def test_the_link_of_an_item_names_the_serial_and_the_id_of_the_item_and_nothing_else_is_a_link():
    link = kodik.link_of(PAGE, "50228", "7bbb4c6b", "1144202")

    assert kodik.parse_link(link) == ("50228", "7bbb4c6b", "1144202")
    assert kodik.KodikPlayer.owns(link) and not kodik.KodikPlayer.owns(PAGE) and not kodik.KodikPlayer.owns(PAGE + "#cvh=1")


def test_the_cipher_of_the_links_and_the_address_of_an_iframe():
    link = "//cloud.kodik.test/720.mp4:hls:manifest.m3u8"
    assert kodik.rot18_b64(obfuscate(link)) == link
    assert kodik.normalize_iframe(
        "//kodikplayer.com/serial/1/ab/720p?only_episode=true&translations=true&skip_button=1"
    ) == "https://kodikplayer.com/serial/1/ab/720p?translations=true"


# --- CDN VideoHub ---------------------------------------------------------------------------------------------------------------

def playlist_web(playlist: dict | int) -> Web:
    web = Web()
    web.route("/player/sv/playlist", (playlist, "") if isinstance(playlist, int) else json.dumps(playlist))
    web.route("/player/sv/video/", lambda request: (json.dumps({"sources": VIDEO_SOURCES[str(request.url).rsplit("/", 1)[1]],
                                                                 "duration": 600})
                                                    if str(request.url).rsplit("/", 1)[1] in VIDEO_SOURCES else (404, "")))
    return web


async def cvh_entries(playlist: dict | int):
    player, web = CdnVideoHubPlayer(measured), playlist_web(playlist)
    ref = player.find(WIDGET, PAGE)
    async with web.client() as http:
        return await read_catalog(player, ref, http, PAGE)


def test_the_widget_of_a_page_is_the_title_and_the_publisher_the_player_is_embedded_with():
    player = CdnVideoHubPlayer()

    assert player.find(WIDGET, PAGE) == PlayerRef("cvh", PAGE, "54437/1110")
    assert player.find("<p>no player</p>", PAGE) is None
    assert cdnvideohub.widget_of('<video-player data-title-id="7"></video-player>') == (7, cdnvideohub.DEFAULT_PUBLISHER)


async def test_every_item_with_a_video_is_an_entry_none_is_dropped_for_an_upload_before_it_or_a_number_it_lacks():
    found = await cvh_entries({"items": [
        {"cvhId": "c1", "vkId": "v1", "voiceStudio": "A", "voiceType": "x", "season": 1, "episode": 1},
        {"cvhId": "c2", "vkId": "v1", "voiceStudio": "A", "voiceType": "x", "season": 1, "episode": 1},
        {"cvhId": "c3", "vkId": "", "voiceStudio": "A", "season": 1, "episode": 2},
        {"cvhId": "c4", "vkId": "v4k", "voiceType": "x", "season": 3, "episode": 2.5},
        {"vkId": "v9", "voiceStudio": "B", "episode": 7}]})

    assert [(e.key, e.voice.name, e.voice.kind, e.group, e.text) for e in found] == [
        ("c1", "A", "x", "1 сезон", "1 серия"), ("c2", "A", "x", "1 сезон", "1 серия"),
        ("c4", "", "x", "3 сезон", "2.5 серия"), ("v9", "B", "", "", "7 серия")]
    assert found[0].ref.url == f"{PAGE}#cvh=v1" and cdnvideohub.CdnVideoHubPlayer.owns(found[0].ref.url)
    assert found[0].site_fields == {"season": 1, "episode": "1", "vk_id": "v1"}


async def test_an_empty_playlist_is_no_entries_and_a_playlist_that_is_down_is_a_catalog_that_cannot_be_read():
    assert await cvh_entries(204) == []
    with pytest.raises(CatalogError) as refused:
        await cvh_entries(503)

    assert refused.value.transient and parse(refused.value.reason)["code"] == "player_status"


def test_the_player_wants_a_link_with_the_video_and_no_other_is_its_link():
    assert cdnvideohub.vk_id_of(f"{PAGE}#cvh=v1") == "v1" and cdnvideohub.vk_id_of(f"{PAGE}#vk=v1") is None
    assert cdnvideohub.vk_id_of(PAGE) is None


async def test_the_best_rung_measures_the_labels_that_are_not_resolutions():
    rungs = cdnvideohub.read_rungs(json.dumps({"sources": VIDEO_SOURCES["v4k"]}))

    assert [(r.key, r.height) for r in rungs] == [("mpegHighUrl", 720), ("mpeg4kUrl", 0)]
    assert (await cdnvideohub.best_rung(rungs, measured)).height == 1440

    async def unreadable(_rung):
        return None
    assert (await cdnvideohub.best_rung(rungs, unreadable)).key == "mpegHighUrl"


async def test_a_video_reads_with_the_measured_height_and_every_known_file_and_a_missing_one_is_missing():
    player = CdnVideoHubPlayer(measured)
    web = playlist_web({"items": []})
    web.route("/video/empty", json.dumps({"sources": {}}), first=True)
    async with web.client() as http:
        playable = player.read(await player.fetch(player.ref_of(PAGE, "v4k"), http, PAGE))
        empty = player.read(await player.fetch(player.ref_of(PAGE, "empty"), http, PAGE))
        missing = player.read(await player.fetch(player.ref_of(PAGE, "missing"), http, PAGE))

    assert playable.kind is Kind.PLAYABLE and dict(playable.files) == {720: "https://okcdn.test/v4k/720",
                                                                       1440: "https://okcdn.test/v4k/4k"}
    assert (empty.kind, missing.kind) == (Kind.NO_FILE, Kind.NOT_FOUND)


def test_a_playlist_error_is_told_apart_by_whether_asking_again_may_help():
    with pytest.raises(CdnVideoHubError) as down:
        cdnvideohub.read_playlist(cdnvideohub.Answer(503))
    with pytest.raises(CdnVideoHubError) as gone:
        cdnvideohub.read_playlist(cdnvideohub.Answer(403))

    assert down.value.transient and not gone.value.transient
