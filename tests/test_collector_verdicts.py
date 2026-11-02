"""The verdict of a live run of the alignment (tools/collectors/verdicts.py): a pass needs positive evidence of each
voice-over, and what a bad run leaves behind never makes one."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docs" / "missions" / "voice-alignment" / "stage-10" / "tools" / "collectors"))
from verdicts import judge_alignment  # noqa: E402


@pytest.fixture
def library(tmp_path):
    reference = tmp_path / "Series s01e01 [AniStar] 2160p.mp4"
    reference.write_bytes(b"video")
    tracks = {}
    for label in ("BlackCat", "RedHead"):
        track = tmp_path / f"{reference.stem}.{label}.ru.flac"
        track.write_bytes(b"flac")
        tracks[label] = str(track)
    return reference, tracks


def dubs_of(*labels, state="done"):
    return [{"voice_label": label, "state": state} for label in labels]


def pairs_of(*names, ok=True):
    return [{"dub": f"7_s01e01_{name}.mp4", "ok": ok, "error": None} for name in names]


def test_a_run_with_a_result_for_every_voice_over_and_its_track_beside_the_reference_is_a_pass(library):
    reference, tracks = library
    assert judge_alignment(dubs_of("BlackCat", "RedHead"), tracks, reference, pairs_of("BlackCat", "RedHead"), []) == []


def test_no_result_of_the_service_is_no_pass_whatever_files_lie_about(library):
    """One voice-over done, one foreign track in the folder, no pair and no reference: what the old rule let pass."""
    reference, tracks = library
    foreign = reference.parent / "unrelated.flac"
    foreign.write_text("not media")
    problems = judge_alignment(dubs_of("BlackCat"), {}, None, [], [])
    assert any("референса" in each for each in problems) and any("нет результата сервиса" in each for each in problems)
    assert any("BlackCat" in each for each in problems)


def test_a_track_that_is_not_at_the_path_the_database_names_is_not_found_by_the_count_of_files(library):
    reference, tracks = library
    other = reference.parent / "stranger.flac"
    other.write_text("x")
    problems = judge_alignment(dubs_of("BlackCat"), {"BlackCat": str(reference.parent / "missing.flac")}, reference,
                               pairs_of("BlackCat"), [])
    assert problems and "BlackCat" in problems[0]


def test_a_track_beside_another_file_or_under_another_name_is_not_beside_the_reference(library, tmp_path):
    reference, tracks = library
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    moved = elsewhere / Path(tracks["BlackCat"]).name
    moved.write_bytes(b"flac")
    assert any("не рядом" in each for each in judge_alignment(
        dubs_of("BlackCat"), {"BlackCat": str(moved)}, reference, pairs_of("BlackCat"), []))
    renamed = reference.parent / "Another episode.BlackCat.flac"
    renamed.write_bytes(b"flac")
    assert any("не рядом" in each for each in judge_alignment(
        dubs_of("BlackCat"), {"BlackCat": str(renamed)}, reference, pairs_of("BlackCat"), []))


def test_fewer_results_than_voice_overs_a_refused_pair_a_voice_over_not_done_and_a_leftover_each_make_it_fail(library):
    reference, tracks = library
    assert judge_alignment(dubs_of("BlackCat", "RedHead"), tracks, reference, pairs_of("BlackCat"), [])
    assert judge_alignment(dubs_of("BlackCat"), tracks, reference, pairs_of("BlackCat", ok=False), [])
    assert judge_alignment(dubs_of("BlackCat", state="running"), tracks, reference, pairs_of("BlackCat"), [])
    assert judge_alignment(dubs_of("BlackCat"), tracks, reference, pairs_of("BlackCat"), ["downloads/x.mp4"])
    assert judge_alignment([], tracks, reference, pairs_of("BlackCat"), []) == ["нет ни одной озвучки"]
