"""The verdict of a live run of the alignment (tools/collectors/verdicts.py): a pass needs positive evidence of each
voice-over, one result and one track for each, and what a bad run leaves behind never makes one."""
import itertools
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "docs" / "missions" / "voice-alignment" / "stage-10" / "tools" / "collectors"))
from verdicts import judge_alignment, slot_key  # noqa: E402


@pytest.fixture
def library(tmp_path):
    reference = tmp_path / "Series s01e01 [AniStar] 2160p.mp4"
    reference.write_bytes(b"video")
    tracks = {}
    for label in ("BlackCat", "RedHead"):
        track = tmp_path / f"{reference.stem}.{label}.ru.flac"
        track.write_bytes(b"flac")
        tracks[slot_key({"season": 1, "episode": 1, "voice_key": label})] = str(track)
    return reference, tracks


def dubs_of(*labels, state="done", part=None):
    return [{"season": 1, "episode": 1, "part": part, "voice_key": label, "voice_label": label, "state": state}
            for label in labels]


def inputs_of(*labels, part=None):
    tag = f"p{part}" if part else ""
    return {slot_key({"season": 1, "episode": 1, "part": part, "voice_key": label}): f"7_s01e01{tag}_{label}.mp4"
            for label in labels}


def pairs_of(*names, ok=True):
    return [{"dub": f"7_s01e01_{name}.mp4", "ok": ok, "error": None} for name in names]


def judged(dubs, tracks, inputs, reference, pairs, temporary=()):
    return judge_alignment(dubs, tracks, inputs, reference, pairs, list(temporary))


def test_a_run_with_a_result_for_every_voice_over_and_its_track_beside_the_reference_is_a_pass(library):
    reference, tracks = library
    assert judged(dubs_of("BlackCat", "RedHead"), tracks, inputs_of("BlackCat", "RedHead"), reference,
                  pairs_of("BlackCat", "RedHead")) == []


def test_no_result_of_the_service_is_no_pass_whatever_files_lie_about(library):
    """One voice-over done, one foreign track in the folder, no pair and no reference: what the old rule let pass."""
    reference, tracks = library
    foreign = reference.parent / "unrelated.flac"
    foreign.write_text("not media")
    problems = judged(dubs_of("BlackCat"), {}, inputs_of("BlackCat"), None, [])
    assert any("референса" in each for each in problems) and any("нет результата сервиса" in each for each in problems)
    assert any("BlackCat" in each for each in problems)


def test_a_track_that_is_not_at_the_path_the_database_names_is_not_found_by_the_count_of_files(library):
    reference, tracks = library
    other = reference.parent / "stranger.flac"
    other.write_text("x")
    missing = {slot_key(dubs_of("BlackCat")[0]): str(reference.parent / "missing.flac")}
    problems = judged(dubs_of("BlackCat"), missing, inputs_of("BlackCat"), reference, pairs_of("BlackCat"))
    assert problems and "BlackCat" in problems[0]


def test_a_track_beside_another_file_or_under_another_name_is_not_beside_the_reference(library, tmp_path):
    reference, tracks = library
    key = slot_key(dubs_of("BlackCat")[0])
    elsewhere = tmp_path / "other"
    elsewhere.mkdir()
    moved = elsewhere / Path(tracks[key]).name
    moved.write_bytes(b"flac")
    assert any("не рядом" in each for each in judged(dubs_of("BlackCat"), {key: str(moved)}, inputs_of("BlackCat"), reference,
                                                      pairs_of("BlackCat")))
    renamed = reference.parent / "Another episode.BlackCat.flac"
    renamed.write_bytes(b"flac")
    assert any("не рядом" in each for each in judged(dubs_of("BlackCat"), {key: str(renamed)}, inputs_of("BlackCat"), reference,
                                                      pairs_of("BlackCat")))


def test_fewer_results_than_voice_overs_a_refused_pair_a_voice_over_not_done_and_a_leftover_each_make_it_fail(library):
    reference, tracks = library
    both, one = inputs_of("BlackCat", "RedHead"), inputs_of("BlackCat")
    assert judged(dubs_of("BlackCat", "RedHead"), tracks, both, reference, pairs_of("BlackCat"))
    assert judged(dubs_of("BlackCat"), tracks, one, reference, pairs_of("BlackCat", ok=False))
    assert judged(dubs_of("BlackCat", state="running"), tracks, one, reference, pairs_of("BlackCat"))
    assert judged(dubs_of("BlackCat"), tracks, one, reference, pairs_of("BlackCat"), ["downloads/x.mp4"])
    assert judged([], tracks, one, reference, pairs_of("BlackCat")) == ["нет ни одной озвучки"]


def test_the_result_of_the_service_is_the_one_for_the_file_the_voice_over_was_sent_as_not_one_that_ends_in_its_name(library):
    reference, tracks = library
    problems = judged(dubs_of("BlackCat"), tracks, inputs_of("BlackCat"), reference, [
        {"dub": "7_s01e01_NotBlackCat.mp4", "ok": True, "error": None}, {"dub": "9_s01e01_BlackCat.mp4", "ok": True, "error": None}])
    assert any("нет результата сервиса" in each for each in problems)


def test_two_parts_of_a_voice_over_with_one_result_and_one_track_are_not_a_pass(library):
    """Two parts under one label, one result of the service and one track: what a pass by the label let through."""
    reference, tracks = library
    both = dubs_of("Dub", part=1) + dubs_of("Dub", part=2)
    inputs = {**inputs_of("Dub", part=1), **inputs_of("Dub", part=2)}
    only_second = {slot_key(both[1]): str(next(iter(tracks.values())))}
    problems = judged(both, only_second, inputs, reference, [{"dub": "7_s01e01p2_Dub.mp4", "ok": True, "error": None}])
    assert any("нет результата сервиса" in each for each in problems) and any("дорожки" in each for each in problems)


def test_a_run_with_parts_is_refused_by_the_instrument_and_not_passed_even_when_each_part_has_its_evidence(library):
    """The instrument looks at one episode as a whole, with one reference; a run with parts is not judged by it."""
    reference, tracks = library
    parts = dubs_of("Dub", part=1) + dubs_of("Dub", part=2)
    inputs = {**inputs_of("Dub", part=1), **inputs_of("Dub", part=2)}
    outputs = {slot_key(parts[0]): str(next(iter(tracks.values()))), slot_key(parts[1]): str(next(iter(tracks.values())))}
    pairs = [{"dub": "7_s01e01p1_Dub.mp4", "ok": True, "error": None}, {"dub": "7_s01e01p2_Dub.mp4", "ok": True, "error": None}]
    assert any("без частей" in each for each in judged(parts, outputs, inputs, reference, pairs))


def test_one_result_is_not_counted_for_two_voice_overs_sent_as_one_file(library):
    reference, tracks = library
    twice = dubs_of("BlackCat", "RedHead")
    inputs = {slot_key(twice[0]): "7_s01e01_BlackCat.mp4", slot_key(twice[1]): "7_s01e01_BlackCat.mp4"}
    problems = judged(twice, tracks, inputs, reference, pairs_of("BlackCat"))
    assert any("уже засчитан" in each for each in problems)


def test_the_evidence_is_read_from_the_database_by_the_key_of_each_voice_over_and_a_part_is_not_its_label(tmp_path):
    import sqlite3
    from live_sync import evidence_of
    (tmp_path / "work").mkdir()
    with sqlite3.connect(tmp_path / "work" / "app.db") as conn:
        conn.execute("CREATE TABLE voice_dubs (series_id INT, season INT, episode INT, part INT, voice_key TEXT, status TEXT, "
                     "output_path TEXT)")
        conn.executemany("INSERT INTO voice_dubs VALUES (?,?,?,?,?,?,?)", [
            (7, 1, 1, 0, "BlackCat", "placed", "/lib/a.flac"), (7, 1, 1, 0, "RedHead", "rejected", None),
            (7, 1, 1, 2, "BlackCat", "placed", "/lib/b.flac"), (7, 1, 2, 0, "BlackCat", "placed", "/lib/other.flac")])
    tracks, inputs = evidence_of(str(tmp_path / "work"), 1)
    assert tracks == {"1/1/0/BlackCat": "/lib/a.flac", "1/1/2/BlackCat": "/lib/b.flac"}
    assert inputs == {"1/1/0/BlackCat": "7_s01e01_BlackCat.mp4", "1/1/0/RedHead": "7_s01e01_RedHead.mp4",
                      "1/1/2/BlackCat": "7_s01e01p2_BlackCat.mp4"}


def test_one_track_is_not_counted_for_two_voice_overs(library):
    reference, tracks = library
    both = dubs_of("BlackCat", "RedHead")
    same = {slot_key(each): next(iter(tracks.values())) for each in both}
    problems = judged(both, same, inputs_of("BlackCat", "RedHead"), reference, pairs_of("BlackCat", "RedHead"))
    assert any("уже засчитана" in each for each in problems)


def test_a_track_of_a_compilation_that_begins_like_the_reference_does_not_lie_beside_it(library):
    reference, tracks = library
    compilation = reference.parent / f"{reference.stem[:-len(' [AniStar] 2160p')]}-e02 [AniStar] 2160p.BlackCat.ru.flac"
    longer = reference.parent / f"{reference.stem}-e02.BlackCat.ru.flac"
    longer.write_bytes(b"flac")
    problems = judged(dubs_of("BlackCat"), {slot_key(dubs_of("BlackCat")[0]): str(longer)}, inputs_of("BlackCat"), reference,
                      pairs_of("BlackCat"))
    assert any("не рядом" in each for each in problems) and not compilation.exists()


def test_the_evidence_is_judged_for_every_small_run_and_only_a_run_with_all_of_it_passes(library):
    """Up to three voice-overs; each may lack its result, its track, or share its track with another: only the whole passes."""
    reference, tracks = library
    base = next(iter(tracks.values()))
    extra = reference.parent / f"{reference.stem}.Extra.ru.flac"
    extra.write_bytes(b"flac")
    for size in (1, 2, 3):
        labels = [f"V{number}" for number in range(size)]
        dubs = dubs_of(*labels)
        for results in itertools.product((True, False), repeat=size):
            for placed in itertools.product(("own", "none", "shared"), repeat=size):
                outputs = {}
                for each, how in zip(dubs, placed):
                    if how == "own":
                        own = reference.parent / f"{reference.stem}.{each['voice_key']}.ru.flac"
                        own.write_bytes(b"flac")
                        outputs[slot_key(each)] = str(own)
                    elif how == "shared":
                        outputs[slot_key(each)] = base
                pairs = pairs_of(*[label for label, there in zip(labels, results) if there])
                problems = judged(dubs, outputs, inputs_of(*labels), reference, pairs)
                paths = list(outputs.values())
                sharing = len(paths) != len(set(paths))
                whole = all(results) and "none" not in placed and not sharing
                assert (problems == []) == whole, (size, results, placed, problems)
                if sharing and all(results) and "none" not in placed:
                    assert any("уже засчитана" in each for each in problems), (size, results, placed, problems)
