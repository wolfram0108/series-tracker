"""The player Alloha, asked and downloaded as its own player does it (modules/sources/players/alloha_session.py), on a
model of its host, its socket and its CDN (tests/alloha_model.py).

The document of the iframe is an ordinary page of the host: it lists the files the player has of the title (voice-over,
season, episode -> the number its files are asked by), and the host answers 404 for a voice-over or an episode it has
not. The files of a listed episode are asked by `POST /bnsi/movies/<id>` signed by `Borth` (the fingerprint of the profile
and the tail of the `viewporti` of that document). A download plays: the socket gives `edge_hash`, the CDN gives the
playlist and its segments only with the key of the script and the latest `edge_hash`.
"""
from __future__ import annotations

import os

import pytest

from modules.sources.players import alloha
from modules.sources.players.alloha import AllohaPlayer, catalog_of, link_of, parse_link, read_answer
from modules.sources.players.alloha_session import AllohaRefused, borth_tail, profile_of, tracks_of
from modules.sources.players.contract import Kind, PlayerRef, StreamUnavailable
from alloha_model import HOST, KEY, PROFILE, AllohaModel, document
from sources_support import STEP_LIMIT_S, within
from core.reasons import code_of

PAGE = "https://old.yummyani.me/catalog/item/lyubov-pod-gipnozom"
IFRAME = ("//alloha.yani.tv/?token_movie=8bf4&translation=158&season=1&episode=5&token=8b55"
          "&hidden=translation,season,episode")


def answer(*tracks: dict) -> dict:
    return {"hlsSource": list(tracks), "tracks": None, "type": "video", "pnr": "wss://alloha.test/ws/"}


def track(label: str, heights=(360, 720, 1080), default: bool | None = None) -> dict:
    found = {"label": label, "audioId": label, "quality": {
        str(height): f"https://cdn{label}.vkvideo.test/{height}.m3u8 or https://backup.test/{height}.m3u8" for height in heights}}
    return {**found, "default": default} if default is not None else found


def episode_ref(episode: int, season: int = 1, translation: str = "158") -> PlayerRef:
    iframe = f"{HOST}/?token_movie=8bf4&translation={translation}&season={season}&episode={episode}&token=8b55"
    return PlayerRef("alloha", link_of(PAGE, iframe))


def a_player(model: AllohaModel | None, profile=PROFILE) -> AllohaPlayer:
    return AllohaPlayer(profile, client=model.client if model is not None else None)


@pytest.fixture(autouse=True)
def unpaced(monkeypatch):
    """The pace of the asks is the host's limit; a model of the host has none."""
    monkeypatch.setattr(alloha, "ASK_INTERVAL_S", 0.0)


# --- the link, the iframe, the answer -------------------------------------------------------------------------------------------

def test_a_page_embeds_alloha_by_an_iframe_with_the_token_of_the_title_and_the_link_keeps_page_and_iframe():
    player = AllohaPlayer()
    frame = f'<iframe src="{IFRAME.replace("&", "&amp;")}" allowfullscreen></iframe>'
    ref = player.find(frame, PAGE)

    assert parse_link(ref.url) == (PAGE, "https:" + IFRAME)
    assert player.find('<iframe src="https://alloha.test/embed/1"></iframe>', PAGE) is None
    assert AllohaPlayer.owns(ref.url) and not AllohaPlayer.owns(PAGE) and not AllohaPlayer.owns(PAGE + "#kodik=1/2/3")
    # The video is the title, the voice-over, the season and the episode: the same on any page that embeds it.
    assert AllohaPlayer.video_of(ref.url) == "8bf4/158/1/5" == AllohaPlayer.video_of(link_of("https://other.test/a", IFRAME))


def test_the_answer_of_the_player_is_read_into_the_files_of_the_voice_it_marks():
    marked = read_answer(answer(track("1", (360, 720)), track("2", (480, 1080), default=True)))
    first = read_answer(answer(track("1", (360, 720)), track("2", (480, 1080))))

    assert (marked.kind, marked.resolutions, marked.title) == (Kind.PLAYABLE, (480, 1080), "2")
    # Of "<one> or <another>" the first address is the player's own choice.
    assert dict(marked.files)[1080] == "https://cdn2.vkvideo.test/1080.m3u8" and first.resolutions == (360, 720)
    assert read_answer({"hlsSource": []}).kind is Kind.NOT_PLAYER and read_answer([]).kind is Kind.NOT_PLAYER
    assert read_answer(answer({"label": "1", "quality": {}})).kind is Kind.NO_FILE
    # The streams of a download: the voice-over the player marks first, every mirror of each height kept in order.
    streams = tracks_of(answer(track("1", (360,)), track("2", (480,), default=True)))
    assert [(each.label, each.height, len(each.urls)) for each in streams] == [("2", 480, 2), ("1", 360, 2)]


def test_the_list_of_the_files_is_read_from_the_document_and_a_document_without_it_says_nothing():
    listed = catalog_of(document({"158": {1: [1, 2], 2: [7]}, "200": {1: [1]}}))
    assert listed == {("158", "1", "1"): "91", ("158", "1", "2"): "92", ("158", "2", "7"): "97", ("200", "1", "1"): "91"}
    assert catalog_of("<html><title>Ошибка!</title></html>") is None
    assert catalog_of("<script>const fileList = JSON.parse('{\"type\":\"movie\"}');</script>") is None


def test_the_tail_of_borth_is_the_players_own_on_the_vectors_of_the_prototype():
    vectors = {"a": "a", "abcdef": "cdbefa", "0123456789": "2516943780", "abcdefghijklmnop": "lnhgpiacojefmkbd", "": ""}
    assert {seed: borth_tail(seed) for seed in vectors} == vectors


def test_the_profile_is_both_values_of_the_environment_or_none_and_a_wrong_one_stops_the_start():
    assert profile_of({}) is None
    found = profile_of({"ST_ALLOHA_FINGERPRINT": PROFILE.fingerprint, "ST_ALLOHA_USER_AGENT": PROFILE.user_agent})
    assert found == PROFILE
    for wrong in ({"ST_ALLOHA_FINGERPRINT": "12ab"}, {"ST_ALLOHA_FINGERPRINT": PROFILE.fingerprint,
                                                      "ST_ALLOHA_USER_AGENT": "Mozilla/5.0 Firefox/120"}):
        with pytest.raises(ValueError):
            profile_of(wrong)


# --- an ask: the document, then the files signed afresh ---------------------------------------------------------------------------

async def test_an_ask_reads_the_document_as_the_site_and_the_files_signed_by_the_profile_and_the_seed_of_that_document():
    model = AllohaModel(heights=(360, 720, 1080))
    reading = a_player(model).read(await a_player(model).fetch(episode_ref(1), None, PAGE))

    assert (reading.kind, reading.resolutions, reading.title) == (Kind.PLAYABLE, (360, 720, 1080), "AniStar")
    (signed,) = model.files_asked
    assert signed["Borth"] == f"{PROFILE.fingerprint}|{borth_tail('0123456789abcdefghij')}"
    assert signed["User-Agent"] == PROFILE.user_agent and signed["Origin"] == HOST
    # Reading plays nothing: no script, no socket, no report, no CDN.
    assert [url for _m, url in model.asked if "/build/" in url or "/events" in url] == [] and model.sockets == []


async def test_a_voice_over_or_an_episode_the_player_has_not_is_told_by_the_document_and_its_files_are_not_asked():
    model = AllohaModel(voices={"158": {1: [5]}})
    player = a_player(model)
    other_voice = player.read(await player.fetch(episode_ref(5, translation="200"), None, PAGE))
    listed_not = player.read(await player.fetch(episode_ref(6), None, PAGE))

    # The host answers 404 for the iframe of a voice-over it has not; the list of the player has no episode 6.
    assert (other_voice.kind, other_voice.status) == (Kind.NOT_FOUND, 404)
    assert model.files_asked == []
    assert listed_not.kind is Kind.NOT_FOUND or (listed_not.kind, listed_not.status) == (Kind.NOT_FOUND, 404)


async def test_a_refusal_of_the_files_is_unsure_and_names_its_status_and_no_profile_asks_nothing():
    model = AllohaModel()
    model.refuse_files = 404  # the host refuses past its rate with the 404 of a video it has not
    refused = a_player(model).read(await a_player(model).fetch(episode_ref(1), None, PAGE))
    unset = a_player(model, profile=None)
    bare = unset.read(await unset.fetch(episode_ref(1), None, PAGE))

    assert (refused.kind, refused.status) == (Kind.HTTP_ERROR, 404)
    assert bare.kind is Kind.NO_ANSWER and code_of(bare.message) == "alloha_no_profile"
    with pytest.raises(StreamUnavailable) as raised:
        await unset.stream(episode_ref(1).url, None, 720, None)
    assert raised.value.transient is False


# --- a download: the player plays and takes the segments itself ----------------------------------------------------------------

async def transfer(model: AllohaModel, target: str, cap: int | None = 720):
    figures = []

    async def told(data: dict) -> None:
        figures.append(data)

    player = a_player(model)
    assert await player.stream(episode_ref(1).url, None, cap, None) == {"stream_url": episode_ref(1).url,
                                                                        "player_fetch": True}
    done = await within(player.transfer(episode_ref(1).url, target, cap, 16, told), STEP_LIMIT_S, "the transfer")
    return done, figures


async def test_a_download_plays_the_episode_and_takes_every_segment_in_order_at_the_best_height_within_the_cap(tmp_path):
    model = AllohaModel(heights=(360, 720, 1080), segments=12)
    target = str(tmp_path / "e1.download.ts")
    done, figures = await transfer(model, target)

    with open(target, "rb") as file:
        assert file.read() == b"INIT" + b"".join(model.body(720, index) for index in range(12))
    assert done == {"seconds": 48.0, "longest": 4.0, "height": 720}
    (socket,) = model.sockets
    assert socket.said[:2] == ["playback_start", "resumed"] and model.closed == 1
    # Every request of the CDN carried the key of the script and the edge of the socket; the reports went as a player's.
    assert {(each["Authorizations"], each["Accepts-Controls"]) for each in model.media} == {("Bearer " + KEY, "E1")}
    assert model.reports[:3] == ["/events", "/stat", "/stat"]
    assert figures[0] == {"phase": "link", "source_seconds": 48.0} and figures[-1]["progress"] == 100
    # Each connection of the download is a client of its own: one client speaks HTTP/2 over one connection.
    assert model.clients == 1 + 2


async def test_a_new_edge_given_in_the_middle_is_carried_by_every_request_after_it(tmp_path):
    model = AllohaModel(segments=40)
    model.after_segment[3] = lambda: model.sockets[0].give("E2")
    done, _figures = await transfer(model, str(tmp_path / "e1.ts"))

    edges = [each["Accepts-Controls"] for each in model.media]
    assert done["seconds"] == 160.0 and edges[0] == "E1" and edges[-1] == "E2" and "E2" in edges


@pytest.mark.parametrize("trouble, final", [("gone", True), ("blocked", False), ("socket", False)])
async def test_a_refusal_ends_the_download_at_once_and_says_whether_it_is_the_sources_last_word(tmp_path, trouble, final):
    model = AllohaModel(segments=40)
    if trouble == "gone":
        model.gone.add(4)
    elif trouble == "blocked":
        model.after_segment[2] = lambda: setattr(model, "edges", [])  # the CDN closed the session
    else:
        model.after_segment[2] = lambda: model.sockets[0].drop()
    with pytest.raises(AllohaRefused) as raised:
        await transfer(model, str(tmp_path / "e1.ts"))

    assert raised.value.final is final and model.closed == 1
    if trouble == "blocked":
        assert "session_blocked" in str(raised.value)


async def test_a_cap_below_every_height_of_the_episode_is_no_file(tmp_path):
    model = AllohaModel(heights=(720, 1080))
    with pytest.raises(AllohaRefused) as raised:
        await transfer(model, str(tmp_path / "e1.ts"), cap=480)
    assert raised.value.final is True and not os.path.exists(tmp_path / "e1.ts")


def test_the_link_names_the_player_and_another_link_is_not_its_own():
    assert PlayerRef("alloha", link_of(PAGE, IFRAME)).url.startswith(PAGE + "#alloha=")
    assert parse_link(PAGE) is None and AllohaPlayer.video_of(PAGE) == ""
