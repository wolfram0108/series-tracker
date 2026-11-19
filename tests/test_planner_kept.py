"""The keep of the aligned sounds (modules/planner/kept.py): a hard link of the file a job wrote, found again by the episode, the
timeline it was aligned to and the row of its voice-over; nothing is copied, and a set that does not fit is not used."""
import os

import pytest

from modules.planner.kept import Kept

SLOT = (6, 1)
TIMELINE = {"source_id": 4, "row_key": "ref", "video": ""}
DUB = {"source_id": 2, "row_key": "g1", "video": ""}


def job_file(tmp_path, name: str = "G1.flac", size: int = 1000) -> str:
    folder = tmp_path / "job" / "out"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(b"x" * size)
    return str(path)


def test_a_kept_file_is_a_hard_link_of_the_file_of_the_job_not_a_copy(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    source = job_file(tmp_path)
    found = kept.keep(SLOT, TIMELINE, "G1", DUB, source)
    assert kept.find(SLOT, TIMELINE, "G1", DUB) == found
    assert os.path.samefile(source, found) and os.stat(source).st_nlink == 2


def test_the_data_stays_when_the_job_goes_and_goes_with_the_last_link(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    source = job_file(tmp_path)
    found = kept.keep(SLOT, TIMELINE, "G1", DUB, source)
    os.remove(source)  # the job's folder is removed with its record
    assert kept.find(SLOT, TIMELINE, "G1", DUB) == found and open(found, "rb").read() == b"x" * 1000
    assert kept.retire(SLOT) and not os.path.exists(found)


def test_an_alignment_is_found_only_for_the_timeline_and_the_row_it_was_made_for(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path))
    assert kept.find(SLOT, {**TIMELINE, "row_key": "hd"}, "G1", DUB) is None  # another video: not in step with it
    assert kept.find(SLOT, TIMELINE, "G1", {**DUB, "row_key": "g1-other"}) is None  # the voice-over of another row
    assert kept.find(SLOT, TIMELINE, "G2", DUB) is None and kept.find((6, 2), TIMELINE, "G1", DUB) is None


def test_a_file_that_is_not_whole_is_not_used(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    found = kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path))
    with open(found, "ab") as stream:  # written to after it was kept: it is not the file that was judged
        stream.write(b"y")
    assert kept.find(SLOT, TIMELINE, "G1", DUB) is None


def test_the_voice_overs_of_an_episode_are_kept_together_and_a_voice_over_again_replaces_its_own(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    first = kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path, "G1.flac"))
    second = kept.keep(SLOT, TIMELINE, "G2", {**DUB, "row_key": "g2"}, job_file(tmp_path, "G2.flac", 2000))
    assert kept.find(SLOT, TIMELINE, "G1", DUB) == first and kept.find(SLOT, TIMELINE, "G2", {**DUB, "row_key": "g2"}) == second
    again = kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path, "G1.new.flac", 1500))
    assert again == first and kept.find(SLOT, TIMELINE, "G1", DUB) == first and os.stat(first).st_size == 1500
    assert sorted(os.listdir(kept.folder(SLOT))) == ["G1.flac", "G2.flac", "manifest.json"]


def test_an_alignment_to_another_timeline_replaces_the_whole_set_of_the_episode(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    old = kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path))
    hd = {**TIMELINE, "row_key": "hd"}
    kept.keep(SLOT, hd, "G2", {**DUB, "row_key": "g2"}, job_file(tmp_path, "G2.flac"))
    assert kept.find(SLOT, TIMELINE, "G1", DUB) is None and not os.path.exists(old)
    assert sorted(os.listdir(kept.folder(SLOT))) == ["G2.flac", "manifest.json"]


def test_a_file_that_cannot_be_linked_is_an_error_and_the_set_is_as_it_was(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    found = kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path))
    with pytest.raises(OSError):
        kept.keep(SLOT, TIMELINE, "G2", {**DUB, "row_key": "g2"}, str(tmp_path / "no-such-file.flac"))
    assert kept.find(SLOT, TIMELINE, "G1", DUB) == found and kept.find(SLOT, TIMELINE, "G2", {**DUB, "row_key": "g2"}) is None


def test_the_episodes_that_hold_a_set_are_listed_and_the_series_goes_whole(tmp_path):
    kept = Kept(str(tmp_path / "kept"))
    assert kept.slots() == []
    kept.keep(SLOT, TIMELINE, "G1", DUB, job_file(tmp_path))
    kept.keep((6, 2), TIMELINE, "G1", DUB, job_file(tmp_path))
    assert kept.slots() == [(6, 1), (6, 2)]
    assert kept.retire((6, 1)) and not kept.retire((6, 1)) and kept.slots() == [(6, 2)]
    kept.drop()
    assert kept.slots() == [] and not os.path.exists(kept.root)
