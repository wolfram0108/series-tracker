"""The site 1.ruchime.org read into the table of the page (M-6, M-7), on the snapshots of the real answers in `raw/`.

The release page, its list of 390 episodes, the pages of the first and the last episode and their players are the
snapshots; the episodes between are the page of the last one with a video of their own, so that the table has the whole
list. The catalog of Vibix is the decoded answer of its API, the manifests its CDN gives are valid ones.
"""
import json
import re

import httpx
import pytest

from core.contracts import ReleaseTable, RowState
from core.reasons import parse, reason
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.vibix_protocol import VibixUnavailable, encode_envelope, parse_playlist
from modules.sources.sites import (PHASE_CATALOG, PHASE_EPISODES, PHASE_PLAYERS, PHASE_RELEASE, ReadContext, SiteError,
                                   SiteRegistry)
from modules.sources.sites.ruchime import RuchimeSite
from sources_support import READ_LIMIT_S, Web, json_of, text_of, within

SLUG = "praktikuia-ci-sto-tysiac-let"
RELEASE = f"https://1.ruchime.org/release/{SLUG}"
KEY = b"K" * 32
MANIFEST = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\nseg.ts\n#EXT-X-ENDLIST\n"
FILLER_VK = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
FILLER_ALLVIDEO = '<script>file:"[360p]https://cdn.test/360.mp4/,[1080p]https://cdn.test/1080.mp4/"</script>'
DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
# The videos of the snapshots: the embed page each player gave for the first and for the last episode.
RAW_VK = {"456246539": "run1/ruchime__episode-1__vk_embed.html", "456256559": "run1/ruchime__episode-390__vk_embed.html"}
RAW_ALLVIDEO = {"688273": "run1/ruchime__episode-1__allvideo_embed.html",
                "1081867": "run1/ruchime__episode-390__allvideo_embed.html"}


def episode_page(number: int) -> str:
    if number == 1:
        return text_of("run1/ruchime__episode-1.html")
    last = text_of("run1/ruchime__episode-390.html")
    if number == 390:
        return last
    return (last.replace("456256559", f"4{number:08d}").replace("844d706ebb47dffa", f"{number:016x}")
            .replace("1081867", f"{9_000_000 + number}"))


def release_with(entries: list[dict]) -> str:
    """The snapshot of the release page with another list of episodes in place of its own."""
    return re.sub(r"const ALL_EPS\s*=\s*\[.*?\];", lambda _: "const ALL_EPS = " + json.dumps(entries) + ";",
                  text_of("run1/ruchime__release.html"), count=1, flags=re.DOTALL)


def entry(number: int, **fields) -> dict:
    return {"id": 1000 + number, "number": number, "compilation_from": None, "compilation_to": None,
            "title": f"Серия {number}", "is_vip": False, "is_trailer": False, "date": "09 May 2026", **fields}


def embed_by(known: dict, filler: str):
    def answer(request: httpx.Request):
        url = str(request.url)
        return next((text_of(name) for marker, name in known.items() if marker in url), filler)
    return answer


def kodik_iframe(request: httpx.Request):
    """The iframe of Animy, the voice-over the pages of the release name: the snapshot of the iframe of dunhuatv."""
    return text_of("run1/dunhuatv__kodik_voice0.html") if "/serial/50228/" in str(request.url) else (404, "")


def web_of(release: str | None = None, *, episodes=episode_page) -> Web:
    web = Web()
    web.route(f"/release/{SLUG}/episode-", lambda request: episodes(int(str(request.url).rsplit("-", 1)[1])))
    web.route(f"/release/{SLUG}", release if release is not None else text_of("run1/ruchime__release.html"))
    web.route("video_ext.php", embed_by(RAW_VK, FILLER_VK))
    web.route("/embed/", embed_by(RAW_ALLVIDEO, FILLER_ALLVIDEO))
    web.route("al_video.php", DENIED, method="POST")
    web.route("kodikplayer.com/serial/", kodik_iframe)
    web.route("kodikplayer.com/ftor", text_of("run1/dunhuatv__kodik_ftor.json"), method="POST")
    web.route("kinescopecdn.net", lambda request: (200, encode_envelope(MANIFEST, KEY).decode()))
    return web


def catalog_of(site: RuchimeSite, snapshot: str, failure: Exception | None = None) -> list[str]:
    """Put the decoded catalog of a snapshot where the browser capture of Vibix would put it; the pages asked are returned."""
    asked: list[str] = []

    async def playlist(page_url: str):
        asked.append(page_url)
        if failure is not None:
            raise failure
        return parse_playlist(json_of(f"run3_players/vibix__{snapshot}__api_decoded.json")), KEY

    site.players.get("vibix").playlist = playlist
    return asked


def a_site(snapshot: str = "asura-bezumnyi-korol-2") -> RuchimeSite:
    site = RuchimeSite(PlayerRegistry.standard())
    catalog_of(site, snapshot)
    return site


class Run:
    def __init__(self) -> None:
        self.progress: list[tuple[str, int, int]] = []

    async def read(self, site: RuchimeSite, web: Web):
        async with web.client() as http:
            return await within(site.read(RELEASE, ReadContext(http, lambda *step: self.progress.append(step))),
                                READ_LIMIT_S, "the read of the release")


# --- the whole page: the full list by every player and the catalog (M-7) ---------------------------------------------------

_READ: list = []


@pytest.fixture
async def whole():
    """The one read of the whole release that the tests below look at from different sides (it is 1532 asks)."""
    if not _READ:
        site, run, web = RuchimeSite(PlayerRegistry.standard()), Run(), web_of()
        pages = catalog_of(site, SLUG)
        _READ.append((await run.read(site, web), run.progress, web, pages))
    return _READ[0]


def of(draft, player: str, number: int):
    return next(row for row in draft.rows if row.player == player and row.site_fields.get("number") == number)


async def test_the_head_of_the_table_has_the_names_and_the_voice_the_site_gives(whole):
    draft, *_ = whole

    assert draft.names == ["Практикуя ци сто тысяч лет", "炼气十万年"]
    assert draft.site_voice == "Ruchime"


async def test_every_entry_of_the_list_is_a_row_of_every_player_its_page_shows_and_the_catalogs_add_their_own(whole):
    draft, *_ = whole
    by_player = {name: [row for row in draft.rows if row.player == name] for name in ("vk", "allvideo", "vibix", "kodik")}
    listed = json_of("run1/ruchime__ALL_EPS.json")

    assert {name: len(rows) for name, rows in by_player.items()} == {"vk": 390, "allvideo": 390, "vibix": 390, "kodik": 362}
    assert len(listed) == 390 and len({row.key for row in draft.rows}) == len(draft.rows) == 1532
    # The text of a row is the title of the list as the site gives it, in the order of the list.
    assert [row.text for row in by_player["vk"]] == [item["title"] for item in listed]
    assert [row.key for row in by_player["vk"]][:2] == [f"vk:{listed[0]['id']}", f"vk:{listed[1]['id']}"]
    assert ReleaseTable(url=RELEASE, site="ruchime", rows=draft.rows, complete=True, read_at="2026-11-01T12:00:00+00:00")


async def test_no_player_is_left_out_because_a_better_resolution_was_found_already(whole):
    draft, _progress, web, _pages = whole

    assert {row.player: row.heights for row in draft.rows if row.site_fields.get("number") == 3} == {
        "vk": [720, 1080], "allvideo": [360, 1080]}
    assert web.count("video_ext.php") >= 390 and web.count("/embed/") >= 390


async def test_the_voice_of_a_row_is_the_voice_of_the_site_for_the_pages_and_the_name_of_the_stream_for_vibix(whole):
    draft, *_ = whole

    assert {row.voice.name for row in draft.rows if row.player in ("vk", "allvideo")} == {"Ruchime"}
    assert {row.voice.name for row in draft.rows if row.player == "vibix"} == {"RuChiMe"}


async def test_the_rows_of_the_snapshots_say_what_the_players_said(whole):
    draft, *_ = whole

    # VK closes both videos to the client of the snapshot: a refusal about the video is "no", not "unknown".
    for number in (1, 390):
        row = of(draft, "vk", number)
        assert row.state is RowState.ABSENT and row.heights == [] and parse(row.reason)["code"] == "video_private"
    assert (of(draft, "allvideo", 1).state, of(draft, "allvideo", 1).heights) == (RowState.PLAYABLE, [360, 720, 1080])
    assert (of(draft, "allvideo", 390).state, of(draft, "allvideo", 390).heights) == (RowState.PLAYABLE, [360])


async def test_a_vibix_row_is_an_entry_of_the_catalog_with_its_season_its_title_and_its_link(whole):
    draft, *_ = whole
    vibix = [row for row in draft.rows if row.player == "vibix"]

    assert vibix[0].key == "vibix:1/1" and vibix[-1].key == "vibix:1/390"
    assert {row.group for row in vibix} == {"Сезон 1"}
    assert [row.text for row in vibix[:2]] == ["Серия 1", "Серия 2"]
    assert vibix[0].link == f"{RELEASE}/episode-1#vibix=1/1"
    assert all(row.state is RowState.PLAYABLE and row.heights == [1080] for row in vibix)


async def test_the_numbers_of_the_site_are_a_note_on_the_row_and_the_date_is_the_day_the_site_gives(whole):
    draft, *_ = whole
    row = of(draft, "allvideo", 7)

    assert row.site_fields == {"id": row.site_fields["id"], "number": 7, "compilation_from": None,
                               "compilation_to": None, "is_trailer": False, "is_vip": False}
    assert (of(draft, "vk", 1).published, of(draft, "vk", 390).published) == ("2026-05-09", "2026-10-31")
    assert row.link.startswith("https://fsst.online/embed/") and "hash=" in of(draft, "vk", 3).link


UNSTABLE = re.compile(r"expires=|sign=|token|\.mp4|\.m3u8|mp4_|kinescopecdn|api_hash")


async def test_a_link_is_the_stable_address_of_the_video_and_never_a_signed_stream_or_a_token(whole):
    draft, *_ = whole

    assert not [row.link for row in draft.rows if UNSTABLE.search(row.link) or not row.link]
    for row in draft.rows:
        if row.player == "vk":
            assert row.link.startswith("https://vkvideo.ru/video_ext.php?oid=-") and "&hash=" in row.link
        elif row.player == "allvideo":
            assert re.fullmatch(r"https://(fsst|incvideo1)\.online/embed/\d+/", row.link)
        elif row.player == "vibix":
            assert re.fullmatch(re.escape(RELEASE) + r"/episode-1#vibix=1/\d+", row.link)
        else:
            assert re.fullmatch(re.escape(RELEASE) + r"/episode-1#kodik=50228/[0-9a-f]+/\d+", row.link)


async def test_two_reads_of_the_same_answers_give_the_same_link_in_every_row(whole):
    first, *_ = whole

    second = await Run().read(a_site(SLUG), web_of())

    assert [(row.key, row.link) for row in second.rows] == [(row.key, row.link) for row in first.rows]


async def test_kodik_on_the_pages_of_the_release_is_the_voice_the_address_of_its_iframe_names(whole):
    draft, _progress, web, _pages = whole
    kodik = [row for row in draft.rows if row.player == "kodik"]

    # The iframe lists three voice-overs; the address limits it to the one the release has (`only_translations=1043`).
    assert {(row.voice.name, row.voice.kind, row.group) for row in kodik} == {("Animy", "voice", "1 сезон")}
    assert [row.text for row in kodik[:2]] == ["1 серия", "2 серия"] and any(row.text == "109-110 серия" for row in kodik)
    assert (kodik[0].heights, kodik[0].state) == ([360, 480, 720], RowState.PLAYABLE)
    # One iframe serves all the pages that show it.
    assert web.count("kodikplayer.com/serial/") == 1


async def test_the_read_reports_the_release_the_pages_the_catalogs_and_the_players_in_that_order(whole):
    _draft, progress, _web, pages = whole

    assert list(dict.fromkeys(phase for phase, _current, _total in progress)) == [
        PHASE_RELEASE, PHASE_EPISODES, PHASE_CATALOG, PHASE_PLAYERS]
    assert (PHASE_RELEASE, 1, 1) in progress and (PHASE_EPISODES, 390, 390) in progress
    assert (PHASE_CATALOG, 2, 2) in progress and progress[-1] == (PHASE_PLAYERS, 1532, 1532)
    # The catalog is asked through the first page that shows the player, and through no other.
    assert set(pages) == {f"{RELEASE}/episode-1"}


# --- the states of a row (M-6) and the catalog of two seasons ---------------------------------------------------------------

async def test_a_row_is_playable_absent_or_not_obtained_and_the_three_are_told_apart():
    def pages(number: int) -> str:
        return episode_page(390).replace("456256559", f"4{number:08d}").replace("1081867", f"{9_000_000 + number}")

    web = web_of(release_with([entry(number) for number in (1, 2, 3, 4)]), episodes=pages)
    web.route("/embed/9000002", (404, ""), first=True)
    web.route("/embed/9000003", (503, "busy"), first=True)
    site = a_site()
    allvideo = site.players.get("allvideo")
    fetch = allvideo.fetch

    async def fetch_or_break(ref, http, page_url):
        if "9000004" in ref.url:
            raise RuntimeError("the markup changed")
        return await fetch(ref, http, page_url)

    allvideo.fetch = fetch_or_break

    draft = await Run().read(site, web)

    assert (of(draft, "allvideo", 1).state, of(draft, "allvideo", 1).heights) == (RowState.PLAYABLE, [360, 1080])
    # A video the host says is not there is "no"; a host that stays busy to the end is "not obtained".
    assert of(draft, "allvideo", 2).state is RowState.ABSENT
    assert parse(of(draft, "allvideo", 2).reason)["code"] == "player_video_missing"
    assert of(draft, "allvideo", 3).state is RowState.NOT_OBTAINED
    assert parse(of(draft, "allvideo", 3).reason)["code"] == "player_status"
    # A probe that raised is not an answer "no" either.
    assert of(draft, "allvideo", 4).state is RowState.NOT_OBTAINED
    assert parse(of(draft, "allvideo", 4).reason)["code"] == "player_failed"


async def test_the_catalog_of_two_seasons_gives_a_group_of_nine_rows_and_a_group_of_fourteen():
    draft = await Run().read(a_site(), web_of(release_with([entry(1), entry(2)])))
    vibix = [row for row in draft.rows if row.player == "vibix"]

    assert [(group, sum(1 for row in vibix if row.group == group)) for group in ("Сезон 1", "Сезон 2")] == [
        ("Сезон 1", 9), ("Сезон 2", 14)]
    assert vibix[9].key == "vibix:2/1" and vibix[9].text == "Серия 1" and vibix[-1].key == "vibix:2/14"
    assert len({row.key for row in vibix}) == 23


async def test_two_entries_that_share_a_video_are_two_rows_and_the_video_is_asked_once():
    web = web_of(release_with([entry(1), entry(2)]), episodes=lambda number: episode_page(390))

    draft = await Run().read(a_site(), web)
    vk = [row for row in draft.rows if row.player == "vk"]

    assert [row.key for row in vk] == ["vk:1001", "vk:1002"] and vk[0].link == vk[1].link
    assert vk[0].state is vk[1].state is RowState.ABSENT
    assert web.count("id=456256559&hash") == 1


async def test_a_series_is_asked_of_vk_in_one_batch_when_the_player_page_issues_a_token():
    web = web_of(release_with([entry(390), entry(2)]))
    web.route("video_ext.php", lambda request: text_of("run3_players/ruchime__vk_embed_agent0.html"), first=True)
    web.route("/method/video.get", lambda request: text_of("run3_players/ruchime__vk_video_get_agent0.json"),
              method="POST")

    draft = await Run().read(a_site(), web)
    first_form = web.forms("/method/video.get")[0]

    assert first_form["videos"].split(",")[0] == "-170515668_456256559_844d706ebb47dffa"
    assert first_form["videos"].count(",") == 1 and first_form["ref_domain"] == "1.ruchime.org"
    # The batch settled the video it returned: the player page of it was asked only for the token.
    assert web.count("id=456256559&hash") == 1
    assert of(draft, "vk", 390).state is RowState.ABSENT


# --- a read that cannot give the whole page gives none of it ----------------------------------------------------------------

@pytest.mark.parametrize("web, code", [
    (Web().route(f"/release/{SLUG}", (404, "")), "site_status"),
    (Web().route(f"/release/{SLUG}", "<html>no list</html>"), "title_no_videos"),
])
async def test_a_page_without_a_list_or_with_an_error_status_is_refused_with_a_reason(web, code):
    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), web)

    assert parse(refused.value.reason)["code"] == code


async def test_a_host_that_does_not_answer_is_a_refusal_with_a_reason():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), Web().route(f"/release/{SLUG}", down))

    assert parse(refused.value.reason)["code"] == "site_unreachable"


async def test_an_unread_page_of_an_episode_refuses_the_table_instead_of_leaving_a_hole_in_it():
    web = web_of(release_with([entry(1), entry(2), entry(3)]))
    web.route("/episode-2", (500, ""), first=True)

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), web)

    assert parse(refused.value.reason) == {"code": "pages_unread", "params": {"site": "Ruchime", "count": 1, "total": 3}}


async def test_a_catalog_that_cannot_be_captured_refuses_the_table_after_one_more_try():
    site = RuchimeSite(PlayerRegistry.standard())
    asked = catalog_of(site, SLUG, failure=VibixUnavailable(reason("vibix_api_timeout")))

    with pytest.raises(SiteError) as refused:
        await Run().read(site, web_of(release_with([entry(1)])))

    assert parse(refused.value.reason)["code"] == "player_failed" and len(asked) == 2


async def test_a_release_whose_pages_do_not_show_vibix_has_no_catalog_to_capture():
    site = RuchimeSite(PlayerRegistry.standard())
    asked = catalog_of(site, SLUG)

    draft = await Run().read(site, web_of(release_with([entry(1)]),
                                          episodes=lambda number: episode_page(390).replace("vibix-embed", "other")))

    assert asked == [] and {row.player for row in draft.rows} == {"vk", "allvideo", "kodik"}


# --- the choices of a source do not change the page ------------------------------------------------------------------------

def test_the_address_of_the_page_is_the_release_alone_whatever_the_source_chose():
    site = RuchimeSite()

    assert site.canonical_url(f"{RELEASE}/episode-5?vibix=2&off=vk#x", {"vibix_season": "2", "players_off": "vk"}) == RELEASE
    assert site.canonical_url(RELEASE) == RELEASE and site.canonical_url(site.canonical_url(RELEASE)) == RELEASE


def test_the_site_is_found_by_its_domain_and_only_for_the_pages_of_a_release():
    registry = SiteRegistry([RuchimeSite()])

    assert registry.find(f"{RELEASE}/episode-2") is not None and registry.find("https://ruchime.org/release/x") is not None
    assert registry.find("https://1.ruchime.org/catalog") is None and registry.find("https://vkvideo.ru/@chan") is None


def test_the_players_of_the_site_are_the_players_of_the_module_when_it_gives_them():
    own, given = PlayerRegistry.standard(), PlayerRegistry.standard()
    site = RuchimeSite(own)

    site.configure(players=given, vk_call=None)

    assert site.players is given and site.players.get("vibix") is given.get("vibix")


async def test_a_kodik_iframe_that_cannot_be_opened_refuses_the_table_with_the_reason_of_the_player():
    web = web_of(release_with([entry(1)]))
    web.route("kodikplayer.com/serial/", (500, ""), first=True)

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), web)

    assert parse(refused.value.reason) == {"code": "player_status", "params": {"player": "Kodik", "status": 500}}


async def test_the_catalog_of_a_player_is_read_once_however_many_pages_show_it():
    site = RuchimeSite(PlayerRegistry.standard())
    pages = catalog_of(site, "asura-bezumnyi-korol-2")
    web = web_of(release_with([entry(number) for number in range(1, 7)]))

    draft = await Run().read(site, web)

    # Six pages show both players; each catalog is read through the first of them and no other.
    assert web.count("kodikplayer.com/serial/") == 1 and set(pages) == {f"{RELEASE}/episode-1"}
    assert sum(1 for row in draft.rows if row.player == "vibix") == 23
