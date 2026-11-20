"""The player Alloha, asked and downloaded as its own player does it (modules/sources/players/alloha_session.py), on a
model of its host, its socket and its CDN (tests/alloha_model.py).

The document of the iframe is an ordinary page of the host: it lists the files the player has of the title (voice-over,
season, episode -> the number its files are asked by), and the host answers 404 for a voice-over or an episode it has
not. The files of a listed episode are asked by `POST /bnsi/movies/<id>` signed by `Borth` (the fingerprint of the profile
and the tail of the `viewporti` of that document). A download plays: the socket gives `edge_hash`, the CDN gives the
playlist and its segments only with the key of the script and the latest `edge_hash`.
"""
from __future__ import annotations


import pytest

from modules.sources.players import alloha
from modules.sources.players.alloha import AllohaPlayer, catalog_of, link_of
from modules.sources.players.alloha_session import AllohaRefused
from modules.sources.players.contract import Kind, PlayerRef, StreamUnavailable
from alloha_model import HOST, PROFILE, AllohaModel, document
from sources_support import STEP_LIMIT_S, within
from core.reasons import code_of

PAGE = "https://old.yummyani.me/catalog/item/lyubov-pod-gipnozom"
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

def test_the_list_of_the_files_is_read_from_the_document_and_a_document_without_it_says_nothing():
    listed = catalog_of(document({"158": {1: [1, 2], 2: [7]}, "200": {1: [1]}}))
    assert listed == {("158", "1", "1"): "91", ("158", "1", "2"): "92", ("158", "2", "7"): "97", ("200", "1", "1"): "91"}
    assert catalog_of("<html><title>Ошибка!</title></html>") is None
    assert catalog_of("<script>const fileList = JSON.parse('{\"type\":\"movie\"}');</script>") is None


# --- an ask: the document, then the files signed afresh ---------------------------------------------------------------------------

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
