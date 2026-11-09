"""The container of an episode (core/container.py) on real ffmpeg: what is built reads back as asked, nothing is encoded
again, the order and the default track are those given, a container is rebuilt from itself, and a build that is not
what was asked says why."""
import asyncio
import os
import shutil
import subprocess

import pytest

from core import container
from core.container import AudioIn

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not installed")


def make(path, *args) -> str:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args, str(path)], check=True)
    return str(path)


@pytest.fixture
def media(tmp_path):
    video = make(tmp_path / "ref.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=160x90:rate=10",
                 "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    dubs = {name: make(tmp_path / f"{name}.flac", "-f", "lavfi", "-i", f"sine=frequency={hz}:duration=3")
            for name, hz in (("ruchime", 500), ("anistar", 700))}
    other = make(tmp_path / "other.mp4", "-f", "lavfi", "-i", "testsrc=duration=5:size=160x90:rate=10",
                 "-f", "lavfi", "-i", "sine=frequency=900:duration=5", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    return tmp_path, video, dubs, other


def codecs(path) -> list[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)],
                         check=True, capture_output=True, text=True).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def test_a_container_holds_the_video_and_the_tracks_in_the_order_given_copied_as_they_are(media):
    tmp, video, dubs, _ = media
    out = str(tmp / "ep.mkv")
    tracks = [AudioIn("ruchime", dubs["ruchime"], 0, "1:a", language="rus"), AudioIn("ПВА ШОУ", video, 0, "1:a", language="chi"),
              AudioIn("anistar", dubs["anistar"], 0, "1:a")]
    assert asyncio.run(container.build(out, video, "1:a", tracks)) is None
    made = asyncio.run(container.contents(out))
    assert made.video == "1:a" and made.labels == ["ruchime", "ПВА ШОУ", "anistar"]
    assert [track.default for track in made.audio] == [True, False, False]
    assert [track.aligned_to for track in made.audio] == ["1:a"] * 3
    # Each track bears the language of its voice-over; one nobody gave a language to claims none (Matroska's «und»).
    assert [track.language for track in made.audio] == ["rus", "chi", "und"]
    assert codecs(out) == ["mpeg4", "flac", "aac", "flac"]  # nothing encoded again


def test_a_container_is_rebuilt_from_itself_in_a_new_order_and_without_a_track(media):
    tmp, video, dubs, _ = media
    first = str(tmp / "first.mkv")
    asyncio.run(container.build(first, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a"),
                                                       AudioIn("ruchime", dubs["ruchime"], 0, "1:a"),
                                                       AudioIn("anistar", dubs["anistar"], 0, "1:a")]))
    again = str(tmp / "again.mkv")
    was = asyncio.run(container.contents(first))
    order = {track.label: track.index for track in was.audio}
    tracks = [AudioIn(label, first, order[label], "1:a") for label in ("anistar", "ПВА ШОУ")]
    assert asyncio.run(container.build(again, first, "1:a", tracks)) is None
    assert asyncio.run(container.contents(again)).labels == ["anistar", "ПВА ШОУ"]
    assert codecs(again) == ["mpeg4", "flac", "aac"]


def test_the_tracks_of_a_container_go_into_a_new_video_of_the_same_identity(media):
    """Point 6 of ЭТАП_11: the tracks are taken out of the old container and put next to the new video, not fetched again."""
    tmp, video, dubs, _ = media
    old = str(tmp / "old.mkv")
    asyncio.run(container.build(old, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a"),
                                                     AudioIn("ruchime", dubs["ruchime"], 0, "1:a")]))
    better = make(tmp / "better.mp4", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=10",
                  "-f", "lavfi", "-i", "sine=frequency=300:duration=3", "-c:v", "mpeg4", "-c:a", "aac", "-shortest")
    new = str(tmp / "new.mkv")
    assert asyncio.run(container.build(new, better, "1:a", [AudioIn("ПВА ШОУ", better, 0, "1:a"),
                                                             AudioIn("ruchime", old, 1, "1:a")])) is None
    found = asyncio.run(container.contents(new))
    assert found.labels == ["ПВА ШОУ", "ruchime"] and found.height == 180  # the height of the film the file holds


def test_the_title_of_a_track_is_the_users_name_and_its_group_stays_the_tag(media):
    tmp, video, dubs, _ = media
    out = str(tmp / "titled.mkv")
    assert asyncio.run(container.build(out, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a", own=True),
                                                         AudioIn("ruchime", dubs["ruchime"], 0, "1:a", title="ПМ_ruchime")])) is None
    found = asyncio.run(container.contents(out))
    assert [(track.label, track.title) for track in found.audio] == [("ПВА ШОУ", "ПВА ШОУ"), ("ruchime", "ПМ_ruchime")]
    assert container.problem_of(found, "1:a", [("ПВА ШОУ", "ПВА ШОУ", "", True, "und"), ("ruchime", "ruchime", "", False, "und")],
                                None) == "container_tracks_wrong"  # a file with another title is not the one asked for


@pytest.mark.parametrize("case, expected", [
    ("unreadable", "container_unreadable"), ("video", "container_video_wrong"), ("tracks", "container_tracks_wrong"),
    ("language", "container_tracks_wrong"), ("length", "container_length_wrong"), ("good", None)])
def test_a_build_that_is_not_what_was_asked_says_why(media, case, expected):
    tmp, video, dubs, other = media
    out = str(tmp / "ep.mkv")
    asyncio.run(container.build(out, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a")]))
    made = None if case == "unreadable" else asyncio.run(container.contents(out))
    identity = "2:b" if case == "video" else "1:a"
    signature = ([("ruchime", "ruchime", "", False, "und")] if case == "tracks" else [("ПВА ШОУ", "ПВА ШОУ", "", False, "und")]
                 if case != "language" else [("ПВА ШОУ", "ПВА ШОУ", "", False, "rus")])
    duration = 5.0 if case == "length" else 3.0
    assert container.problem_of(made, identity, signature, duration) == expected


def test_a_file_not_built_here_reads_with_no_identity_and_its_tracks_by_title(media):
    _, video, _, _ = media
    found = asyncio.run(container.contents(video))
    assert found.has_video and found.video == "" and len(found.audio) == 1 and found.duration > 2.5
    assert asyncio.run(container.contents(str(video) + ".missing")) is None


def test_a_container_carries_its_provenance_and_gives_it_back(media):
    """Stage 12: the table of what the container holds travels inside it, through a rebuild too."""
    tmp, video, dubs, _ = media
    table = {"video": {"source": "сайт", "row": "1"}, "tracks": [{"voice": "ruchime", "aligned_to": "1:a"}]}
    first = str(tmp / "first.mkv")
    assert asyncio.run(container.build(first, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a", own=True),
                                                            AudioIn("ruchime", dubs["ruchime"], 0, "1:a")],
                                       provenance=table)) is None
    assert asyncio.run(container.provenance_of(first)) == table
    again = str(tmp / "again.mkv")
    newer = {**table, "rev": 2}
    assert asyncio.run(container.build(again, first, "1:a", [AudioIn("ПВА ШОУ", first, 0, "1:a", own=True)],
                                       provenance=newer)) is None
    assert asyncio.run(container.provenance_of(again)) == newer
    assert asyncio.run(container.provenance_of(video)) is None  # a file not built here carries none
    assert sorted(os.listdir(tmp)) == sorted(set(os.listdir(tmp)) - {"first.mkv.provenance.json"})


def test_one_recording_under_the_names_of_two_voice_overs_is_not_built_and_silence_is_no_recording(media):
    """A player once gave the file of one voice-over under the names of eight others (Kodik's borrowed episodes): the
    container never holds one recording twice, whatever files it comes in. Silence says nothing of whose it is."""
    tmp, video, dubs, _ = media
    copy = str(tmp / "copy of ruchime.flac")
    shutil.copyfile(dubs["ruchime"], copy)
    out = str(tmp / "ep.mkv")
    tracks = [AudioIn("ПВА ШОУ", video, 0, "1:a"), AudioIn("ruchime", dubs["ruchime"], 0, "1:a"),
              AudioIn("anistar", dubs["anistar"], 0, "1:a"), AudioIn("Flarrow Films", copy, 0, "1:a")]
    assert asyncio.run(container.build(out, video, "1:a", tracks)) == "container_same_recording: ruchime = Flarrow Films"
    assert not os.path.exists(out)

    quiet = [make(tmp / f"quiet{n}.flac", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", "3") for n in (1, 2)]
    assert asyncio.run(container.build(out, video, "1:a", [AudioIn("ПВА ШОУ", video, 0, "1:a")] + [
        AudioIn(f"тихая {n}", path, 0, "1:a") for n, path in enumerate(quiet)])) is None
