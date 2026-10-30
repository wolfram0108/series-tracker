"""Unit tests for the dunhuatv.ru provider: title page, CDN VideoHub (CVH) and Kodik."""
import base64
import json

import httpx
import pytest

from modules.sources.video_providers import (DunhuaTvProvider, StreamUnavailable,
                                              get_default_registry)
from core.reasons import parse
from provider_contract import check_release
from modules.sources.players import cdnvideohub, kodik
from modules.sources.players.cdnvideohub import CdnVideoHubError, Rung
from modules.sources.players.contract import Kind

PAGE_URL = "https://dunhuatv.ru/28-praktikuja-ci-sto-tysjach-let-smotret-onlajn.html"

PAGE_HTML = """
<html><head><title>Практикуя ци (2023) дунхуа онлайн</title></head><body>
<h1 class="page__title">Практикуя ци <span>сто тысяч</span> лет</h1>
<details class="faq-item"><summary>Как называется в оригинале?</summary>
<div class="faq-a">Lian Qi Shi Wan Nian (англ. One Hundred Thousand Years of Qi Refining).</div></details>
<div class="playercdn">
<video-player id="myplayer" data-title-id="54437" data-publisher-id="1110" ident="player_1"
 data-aggregator="mali" is-show-voice-only="false"></video-player></div>
<div class="tabs-block__content video-inside d-none">
<iframe data-src="//kodikplayer.com/serial/50228/7bbb4c6b/720p?only_episode=true&amp;x=1"
 loading="lazy"></iframe></div>
</body></html>
"""

KODIK_HTML = """
<script>
var serialId = Number(50228);
var domain = "dunhuatv.ru";
var d_sign = "ds:1";
var pd = "kodikplayer.com";
var pd_sign = "ps:1";
var ref = "https://dunhuatv.ru/";
var ref_sign = "rs:1";
</script>
<div class="serial-translations-box"><select>
<option value="1043" data-id="1043" data-translation-type="voice" data-media-id="50228"
 data-media-hash="7bbb4c6b" data-title="Animy" data-episode-count="1~3" selected="selected">Animy</option>
<option value="910" data-id="910" data-translation-type="voice" data-media-id="49002"
 data-media-hash="b1b36deb" data-title="AniStar" data-episode-count="2">AniStar</option>
</select></div>
<div class="serial-series-box"><select>
<option
  value="1"
  data-id="101"
  data-hash="aa01"
  data-title="1 серия" data-other-translation="false">1 серия</option>
<option value="2" data-id="102" data-hash="aa02" data-title="2 серия" data-other-translation="false">2</option>
<option value="3" data-id="103" data-hash="aa03" data-title="3 серия" selected="selected"
 data-other-translation="false">3</option>
</select></div>
<div class="series-options"><div class="season-1">
<option value="9" data-id="999" data-hash="ff09">9</option></div></div>
"""

PLAYLIST = {"isSerial": True, "items": [
    {"cvhId": "c1", "vkId": "v1", "voiceStudio": "AniMy / RuChiMe", "voiceType": "Многоголосый",
     "season": 1, "episode": 1},
    {"cvhId": "c2", "vkId": "v1s", "voiceStudio": "AniStar", "voiceType": "Многоголосый",
     "season": 1, "episode": 1},
    {"cvhId": "c3", "vkId": "v2", "voiceStudio": "AniMy / RuChiMe", "voiceType": "Многоголосый",
     "season": 1, "episode": 2},
    {"cvhId": "c4", "vkId": "v2s", "voiceStudio": "AniStar", "voiceType": "Многоголосый",
     "season": 1, "episode": 2},
    {"cvhId": "c5", "vkId": "v3x", "voiceStudio": "AniStar", "voiceType": "Неизвестный",
     "season": 1, "episode": 3},
]}

VIDEO_SOURCES = {
    "v1": {"mpegLowUrl": "https://okcdn.test/v1/360", "mpegHighUrl": "https://okcdn.test/v1/720"},
    "v1s": {"mpegHighUrl": "https://okcdn.test/v1s/720", "mpegFullHdUrl": "https://okcdn.test/v1s/1080"},
    "v2": {"mpegLowUrl": "https://okcdn.test/v2/360"},
    "v2s": {"mpegLowUrl": "https://okcdn.test/v2s/360"},
    "v3x": {"mpegMediumUrl": "https://okcdn.test/v3x/480"},
    "v4k": {"mpegHighUrl": "https://okcdn.test/v4k/720", "mpeg4kUrl": "https://okcdn.test/v4k/4k"},
}


KODIK_ANISTAR_HTML = KODIK_HTML.split('<div class="serial-series-box">')[0] + """
<div class="serial-series-box"><select>
<option value="2" data-id="902" data-hash="bb02" data-title="2 серия">2</option>
<option value="3" data-id="903" data-hash="bb03" data-title="3 серия">3</option>
<option value="9" data-id="909" data-hash="bb09" data-title="9 серия">9</option>
</select></div>"""


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


class Site:
    """The whole external surface of the provider, served from memory."""

    def __init__(self) -> None:
        self.page = PAGE_HTML
        self.playlist: dict | int = PLAYLIST
        self.sources = dict(VIDEO_SOURCES)
        self.failing_videos: dict[str, int] = {}
        self.kodik_pages = {"50228": KODIK_HTML, "49002": KODIK_ANISTAR_HTML}
        self.ftor = {"aa01": ftor_links(360, 720), "aa02": ftor_links(480, 720),
                     "aa03": ftor_links(360, 480, 720), "bb02": ftor_links(360),
                     "bb03": ftor_links(480), "bb09": ftor_links(720)}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith("https://dunhuatv.ru/"):
            return httpx.Response(200, text=self.page)
        if url.startswith(f"{cdnvideohub.API}/player/sv/playlist"):
            if isinstance(self.playlist, int):
                return httpx.Response(self.playlist)
            return httpx.Response(200, json=self.playlist)
        if url.startswith(f"{cdnvideohub.API}/player/sv/video/"):
            vk_id = url.rsplit("/", 1)[1]
            if vk_id in self.failing_videos:
                return httpx.Response(self.failing_videos[vk_id])
            if vk_id not in self.sources:
                return httpx.Response(404)
            return httpx.Response(200, json={"sources": self.sources[vk_id], "duration": 600})
        if url.startswith("https://kodikplayer.com/serial/"):
            page = self.kodik_pages.get(url.split("/serial/")[1].split("/")[0])
            return httpx.Response(200, text=page) if page else httpx.Response(404)
        if url == kodik.FTOR_URL:
            form = dict(x.split("=", 1) for x in request.content.decode().split("&"))
            body = self.ftor.get(form["hash"])
            return httpx.Response(200, json=body) if body else httpx.Response(404)
        return httpx.Response(404)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


async def fake_measure(rung: Rung) -> int | None:
    return {"mpeg4kUrl": 1440}.get(rung.key)


@pytest.fixture
def site() -> Site:
    return Site()


@pytest.fixture
def provider() -> DunhuaTvProvider:
    return DunhuaTvProvider(measure=fake_measure)




def test_match_only_title_pages(provider):
    assert provider.match_url(PAGE_URL)
    assert provider.match_url("https://www.dunhuatv.ru/5-x.html#cvh=1")
    assert not provider.match_url("https://dunhuatv.ru/page/2/")
    assert not provider.match_url("https://dunhuatv.ru/o-nas.html")
    assert not provider.match_url("https://1.ruchime.org/release/x")
    assert provider.canonical_url(PAGE_URL + "?a=1#vk=2") == PAGE_URL
    with pytest.raises(ValueError):
        provider.canonical_url("https://dunhuatv.ru/page/2/")


def test_default_registry_routes_by_host():
    registry = get_default_registry()
    assert registry.find_provider(PAGE_URL).name == "dunhuatv"
    assert registry.find_provider("https://1.ruchime.org/release/x").name == "ruchime"




def test_title_page_fields():
    from modules.sources.video_providers.dunhuatv import parse_title_page
    page = parse_title_page(PAGE_HTML)
    assert page.name_ru == "Практикуя ци сто тысяч лет"
    assert page.name_en == "Lian Qi Shi Wan Nian"
    assert (page.aggregator_id, page.publisher_id) == (54437, 1110)
    assert page.kodik_frame == "https://kodikplayer.com/serial/50228/7bbb4c6b/720p?x=1"


def test_title_page_without_players():
    from modules.sources.video_providers.dunhuatv import parse_title_page
    page = parse_title_page("<h1>Трейлер</h1>", "fallback")
    assert page.aggregator_id is None and page.kodik_frame is None
    assert parse_title_page("<p>x</p>", "fallback").name_ru == "fallback"




async def fetch_playlist(http):
    return cdnvideohub.read_playlist(await cdnvideohub.playlist_request(http, 1, 1110))


@pytest.mark.asyncio
async def test_playlist_keeps_playable_items_and_last_duplicate(site):
    site.playlist = {"items": [
        {"vkId": "old", "voiceStudio": "A", "voiceType": "x", "season": 1, "episode": 1},
        {"vkId": "new", "voiceStudio": "A", "voiceType": "x", "season": 1, "episode": 1},
        {"vkId": "", "voiceStudio": "A", "season": 1, "episode": 2},
        {"vkId": "frac", "voiceStudio": "A", "season": 1, "episode": 2.5},
        {"vkId": "other", "voiceStudio": "B", "voiceType": "x", "season": 1, "episode": 1}]}
    async with site.client() as http:
        items = await fetch_playlist(http)
    assert [(i.studio, i.vk_id) for i in items] == [("A", "new"), ("B", "other")]


@pytest.mark.asyncio
async def test_playlist_empty_answer_is_no_items(site):
    site.playlist = 204
    async with site.client() as http:
        assert await fetch_playlist(http) == []


@pytest.mark.asyncio
async def test_playlist_server_error_is_transient(site):
    site.playlist = 503
    async with site.client() as http:
        with pytest.raises(CdnVideoHubError) as caught:
            await fetch_playlist(http)
    assert caught.value.transient


@pytest.mark.asyncio
async def test_best_rung_measures_labels_that_are_not_resolutions():
    rungs = cdnvideohub.read_rungs(json.dumps({"sources": VIDEO_SOURCES["v4k"]}))
    assert [(r.key, r.height) for r in rungs] == [("mpegHighUrl", 720), ("mpeg4kUrl", 0)]
    assert (await cdnvideohub.best_rung(rungs, fake_measure)).height == 1440

    async def unreadable(_rung):
        return None
    fallback = await cdnvideohub.best_rung(rungs, unreadable)
    assert (fallback.key, fallback.height) == ("mpegHighUrl", 720)


@pytest.mark.asyncio
async def test_a_video_without_links_or_answer_reads_as_no_file_or_missing(site):
    site.sources["empty"] = {}
    player = cdnvideohub.CdnVideoHubPlayer(fake_measure)
    async with site.client() as http:
        empty = player.read(await player.fetch(player.ref_of(PAGE_URL, "empty"), http, PAGE_URL))
        missing = player.read(await player.fetch(player.ref_of(PAGE_URL, "missing"), http, PAGE_URL))
    assert (empty.kind, missing.kind) == (Kind.NO_FILE, Kind.NOT_FOUND)


@pytest.mark.asyncio
async def test_a_cvh_video_reads_with_the_measured_height_and_every_known_file(site):
    player = cdnvideohub.CdnVideoHubPlayer(fake_measure)
    async with site.client() as http:
        reading = player.read(await player.fetch(player.ref_of(PAGE_URL, "v4k"), http, PAGE_URL))
    assert reading.kind is Kind.PLAYABLE and reading.resolutions == (720, 1440)
    assert dict(reading.files) == {720: "https://okcdn.test/v4k/720", 1440: "https://okcdn.test/v4k/4k"}


def test_kodik_page_parts():
    voices = kodik.parse_voices(KODIK_HTML)
    assert [(v.studio, v.media_id, v.media_hash) for v in voices] == [
        ("Animy", "50228", "7bbb4c6b"), ("AniStar", "49002", "b1b36deb")]
    # The duplicate list after the series box and the selected option are handled.
    assert kodik.parse_episodes(KODIK_HTML) == {
        1: ("101", "aa01"), 2: ("102", "aa02"), 3: ("103", "aa03")}
    assert kodik.parse_signatures(KODIK_HTML)["ref"] == "https://dunhuatv.ru/"
    with pytest.raises(kodik.KodikError):
        kodik.parse_signatures("<html></html>")


def test_kodik_cipher_roundtrip():
    link = "//cloud.kodik.test/720.mp4:hls:manifest.m3u8"
    assert kodik.rot18_b64(obfuscate(link)) == link


def test_kodik_iframe_url_drops_truncating_parameters():
    assert kodik.normalize_iframe(
        "//kodikplayer.com/serial/1/ab/720p?only_episode=true&translations=true&skip_button=1"
    ) == "https://kodikplayer.com/serial/1/ab/720p?translations=true"
    assert kodik.normalize_iframe("//kodikplayer.com/serial/1/ab/720p") \
        == "https://kodikplayer.com/serial/1/ab/720p"


@pytest.mark.asyncio
async def test_kodik_qualities(site):
    player = kodik.KodikPlayer()
    async with site.client() as http:
        reading = player.read(await player.fetch(player.ref_of(PAGE_URL, "50228", "7bbb4c6b", 3), http, PAGE_URL))
        assert reading.kind is Kind.PLAYABLE
        assert dict(reading.files) == {h: f"https://cloud.kodik.test/{h}.mp4:hls:manifest.m3u8" for h in (360, 480, 720)}
        absent = player.read(await player.fetch(player.ref_of(PAGE_URL, "50228", "7bbb4c6b", 99), http, PAGE_URL))
        assert absent.kind is Kind.NOT_FOUND


@pytest.mark.asyncio
async def test_kodik_opens_an_iframe_once_for_all_the_episodes_of_a_voice(site):
    player = kodik.KodikPlayer()
    async with site.client() as http:
        for episode in (1, 2, 3):
            await player.fetch(player.ref_of(PAGE_URL, "50228", "7bbb4c6b", episode), http, PAGE_URL)
    assert sum(1 for request in site.requests if "/serial/" in str(request.url)) == 1


def test_address_roundtrip_keeps_the_choice():
    from modules.sources.video_providers.dunhuatv import (Choice, address_of, split_address)
    choice = Choice("cvh", "AniMy / RuChiMe", "Многоголосый")
    address = address_of(PAGE_URL, choice)
    assert split_address(address) == (PAGE_URL, choice)
    assert split_address(PAGE_URL) == (PAGE_URL, None)
    assert split_address(f"{PAGE_URL}?player=bogus&voice=x") == (PAGE_URL, None)
    assert DunhuaTvProvider().canonical_url(address) == address
    assert DunhuaTvProvider().canonical_url(PAGE_URL + "#cvh=1") == PAGE_URL
    assert DunhuaTvProvider().match_url(address)


def test_legacy_vk_player_name_is_an_alias_of_cvh():
    from modules.sources.video_providers.dunhuatv import Choice, split_address
    assert split_address(f"{PAGE_URL}?player=vk&voice=A")[1] == Choice("cvh", "A")


@pytest.mark.asyncio
async def test_legacy_vk_links_still_resolve(site, provider):
    async with site.client() as http:
        stream = await provider.resolve_stream(f"{PAGE_URL}#vk=v1s", http)
    assert stream["stream_url"] == "https://okcdn.test/v1s/1080"


def test_episode_ranges_show_holes():
    from modules.sources.video_providers.dunhuatv import episode_ranges
    assert episode_ranges([1, 2, 3, 5, 7, 8, 3]) == "1-3, 5, 7-8"
    assert episode_ranges([]) == ""




@pytest.mark.asyncio
async def test_parse_release_lists_every_player_and_voice(site, provider):
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    check_release(provider, release)
    options = [(s["player"], s["studio"], s["kind"], s["ranges"], s["count"])
               for s in release["sources"]]
    assert options == [
        ("cvh", "AniMy / RuChiMe", "Многоголосый", "1-2", 2),
        ("cvh", "AniStar", "Многоголосый", "1-2", 2),
        ("cvh", "AniStar", "Неизвестный", "3", 1),
        ("kodik", "Animy", "voice", "1-3", 3),
        ("kodik", "AniStar", "voice", "2-3, 9", 3),
    ]
    assert [s["episodes"] for s in release["sources"]] == [[1, 2], [1, 2], [3], [1, 2, 3], [2, 3, 9]]
    first = release["sources"][0]
    assert release["selected_source"] == first["url"] == release["canonical_url"]
    assert (first["first"], first["last"]) == (1, 2)
    assert release["title"] == {"ru": "Практикуя ци сто тысяч лет", "en": "Lian Qi Shi Wan Nian"}
    assert release["resolution"] is None and release["source_type"] == "web_video"
    assert release["players"]["cvh"]["available"] and release["players"]["kodik"]["available"]
    assert not any("/player/sv/video/" in str(r.url) or r.url.path.endswith("/ftor")
                   for r in site.requests)


@pytest.mark.asyncio
async def test_parse_release_titles_follow_the_chosen_voice(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    async with site.client() as http:
        vk = await provider.parse_release(
            address_of(PAGE_URL, Choice("cvh", "AniStar", "Неизвестный")), http)
        kodik_release = await provider.parse_release(
            address_of(PAGE_URL, Choice("kodik", "AniStar", "voice")), http)
    assert [e["number"] for e in vk["episodes"]] == [3]
    assert vk["titles"] == ["Практикуя ци сто тысяч лет - 3 серия"]
    assert [e["number"] for e in kodik_release["episodes"]] == [2, 3, 9]
    assert kodik_release["episodes_count"] == 3
    assert "player=kodik" in kodik_release["canonical_url"]


@pytest.mark.asyncio
async def test_parse_release_rejects_an_unknown_voice(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.parse_release(address_of(PAGE_URL, Choice("cvh", "Nobody")), http)
    assert parse(str(refused.value))["code"] == "voice_not_found"


@pytest.mark.asyncio
async def test_parse_release_falls_back_to_kodik_when_cvh_is_absent(site, provider):
    site.page = PAGE_HTML.replace("<video-player", "<x-player")
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    assert {s["player"] for s in release["sources"]} == {"kodik"}
    assert "player=kodik" in release["selected_source"]
    assert not release["players"]["cvh"]["available"]
    assert parse(release["players"]["cvh"]["reason"]) == {"code": "page_no_player", "params": {"player": "CVH"}}


def _second_season(*episodes, studio="AniStar"):
    items = [{"cvhId": f"s2-{n}", "vkId": f"s2v{n}", "voiceStudio": studio, "voiceType": "Многоголосый",
              "season": 2, "episode": n} for n in episodes]
    return {"isSerial": True, "items": PLAYLIST["items"] + items}


@pytest.mark.asyncio
async def test_parse_release_refuses_a_cvh_catalog_whose_seasons_share_episode_numbers(site, provider):
    site.playlist = _second_season(1)
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    assert {s["player"] for s in release["sources"]} == {"kodik"}
    assert not release["players"]["cvh"]["available"]
    assert parse(release["players"]["cvh"]["reason"]) == {
        "code": "season_clash", "params": {"player": "CVH", "episodes": "1"}}


@pytest.mark.asyncio
async def test_a_cvh_catalog_without_other_players_names_the_season_clash(site, provider):
    site.playlist = _second_season(1, 2)
    site.page = PAGE_HTML.replace("<iframe", "<x-frame")
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.parse_release(PAGE_URL, http)
    causes = parse(str(refused.value))["params"]["causes"]
    assert parse(causes[0])["code"] == "season_clash" and parse(causes[0])["params"]["episodes"] == "1, 2"


@pytest.mark.asyncio
async def test_a_number_that_two_seasons_share_is_refused_even_between_different_voice_overs(site, provider):
    site.playlist = _second_season(1, studio="Some Other Studio")
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    assert {s["player"] for s in release["sources"]} == {"kodik"}
    assert parse(release["players"]["cvh"]["reason"])["code"] == "season_clash"


@pytest.mark.asyncio
async def test_seasons_with_distinct_episode_numbers_lose_nothing(site, provider):
    site.playlist = _second_season(4, 5)
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    cvh = {(s["studio"], s["kind"]): s["episodes"] for s in release["sources"] if s["player"] == "cvh"}
    assert cvh[("AniStar", "Многоголосый")] == [1, 2, 4, 5] and cvh[("AniMy / RuChiMe", "Многоголосый")] == [1, 2]


@pytest.mark.asyncio
async def test_parse_release_without_any_player_is_an_error(site, provider):
    site.page = "<h1>Пусто</h1>"
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.parse_release(PAGE_URL, http)
    assert parse(str(refused.value))["code"] == "no_players"




def _links(scan):
    return {int(f["title"].split(" - ")[1].split()[0]): (f["url"].split("#")[1], f["resolution"])
            for f in scan["videos"]}


@pytest.mark.asyncio
async def test_scan_takes_only_the_chosen_cvh_voice(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    url = address_of(PAGE_URL, Choice("cvh", "AniStar", "Многоголосый"))
    async with site.client() as http:
        scan = await provider.scan_episodes(url, http, {})
    assert _links(scan) == {1: ("cvh=v1s", 1080), 2: ("cvh=v2s", 360)}
    probed = {str(r.url).rsplit("/", 1)[1] for r in site.requests if "/player/sv/video/" in str(r.url)}
    assert probed == {"v1s", "v2s"}
    assert not any(r.url.path.endswith("/ftor") for r in site.requests)
    assert all(f["download_url"] is None and f["publication_date"] == "1970-01-01T12:00:00Z"
               for f in scan["videos"])
    report = scan["players"]
    assert report["sources"] == {"cvh": 2} and report["holes"] == []
    assert report["source"] == {"player": "cvh", "studio": "AniStar", "kind": "Многоголосый"}
    assert report["players"]["cvh"]["max_resolution"] == 1080


@pytest.mark.asyncio
async def test_scan_takes_only_the_chosen_kodik_voice(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    url = address_of(PAGE_URL, Choice("kodik", "AniStar", "voice"))
    async with site.client() as http:
        scan = await provider.scan_episodes(url, http, {})
    base = f"kodik=49002/b1b36deb/"
    assert _links(scan) == {2: (base + "2", 360), 3: (base + "3", 480), 9: (base + "9", 720)}
    assert not any("/player/sv/" in str(r.url) for r in site.requests)
    hashes = {r.content.decode().split("hash=")[1].split("&")[0]
              for r in site.requests if r.url.path.endswith("/ftor")}
    assert hashes == {"bb02", "bb03", "bb09"}


@pytest.mark.asyncio
async def test_scan_without_a_choice_uses_the_first_player_and_voice(site, provider):
    async with site.client() as http:
        scan = await provider.scan_episodes(PAGE_URL, http, {})
    assert scan["players"]["source"]["studio"] == "AniMy / RuChiMe"
    assert _links(scan) == {1: ("cvh=v1", 720), 2: ("cvh=v2", 360)}


@pytest.mark.asyncio
async def test_scan_measures_an_unlabelled_rung_once(site):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    site.playlist = {"items": [{"vkId": "v4k", "voiceStudio": "A", "voiceType": "x",
                                "season": 1, "episode": 1}]}
    calls = []

    async def counting(rung):
        calls.append(rung.key)
        return 1440
    provider = DunhuaTvProvider(measure=counting)
    async with site.client() as http:
        scan = await provider.scan_episodes(
            address_of(PAGE_URL, Choice("cvh", "A", "x")), http, {})
        await provider.resolve_stream(scan["videos"][0]["url"], http)
    assert scan["videos"][0]["resolution"] == 1440
    assert calls == ["mpeg4kUrl"]


@pytest.mark.asyncio
async def test_scan_respects_episode_filter_and_series_name(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    url = address_of(PAGE_URL, Choice("kodik", "Animy", "voice"))
    async with site.client() as http:
        scan = await provider.scan_episodes(url, http, {"name": "Моя серия"}, episodes=[3])
    assert [f["title"] for f in scan["videos"]] == ["Моя серия - 3 серия"]
    assert sum(r.url.path.endswith("/ftor") for r in site.requests) == 1


@pytest.mark.asyncio
async def test_scan_reports_episode_without_a_stream(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    site.sources["v2"] = {}
    async with site.client() as http:
        scan = await provider.scan_episodes(
            address_of(PAGE_URL, Choice("cvh", "AniMy / RuChiMe", "Многоголосый")), http, {})
    assert _links(scan) == {1: ("cvh=v1", 720)}
    assert scan["players"]["holes"][0]["episode"] == 2
    assert parse(scan["players"]["holes"][0]["reason"])["params"]["player"] == "CVH"


@pytest.mark.asyncio
async def test_a_throttled_episode_is_unverified_and_the_others_are_kept(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    site.failing_videos["v2"] = 503
    async with site.client() as http:
        scan = await provider.scan_episodes(
            address_of(PAGE_URL, Choice("cvh", "AniMy / RuChiMe", "Многоголосый")), http, {})
    assert _links(scan) == {1: ("cvh=v1", 720)}
    assert [(hole["episode"], hole["unverified"]) for hole in scan["players"]["holes"]] == [(2, True)]
    assert scan["players"]["players"]["cvh"]["unverified"] == 1


@pytest.mark.asyncio
async def test_scan_asks_for_a_retry_when_no_episode_could_be_read(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    site.failing_videos.update({"v1": 503, "v2": 503})
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.scan_episodes(
                address_of(PAGE_URL, Choice("cvh", "AniMy / RuChiMe", "Многоголосый")), http, {})
    assert parse(str(refused.value))["code"] == "episodes_unread"


@pytest.mark.asyncio
async def test_scan_asks_for_a_retry_when_the_playlist_is_down(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    site.playlist = 503
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.scan_episodes(
                address_of(PAGE_URL, Choice("cvh", "AniMy / RuChiMe", "Многоголосый")), http, {})
    assert parse(str(refused.value))["code"] in {"episodes_unread", "players_silent"}


@pytest.mark.asyncio
async def test_scan_of_a_vanished_voice_is_an_error_not_a_switch(site, provider):
    from modules.sources.video_providers.dunhuatv import Choice, address_of
    async with site.client() as http:
        with pytest.raises(ValueError) as refused:
            await provider.scan_episodes(address_of(PAGE_URL, Choice("kodik", "Gone", "voice")),
                                         http, {})
    assert parse(str(refused.value))["code"] == "voice_not_found"


@pytest.mark.asyncio
async def test_scan_reports_progress(site, provider):
    seen = []
    async with site.client() as http:
        await provider.scan_episodes(PAGE_URL, http, {},
                                     progress=lambda done, total, phase: seen.append((done, total, phase)))
    assert seen[-1] == (2, 2, "done") and seen[0][2] == "players"




@pytest.mark.asyncio
async def test_resolve_cvh_gives_best_rung_with_the_agent_that_signed_it(site, provider):
    async with site.client() as http:
        stream = await provider.resolve_stream(f"{PAGE_URL}#cvh=v1s", http)
    assert stream["stream_url"] == "https://okcdn.test/v1s/1080"
    assert stream["headers"]["User-Agent"] == cdnvideohub.USER_AGENT
    assert stream["headers"]["Referer"] == cdnvideohub.ORIGIN + "/"
    api_agents = {r.headers["user-agent"] for r in site.requests if "/player/sv/video/" in str(r.url)}
    assert api_agents == {cdnvideohub.USER_AGENT}


@pytest.mark.asyncio
async def test_resolve_kodik_gives_highest_quality(site, provider):
    async with site.client() as http:
        stream = await provider.resolve_stream(f"{PAGE_URL}#kodik=50228/7bbb4c6b/3", http)
    assert stream["stream_url"] == "https://cloud.kodik.test/720.mp4:hls:manifest.m3u8"
    assert stream["headers"] == {"Referer": "https://kodikplayer.com/"}


@pytest.mark.asyncio
async def test_resolve_ignores_foreign_links(site, provider):
    async with site.client() as http:
        assert await provider.resolve_stream("https://vk.com/video-1_2", http) is None
        assert await provider.resolve_stream(PAGE_URL, http) is None
        assert await provider.resolve_stream("https://1.ruchime.org/release/x#cvh=1", http) is None


@pytest.mark.asyncio
async def test_resolve_failures_are_stream_unavailable(site, provider):
    async with site.client() as http:
        with pytest.raises(StreamUnavailable) as gone:
            await provider.resolve_stream(f"{PAGE_URL}#cvh=missing", http)
        assert not gone.value.transient and parse(gone.value.reason)["params"]["player"] == "CVH"
        site.failing_videos["v2"] = 503
        with pytest.raises(StreamUnavailable) as busy:
            await provider.resolve_stream(f"{PAGE_URL}#cvh=v2", http)
        assert busy.value.transient
        with pytest.raises(StreamUnavailable):
            await provider.resolve_stream(f"{PAGE_URL}#kodik=50228/7bbb4c6b/99", http)
        with pytest.raises(StreamUnavailable):
            await provider.resolve_stream(f"{PAGE_URL}#kodik=garbage", http)


@pytest.mark.asyncio
async def test_registry_resolves_dunhuatv_links_and_leaves_ruchime_alone(site):
    registry = get_default_registry()
    async with site.client() as http:
        stream = await registry.resolve_stream(f"{PAGE_URL}#cvh=v1", http)
        assert stream["stream_url"] == "https://okcdn.test/v1/720"
        assert await registry.resolve_stream("https://example.test/none", http) is None
    json.dumps(stream)




@pytest.mark.asyncio
async def test_cvh_stream_takes_the_best_rung_within_the_cap(site, provider):
    link = f"{PAGE_URL}#cvh=v1s"
    async with site.client() as http:
        assert (await provider.resolve_stream(link, http, 720))["stream_url"].endswith("/v1s/720")
        # Nothing at or below 480: the lowest rung above it is better than losing the episode.
        assert (await provider.resolve_stream(link, http, 480))["stream_url"].endswith("/v1s/720")
        assert (await provider.resolve_stream(link, http))["stream_url"].endswith("/v1s/1080")
        assert (await provider.resolve_stream(link, http, 2160))["stream_url"].endswith("/v1s/1080")


@pytest.mark.asyncio
async def test_unlabelled_rungs_are_measured_only_when_nothing_else_is_left(site):
    site.sources["only4k"] = {"mpeg4kUrl": "https://okcdn.test/only4k/4k"}
    calls = []

    async def counting(rung):
        calls.append(rung.key)
        return 1440
    provider = DunhuaTvProvider(measure=counting)
    async with site.client() as http:
        await provider.resolve_stream(f"{PAGE_URL}#cvh=v4k", http, 720)
        assert calls == []
        stream = await provider.resolve_stream(f"{PAGE_URL}#cvh=only4k", http, 720)
    assert stream["stream_url"].endswith("/only4k/4k") and calls == ["mpeg4kUrl"]


@pytest.mark.asyncio
async def test_kodik_stream_takes_the_best_quality_within_the_cap(site, provider):
    link = f"{PAGE_URL}#kodik=50228/7bbb4c6b/3"
    async with site.client() as http:
        heights = {cap: (await provider.resolve_stream(link, http, cap))["stream_url"]
                   for cap in (480, 300, None)}
    assert heights[480].endswith("/480.mp4:hls:manifest.m3u8")
    assert heights[300].endswith("/360.mp4:hls:manifest.m3u8")
    assert heights[None].endswith("/720.mp4:hls:manifest.m3u8")


@pytest.mark.asyncio
async def test_registry_passes_the_cap_to_the_provider(site):
    async with site.client() as http:
        stream = await get_default_registry().resolve_stream(f"{PAGE_URL}#cvh=v1s", http, 720)
    assert stream["stream_url"].endswith("/v1s/720")


@pytest.mark.asyncio
async def test_sources_carry_the_link_of_every_episode(site, provider):
    async with site.client() as http:
        release = await provider.parse_release(PAGE_URL, http)
    first, kodik_animy = release["sources"][0], release["sources"][3]
    assert first["links"] == {"1": f"{PAGE_URL}#cvh=v1", "2": f"{PAGE_URL}#cvh=v2"}
    assert kodik_animy["links"]["3"] == f"{PAGE_URL}#kodik=50228/7bbb4c6b/3"
    # The link is exactly what the scan stores as the candidate address.
    async with site.client() as http:
        scan = await provider.scan_episodes(PAGE_URL, http, {})
    assert {f["url"] for f in scan["videos"]} == set(first["links"].values())


@pytest.mark.asyncio
async def test_provider_info_tells_which_provider_has_a_voice_catalog_without_the_network():
    from core import Bus
    from core.db import Database
    from modules.sources import SourcesModule

    bus = Bus()
    sources = SourcesModule(bus, Database(":memory:"))
    asked = {}

    class Env:
        def __init__(self, url):
            self.payload = {"url": url}

    dunhua = await sources.on_provider_info(Env("https://dunhuatv.ru/500-stat-pobeditelem.html?player=cvh"))
    ruchime = await sources.on_provider_info(Env("https://1.ruchime.org/release/some-title"))
    unknown = await sources.on_provider_info(Env("https://example.org/x"))
    assert dunhua == {"provider": "dunhuatv", "voices": True}
    assert ruchime == {"provider": "ruchime", "voices": False}
    assert unknown == {"provider": None, "voices": False}
    assert asked == {}
