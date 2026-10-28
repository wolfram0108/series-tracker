"""The contract of an output: every form of it keeps the same promises."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from modules.voices.outputs import SidecarOutput  # noqa: E402
from test_voices import FolderOutput  # noqa: E402


def make(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return str(path)


@pytest.fixture(params=[SidecarOutput, FolderOutput], ids=["sidecar", "folder"])
def output(request):
    return request.param()


def test_a_track_is_delivered_followed_and_removed(output, tmp_path):
    video = make(tmp_path / "lib" / "Series s01e01 720p.mp4")
    track = make(tmp_path / "work" / "a.flac", b"audio")
    where = output.destination(video, "BlackCat", track, set())
    output.deliver(track, where)
    assert output.exists(where) and not os.path.exists(track)
    renamed = make(tmp_path / "lib" / "Series s01e01 2160p.mp4")
    after = output.destination(renamed, "BlackCat", where, set())
    output.follow(where, after)
    assert output.exists(after) and not output.exists(where)
    output.remove(after)
    assert not output.exists(after)


def test_two_tracks_of_a_video_never_share_a_place(output, tmp_path):
    video = make(tmp_path / "lib" / "Series s01e01.mp4")
    track = make(tmp_path / "work" / "a.flac")
    first = output.destination(video, "BlackCat", track, set())
    assert output.destination(video, "BlackCat", track, {first}) != first


def test_an_interrupted_delivery_is_finished_or_found_lost(output, tmp_path):
    video = make(tmp_path / "lib" / "Series s01e01.mp4")
    track = make(tmp_path / "work" / "a.flac", b"audio")
    where = output.destination(video, "BlackCat", track, set())
    assert output.settle(track, where) is True and output.exists(where)
    assert output.settle(track, where) is True, "finishing twice is the same as once"
    output.remove(where)
    assert output.settle(track, where) is False, "neither the source nor the artifact: lost"
