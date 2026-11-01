"""From the references chosen to the rows of media_items (modules/scan/candidates.py): identity, dates, what is left over.

Pure functions. What is on disk keeps its `unique_id`; a better version of a downloaded candidate gets a row of its
own; the date of a row is stored in one form whatever form the site gave it.
"""
import hashlib

import pytest

from core import ids
from modules.scan import candidates, reference
from modules.scan.candidates import NO_DATE, obsolete, place, stored_date
from scan_support import row

S, SRC = 7, 3


def chosen_of(*rows):
    return reference.choose(list(rows), (), ["vk"], [SRC])


def item(unique_id, url, *, resolution=480, status="pending", filename=None, season=1, episode=1, part=None, **fields):
    return {"unique_id": unique_id, "source_url": url, "resolution": resolution, "status": status,
            "final_filename": filename, "season": season, "episode_start": episode, "episode_end": None, "part": part,
            "slicing_status": "none", **fields}


# --- dates ------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("published, stored", [
    ("2026-05-09", "2026-05-09 12:00:00"),                      # ruchime: a day, no time
    ("2026-03-17T20:13:16Z", "2026-03-17 20:13:16"),            # the VK channel: UTC with Z
    ("2026-03-17T23:30:00+03:00", "2026-03-17 20:30:00"),       # an offset is brought to UTC
    ("2026-03-17T20:13:16", "2026-03-17 20:13:16"),             # naive is UTC
    (None, NO_DATE), ("", NO_DATE), ("   ", NO_DATE), ("yesterday", NO_DATE),
])
def test_the_date_of_a_row_is_stored_in_one_form(published, stored):
    assert stored_date(published) == stored


def test_the_stored_date_is_the_form_the_database_has_always_held():
    assert NO_DATE == "1970-01-01 12:00:00"
    assert ids.media_unique_id("u", stored_date("2026-05-09"), 1) == ids.media_unique_id("u", "2026-05-09 12:00:00", 1)


# --- identity --------------------------------------------------------------------------------------------------

def test_a_new_candidate_is_identified_by_its_row_of_the_layout_and_not_by_its_address_or_date():
    one = chosen_of(row(S, SRC, "k1", 1, 1, link="https://a", published="2026-01-01"))
    other = chosen_of(row(S, SRC, "k1", 1, 1, link="https://b", published="2027-01-01"))
    first, second = place(S, one, [])[0], place(S, other, [])[0]
    assert first.unique_id == second.unique_id == ids.row_unique_id(S, SRC, "k1") and first.is_new
    assert ids.row_unique_id(S, SRC, "k1") != ids.row_unique_id(S, SRC, "k2") != ids.row_unique_id(S, SRC + 1, "k2")
    assert ids.row_unique_id(S, SRC, "k1") != ids.row_unique_id(S + 1, SRC, "k1")


def test_the_formulas_of_the_ids_of_rows_of_the_layout_leave_the_old_ones_alone():
    old = hashlib.md5("u2026-01-01 00:00:00".encode() + b"1").hexdigest()[:16]
    assert ids.media_unique_id("u", "2026-01-01 00:00:00", 1) == old
    assert ids.upgrade_unique_id("u", "2026-01-01 00:00:00", 1, 1080) == hashlib.md5(
        b"u2026-01-01 00:00:001@1080p").hexdigest()[:16]
    key = ids.row_unique_id(1, 2, "k")
    assert key == ids.media_unique_id("row:2/k", "1970-01-01 00:00:00", 1) and len(key) == 16
    assert ids.upgrade_row_unique_id(1, 2, "k", 1080) == ids.upgrade_unique_id("row:2/k", "1970-01-01 00:00:00", 1, 1080)


def test_the_id_of_a_row_follows_the_series_the_source_the_key_and_the_resolution_of_a_better_version():
    base = ids.row_unique_id(1, 2, "k")
    assert len({base, ids.row_unique_id(2, 2, "k"), ids.row_unique_id(1, 3, "k"), ids.row_unique_id(1, 2, "j")}) == 4
    better = {ids.upgrade_row_unique_id(1, 2, "k", 720), ids.upgrade_row_unique_id(1, 2, "k", 1080)}
    assert len(better) == 2 and base not in better


def test_the_columns_written_for_a_candidate_come_from_its_row():
    placed = place(S, chosen_of(row(S, SRC, "k", 2, 5, part=1, height=720, link="https://a", published="2026-05-09",
                                    text="5 серия", voice="AniLibria")), [])[0]
    assert placed.fields == {
        "season": 2, "episode_start": 5, "episode_end": None, "part": 1, "source_url": "https://a",
        "download_url": None, "source_id": SRC, "row_key": "k", "publication_date": "2026-05-09 12:00:00",
        "voiceover_tag": "AniLibria", "source_title": "5 серия", "resolution": 720}


def test_a_candidate_with_no_text_is_titled_by_its_key():
    placed = place(S, chosen_of(row(S, SRC, "k9", 1, 1, text="")), [])[0]
    assert placed.fields["source_title"] == "k9"


@pytest.mark.parametrize("voice, name, tag", [
    ("AniLibria", "anilibria", "AniLibria"),                   # a group: its name, not the name as it came
    ("Неизвестная озвучка 2", "Red Head Sound", "Red Head Sound"),  # no group: the name as it came
    ("Неизвестная озвучка 3", "", None),                       # no name at all: no tag
])
def test_the_tag_of_the_voice_over_is_the_group_when_there_is_one_else_the_name_as_it_came(voice, name, tag):
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, voice=voice, name=name)), [])[0]
    assert placed.fields["voiceover_tag"] == tag


def test_a_row_of_the_slot_with_the_address_of_the_candidate_keeps_its_id_whatever_that_id_was_made_of():
    old = item("legacy-id", "https://a", resolution=720, status="completed", filename="e01.mp4")
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=720)), [old])[0]
    assert (placed.unique_id, placed.is_new) == ("legacy-id", False)
    assert "resolution" not in placed.fields


def test_a_downloaded_row_of_a_higher_resolution_stays_the_candidate_of_its_address():
    old = item("old", "https://a", resolution=2160, status="completed", filename="e01.mp4")
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [old])[0]
    assert (placed.unique_id, placed.is_new) == ("old", False)


def test_a_better_version_of_a_downloaded_candidate_gets_a_row_of_its_own():
    old = item("old", "https://a", resolution=480, status="completed", filename="e01.mp4")
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [old])[0]
    assert placed.is_new and placed.unique_id == ids.upgrade_row_unique_id(S, SRC, "k", 1080) != "old"
    assert placed.fields["resolution"] == 1080


def test_the_row_of_a_better_version_is_found_again_at_the_next_count():
    old = item("old", "https://a", resolution=480, status="completed", filename="e01.mp4")
    up = item(ids.upgrade_row_unique_id(S, SRC, "k", 1080), "https://a", resolution=1080)
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [old, up])[0]
    assert (placed.unique_id, placed.is_new) == (up["unique_id"], False)


def test_a_row_not_yet_fetched_takes_the_better_resolution_in_place():
    waiting = item("waiting", "https://a", resolution=480)
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [waiting])[0]
    assert (placed.unique_id, placed.is_new, placed.fields["resolution"]) == ("waiting", False, 1080)


def test_a_row_being_fetched_is_not_rewritten_under_the_download():
    busy = item("busy", "https://a", resolution=480, status="downloading")
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [busy])[0]
    assert placed.is_new and placed.unique_id == ids.upgrade_row_unique_id(S, SRC, "k", 1080)


def test_an_address_in_another_slot_is_another_candidate():
    elsewhere = item("elsewhere", "https://a", episode=2, status="completed", filename="e02.mp4")
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a")), [elsewhere])[0]
    assert placed.is_new and placed.unique_id == ids.row_unique_id(S, SRC, "k")


def test_the_same_row_numbered_otherwise_now_is_renumbered_in_place():
    moved = item(ids.row_unique_id(S, SRC, "k"), "https://a", episode=2, resolution=1080)
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 1, link="https://a", height=1080)), [moved])[0]
    assert (placed.unique_id, placed.is_new, placed.fields["episode_start"]) == (moved["unique_id"], False, 1)


def test_a_slot_with_no_row_has_no_placement():
    assert place(S, chosen_of(row(S, SRC, "c", 1, 1, 2)), []) == []


def test_a_candidate_born_in_a_switched_off_episode_is_born_switched_off():
    placed = place(S, chosen_of(row(S, SRC, "k", 1, 2)), [])[0]
    assert candidates.new_row(S, placed, {1: {2}})["is_ignored_by_user"] is True
    assert candidates.new_row(S, placed, {1: {3}})["is_ignored_by_user"] is False
    assert candidates.new_row(S, placed, None)["is_ignored_by_user"] is False


# --- what is left over -------------------------------------------------------------------------------------------

def test_rows_that_stand_for_nothing_and_are_not_fetched_are_left_over():
    rows = [item("win", "a"), item("lost", "b"), item("failed", "c", status="error"),
            item("done", "d", status="completed", filename="x.mp4"), item("going", "e", status="downloading"),
            item("sliced", "f", slicing_status="completed")]
    assert obsolete(rows, {"win"}, ()) == ["lost", "failed"]


def test_a_row_with_a_task_in_the_queue_is_not_left_over():
    rows = [item("lost", "b"), item("queued", "c")]
    assert obsolete(rows, set(), {"queued"}) == ["lost"]


def test_rows_after_follow_the_writes():
    rows = [item("a", "a"), item("b", "b"), item("c", "c")]
    after = candidates.rows_after(rows, [{"unique_id": "n", "season": 1, "episode_start": 9}],
                                  [("a", {"episode_start": 4})], ["c"])
    assert [(each["unique_id"], each["episode_start"]) for each in after] == [("a", 4), ("b", 1), ("n", 9)]
