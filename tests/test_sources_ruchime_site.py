"""The site 1.ruchime.org read into the table of the page (M-6, M-7), on synthetic answers in the shape of the real ones
(`site_samples`).

The release page, its list of 390 episodes, the pages of the first and the last episode and their players are the samples;
the episodes between are the page of the last one with a video of their own, so that the table has the whole list. The
catalog of Vibix is the decoded answer of its API, the manifests its CDN gives are valid ones.
"""
import json
import re

import httpx
import pytest

from core.contracts import RowState
from core.reasons import parse
from modules.sources.players.registry import PlayerRegistry
from modules.sources.players.vibix_protocol import encode_envelope, parse_playlist
from modules.sources.sites import (ReadContext, SiteError)
from modules.sources.sites.ruchime import RuchimeSite
from sources_support import READ_LIMIT_S, Web, json_of, text_of, within

SLUG = "praktikuia-ci-sto-tysiac-let"
RELEASE = f"https://1.ruchime.org/release/{SLUG}"
KEY = b"K" * 32
MANIFEST = b"#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6.000,\nseg.ts\n#EXT-X-ENDLIST\n"
FILLER_VK = '<script>{"mp4_720":"a","mp4_1080":"b"}</script>'
FILLER_ALLVIDEO = '<script>file:"[360p]https://cdn.test/360.mp4/,[1080p]https://cdn.test/1080.mp4/"</script>'
DENIED = '{"payload":["8",["\\"Ошибка доступа (1)\\"","false","\\"\\""]]}'
# The videos of the samples: the embed page each player gives for the first and for the last episode.
SAMPLE_VK = {"456246539": "ruchime__episode-1__vk_embed.html", "456256559": "ruchime__episode-390__vk_embed.html"}
SAMPLE_ALLVIDEO = {"688273": "ruchime__episode-1__allvideo_embed.html",
                "1081867": "ruchime__episode-390__allvideo_embed.html"}


def episode_page(number: int) -> str:
    if number == 1:
        return text_of("ruchime__episode-1.html")
    last = text_of("ruchime__episode-390.html")
    if number == 390:
        return last
    return (last.replace("456256559", f"4{number:08d}").replace("844d706ebb47dffa", f"{number:016x}")
            .replace("1081867", f"{9_000_000 + number}"))


def release_with(entries: list[dict]) -> str:
    """The sample of the release page with another list of episodes in place of its own."""
    return re.sub(r"const ALL_EPS\s*=\s*\[.*?\];", lambda _: "const ALL_EPS = " + json.dumps(entries) + ";",
                  text_of("ruchime__release.html"), count=1, flags=re.DOTALL)


def entry(number: int, **fields) -> dict:
    return {"id": 1000 + number, "number": number, "compilation_from": None, "compilation_to": None,
            "title": f"Серия {number}", "is_vip": False, "is_trailer": False, "date": "09 May 2026", **fields}


def embed_by(known: dict, filler: str):
    def answer(request: httpx.Request):
        url = str(request.url)
        return next((text_of(name) for marker, name in known.items() if marker in url), filler)
    return answer


def kodik_iframe(request: httpx.Request):
    """The iframe of Animy, the voice-over the pages of the release name: the sample of the iframe of dunhuatv."""
    return text_of("dunhuatv__kodik_voice0.html") if "/serial/50228/" in str(request.url) else (404, "")


def web_of(release: str | None = None, *, episodes=episode_page) -> Web:
    web = Web()
    web.route(f"/release/{SLUG}/episode-", lambda request: episodes(int(str(request.url).rsplit("-", 1)[1])))
    web.route(f"/release/{SLUG}", release if release is not None else text_of("ruchime__release.html"))
    web.route("video_ext.php", embed_by(SAMPLE_VK, FILLER_VK))
    web.route("/embed/", embed_by(SAMPLE_ALLVIDEO, FILLER_ALLVIDEO))
    web.route("al_video.php", DENIED, method="POST")
    web.route("kodikplayer.com/serial/", kodik_iframe)
    web.route("kodikplayer.com/ftor", text_of("dunhuatv__kodik_ftor.json"), method="POST")
    web.route("kinescopecdn.net", lambda request: (200, encode_envelope(MANIFEST, KEY).decode()))
    return web


def catalog_of(site: RuchimeSite, release: str, failure: Exception | None = None) -> list[str]:
    """Put the decoded catalog of a sample where the browser capture of Vibix would put it; the pages asked are returned."""
    asked: list[str] = []

    async def playlist(page_url: str):
        asked.append(page_url)
        if failure is not None:
            raise failure
        return parse_playlist(json_of(f"vibix__{release}__api_decoded.json")), KEY

    site.players.get("vibix").playlist = playlist
    return asked


def a_site(release: str = "asura-bezumnyi-korol-2") -> RuchimeSite:
    site = RuchimeSite(PlayerRegistry.standard())
    catalog_of(site, release)
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


UNSTABLE = re.compile(r"expires=|sign=|token|\.mp4|\.m3u8|mp4_|kinescopecdn|api_hash")


# --- the states of a row (M-6) and the catalog of two seasons ---------------------------------------------------------------

async def test_a_series_is_asked_of_vk_in_one_batch_when_the_player_page_issues_a_token():
    web = web_of(release_with([entry(390), entry(2)]))
    web.route("video_ext.php", lambda request: text_of("ruchime__vk_embed_agent0.html"), first=True)
    web.route("/method/video.get", lambda request: text_of("ruchime__vk_video_get_agent0.json"),
              method="POST")

    draft = await Run().read(a_site(), web)
    first_form = web.forms("/method/video.get")[0]

    assert first_form["videos"].split(",")[0] == "-170515668_456256559_844d706ebb47dffa"
    assert first_form["videos"].count(",") == 1 and first_form["ref_domain"] == "1.ruchime.org"
    # The batch settled the video it returned: the player page of it was asked only for the token.
    assert web.count("id=456256559&hash") == 1
    assert of(draft, "vk", 390).state is RowState.ABSENT


# --- a read that cannot give the page gives none of it; a part it cannot get is named ---------------------------------------

async def test_a_host_that_does_not_answer_is_a_refusal_with_a_reason():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(SiteError) as refused:
        await Run().read(a_site(), Web().route(f"/release/{SLUG}", down))

    assert parse(refused.value.reason)["code"] == "site_unreachable"


async def test_an_unread_page_of_an_episode_is_an_unread_part_and_the_other_pages_are_read():
    web = web_of(release_with([entry(1), entry(2), entry(3)]))
    web.route("/episode-2", (500, ""), first=True)

    draft = await Run().read(a_site(), web)

    assert [(part.part, part.names, parse(part.reason)["code"]) for part in draft.unread] == [
        (f"{RELEASE}/episode-2", ["Серия 2"], "page_status")]
    page_rows = [row for row in draft.rows if row.player in ("vk", "allvideo")]
    assert sorted({row.site_fields["number"] for row in page_rows}) == [1, 3]
    assert {row.part for row in page_rows} == {f"{RELEASE}/episode-1", f"{RELEASE}/episode-3"}


# --- the choices of a source do not change the page ------------------------------------------------------------------------
