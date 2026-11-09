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
from sources_support import STEP_LIMIT_S, Web, within

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
    """Items of a list: (id, value, text, mark); the mark "other" is an item Kodik borrowed from another voice-over."""
    options = "".join(f'<option value="{value}" data-id="{item}" data-hash="h{item}" data-title="{text}"'
                      f' data-other-translation="{"true" if mark == "other" else "false"}">{text}</option>'
                      for item, value, text, mark in triples)
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


async def ask(player, ref, http):
    """What the player reads of one ask; the ask has a limit like every wait of these tests."""
    return player.read(await within(player.fetch(ref, http, PAGE), STEP_LIMIT_S, "the ask of the player"))


async def entries_of(source_url: str, web: Web | None = None):
    player, web = kodik.KodikPlayer(), web or kodik_web()
    async with web.client() as http:
        return await within(read_catalog(player, PlayerRef("kodik", source_url), http, PAGE), STEP_LIMIT_S,
                            "the catalog of the iframe"), web


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


def test_an_iframe_is_found_by_whichever_attribute_holds_its_address():
    player = kodik.KodikPlayer()
    plain = "<iframe src='//kodikplayer.com/serial/77336/e23d/720p' frameborder='0' allowfullscreen></iframe>"
    lazy = '<iframe src="about:blank" data-src="https://kodikplayer.com/serial/2/cd/720p"></iframe>'

    assert player.find(plain, PAGE).url == "https://kodikplayer.com/serial/77336/e23d/720p"
    assert player.find(lazy, PAGE).url == "https://kodikplayer.com/serial/2/cd/720p"


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


def season_page(*triples: tuple[str, str, str, str]) -> str:
    """The iframe of one season of one voice-over, as a site embeds it: no list of voices, the page names the one it plays."""
    return (signed() + '<script>var seasonId = Number(64820); var translationId = 910; var translationTitle = "AniStar";'
            "</script>" + items(*triples))


def season_web() -> Web:
    web = Web()
    web.route("kodikplayer.com/season/64820/", season_page(("501", "1", "1 серия", ""), ("502", "2", "2 серия", "")))
    web.route("kodikplayer.com/ftor", lambda request: json.dumps(ftor_links(360, 720)), method="POST")
    return web


async def test_an_iframe_of_one_season_is_the_voice_its_page_names_and_its_items_are_asked_through_that_season():
    player, web = kodik.KodikPlayer(), season_web()
    source = PlayerRef("kodik", "https://kodikplayer.com/season/64820/8cc4/720p?translations=false&episode=1")
    async with web.client() as http:
        found = await within(read_catalog(player, source, http, PAGE), STEP_LIMIT_S, "the catalog of the season")
        reading = await ask(player, found[1].ref, http)

    assert [(e.key, e.voice.name, e.text) for e in found] == [("910:501", "AniStar", "1 серия"), ("910:502", "AniStar", "2 серия")]
    # The link says the list is a season, so the item is asked of the season it is in; a serial keeps its old spelling.
    assert found[0].ref.url == f"{PAGE}#kodik=season/64820/8cc4/501"
    assert kodik.parse_link(found[0].ref.url) == ("season", "64820", "8cc4", "501")
    assert kodik.KodikPlayer.video_of(found[0].ref.url) == "season/64820/8cc4/501"
    assert kodik.KodikPlayer.video_of(kodik.link_of(PAGE, "50228", "7bbb", "1")) == "50228/7bbb/1"
    assert reading.kind is Kind.PLAYABLE and web.count("/season/64820/") == 1 and web.count("/serial/") == 0


def test_the_addresses_of_one_list_for_each_of_its_episodes_are_one_catalog_and_a_limit_to_a_voice_is_another():
    of = lambda url: kodik.KodikPlayer.catalog_id(PlayerRef("kodik", url))  # noqa: E731

    assert of("https://kodikplayer.com/season/64820/8cc4/720p?translations=false&episode=1") == of(
        "https://kodikplayer.com/season/64820/8cc4/720p?translations=false&episode=7")
    assert of("https://kodikplayer.com/serial/50228/7bbb/720p?only_translations=1043") != of(
        "https://kodikplayer.com/serial/50228/7bbb/720p?only_translations=910")
    assert of("https://kodikplayer.com/season/64820/8cc4/720p") != of("https://kodikplayer.com/serial/64820/8cc4/720p")


@pytest.mark.parametrize("pages, code", [({}, "player_status"), ({"50228": (500, "")}, "player_status"),
                                         ({"50228": "<html></html>"}, "kodik_no_episode_list")])
async def test_an_iframe_that_does_not_open_is_a_catalog_that_cannot_be_read(pages, code):
    with pytest.raises(CatalogError) as refused:
        await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p", kodik_web(pages))

    assert parse(refused.value.reason)["code"] == code


async def test_an_item_the_list_borrowed_from_another_voice_is_not_an_entry_of_this_voice_and_cannot_be_asked():
    """Kodik puts the episode of another voice-over into the list of one that has none of its own (data-other-translation):
    its sound is the other studio's, so it is no row of this one, and asked by its link it is not found."""
    page = (signed() + voices(ANIMY, ANISTAR) + seasons(("1 сезон", "50228", "7bbb4c6b", True))
            + items(("101", "1", "1 серия", ""), ("301", "2", "2 серия", "other"), ("302", "3", "3 серия", "other")))
    pages = {**IFRAMES, "50228": page}
    found, _web = await entries_of("https://kodikplayer.com/serial/50228/7bbb4c6b/720p", kodik_web(pages))

    assert [(e.key, e.voice.name, e.text) for e in found] == [
        ("1043:101", "Animy", "1 серия"), ("910:301", "AniStar", "1 серия"), ("910:302", "AniStar", "2 серия")]
    player, web = kodik.KodikPlayer(), kodik_web(pages)
    async with web.client() as http:
        borrowed = await ask(player, player.ref_of(PAGE, "50228", "7bbb4c6b", "301"), http)
    assert borrowed.kind is Kind.NOT_FOUND and web.count("/ftor") == 0


# --- Kodik: one item ----------------------------------------------------------------------------------------------------------

async def test_an_item_reads_by_its_id_and_the_iframe_is_opened_once_for_all_the_items_of_it():
    player, web = kodik.KodikPlayer(), kodik_web()
    async with web.client() as http:
        readings = [await ask(player, player.ref_of(PAGE, "50228", "7bbb4c6b", item), http)
                    for item in ("101", "102", "103")]
        absent = await ask(player, player.ref_of(PAGE, "50228", "7bbb4c6b", "999"), http)

    assert [dict(r.files) for r in readings] == [{360: "https://cloud.kodik.test/360.mp4:hls:manifest.m3u8",
                                                  720: "https://cloud.kodik.test/720.mp4:hls:manifest.m3u8"}] * 3
    assert absent.kind is Kind.NOT_FOUND and web.count("/serial/") == 1
    hashes = {form["hash"] for form in web.forms("/ftor")}
    assert hashes == {"h101", "h102", "h103"}


def test_the_link_of_an_item_names_the_serial_and_the_id_of_the_item_and_nothing_else_is_a_link():
    link = kodik.link_of(PAGE, "50228", "7bbb4c6b", "1144202")

    assert kodik.parse_link(link) == ("serial", "50228", "7bbb4c6b", "1144202")
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
        return await within(read_catalog(player, ref, http, PAGE), STEP_LIMIT_S, "the playlist")


def test_the_widget_of_a_page_is_the_title_and_the_publisher_the_player_is_embedded_with():
    player = CdnVideoHubPlayer()

    assert player.find(WIDGET, PAGE) == PlayerRef("cvh", PAGE, "54437/1110/mali")
    assert player.find("<p>no player</p>", PAGE) is None
    other = '<video-player data-title-id="62248" data-publisher-id="1126" data-aggregator="kima"></video-player>'
    assert player.find(other, PAGE) == PlayerRef("cvh", PAGE, "62248/1126/kima")


async def test_the_playlist_is_asked_as_the_widget_names_it():
    web = playlist_web({"items": []})
    player = CdnVideoHubPlayer()
    ref = player.find('<video-player data-title-id="62248" data-publisher-id="1126" data-aggregator="kima">', PAGE)
    async with web.client() as http:
        await within(read_catalog(player, ref, http, PAGE), STEP_LIMIT_S, "the playlist")

    asked = [request.url.params for request in web.requests if "/playlist" in str(request.url)]
    assert [(params["id"], params["pub"], params["aggr"]) for params in asked] == [("62248", "1126", "kima")]
    assert cdnvideohub.widget_of('<video-player data-title-id="7"></video-player>') == (
        7, cdnvideohub.DEFAULT_PUBLISHER, cdnvideohub.DEFAULT_AGGREGATOR)


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


# One video as the CDN gave it: the label "4k" was measured at 1440 and the label "2k" at 2160.
SWAPPED = {"mpegHighUrl": "https://okcdn.test/sw/720", "mpegFullHdUrl": "https://okcdn.test/sw/1080",
           "mpeg2kUrl": "https://okcdn.test/sw/2k", "mpeg4kUrl": "https://okcdn.test/sw/4k"}


async def swapped(rung: Rung) -> int | None:
    return {"mpeg2kUrl": 2160, "mpeg4kUrl": 1440}.get(rung.key)


async def test_the_labels_above_full_hd_do_not_order_the_frames_so_every_one_is_measured():
    rungs = cdnvideohub.read_rungs(json.dumps({"sources": SWAPPED}))

    assert (await cdnvideohub.best_rung(rungs, swapped)).key == "mpeg2kUrl"
    assert [(r.key, r.height) for r in await cdnvideohub.measured_rungs(rungs, swapped)] == [
        ("mpegHighUrl", 720), ("mpegFullHdUrl", 1080), ("mpeg2kUrl", 2160), ("mpeg4kUrl", 1440)]
    assert {cap: (await cdnvideohub.rung_within(rungs, cap, swapped)).key for cap in (2160, 1440, 1200)} == {
        2160: "mpeg2kUrl", 1440: "mpeg4kUrl", 1200: "mpegFullHdUrl"}


async def test_a_cap_met_by_a_labelled_rung_measures_nothing():
    calls: list[str] = []

    async def counting(rung: Rung) -> int | None:
        calls.append(rung.key)
        return await swapped(rung)

    rungs = cdnvideohub.read_rungs(json.dumps({"sources": SWAPPED}))

    assert (await cdnvideohub.rung_within(rungs, 1080, counting)).key == "mpegFullHdUrl" and calls == []


async def test_a_measure_that_failed_is_tried_at_the_next_ask():
    answers = iter([None, 2160, 1440])

    async def flaky(rung: Rung) -> int | None:
        return next(answers)

    player = CdnVideoHubPlayer(flaky)
    web = playlist_web({"items": []})
    web.route("/video/sw", json.dumps({"sources": {k: SWAPPED[k] for k in ("mpeg2kUrl", "mpeg4kUrl")}}), first=True)
    async with web.client() as http:
        first = await ask(player, player.ref_of(PAGE, "sw"), http)
        second = await ask(player, player.ref_of(PAGE, "sw"), http)

    assert first.resolutions == (2160,) and second.resolutions == (1440, 2160)


async def test_a_video_reads_every_frame_it_has_whatever_the_labels_say():
    player = CdnVideoHubPlayer(swapped)
    web = playlist_web({"items": []})
    web.route("/video/sw", json.dumps({"sources": SWAPPED}), first=True)
    async with web.client() as http:
        playable = await ask(player, player.ref_of(PAGE, "sw"), http)

    assert playable.resolutions == (720, 1080, 1440, 2160)
    assert dict(playable.files)[2160] == "https://okcdn.test/sw/2k"


async def test_a_video_reads_with_the_measured_height_and_every_known_file_and_a_missing_one_is_missing():
    player = CdnVideoHubPlayer(measured)
    web = playlist_web({"items": []})
    web.route("/video/empty", json.dumps({"sources": {}}), first=True)
    async with web.client() as http:
        playable = await ask(player, player.ref_of(PAGE, "v4k"), http)
        empty = await ask(player, player.ref_of(PAGE, "empty"), http)
        missing = await ask(player, player.ref_of(PAGE, "missing"), http)

    assert playable.kind is Kind.PLAYABLE and dict(playable.files) == {720: "https://okcdn.test/v4k/720",
                                                                       1440: "https://okcdn.test/v4k/4k"}
    assert (empty.kind, missing.kind) == (Kind.NO_FILE, Kind.NOT_FOUND)


def test_a_playlist_error_is_told_apart_by_whether_asking_again_may_help():
    with pytest.raises(CdnVideoHubError) as down:
        cdnvideohub.read_playlist(cdnvideohub.Answer(503))
    with pytest.raises(CdnVideoHubError) as gone:
        cdnvideohub.read_playlist(cdnvideohub.Answer(403))

    assert down.value.transient and not gone.value.transient
