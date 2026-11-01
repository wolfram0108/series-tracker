"""What a row of a voice-over needs to be downloaded (a sync admitted it), which recorded row a planned voice-over is
(the same link under another name), and what the naming of the work folder starts."""
import os

import pytest

from test_voices import (db_path, dubs, layout_changed, library_files, linked, pass_over, placed_set, quiet,  # noqa: F401
                         rows, settled, until_true)


async def stored(voices):
    return await voices.repo.for_series(7)


@pytest.mark.asyncio
async def test_a_row_a_change_of_the_layout_recorded_is_not_admitted_and_the_next_sync_admits_it(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    neighbours.sources.append(linked("kodik", "RedHeadSound", range(1, 4)))
    layout_changed(probe)
    await pass_over(voices, neighbours, 2)
    assert {(r["voice_key"], r["admitted"]) for r in await stored(voices)} == {("BlackCat", 1), ("RedHeadSound", 0)}
    assert len(neighbours.fetches) == 3 and {r["state"] for r in await rows(probe) if r["voice_key"] == "RedHeadSound"} == {
        "planned"}
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 6)
    assert {r["admitted"] for r in await stored(voices)} == {1}


@pytest.mark.asyncio
async def test_a_group_made_after_the_download_keeps_the_voice_over_its_key_and_its_work(dubs):
    probe, neighbours, voices, bus = dubs
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {(r["episode"], r["id"]) for r in await stored(voices)}
    for source in neighbours.sources:  # the user made a group for BlackCat: the layout calls it by the group's name
        if source["studio"] == "BlackCat":
            source["studio"] = "Black Group"
    layout_changed(probe)
    await pass_over(voices, neighbours, 2)
    found = await stored(voices)
    assert {(r["episode"], r["id"]) for r in found} == before  # no row was made, none was dropped
    assert {(r["voice_key"], r["voice_label"], r["status"]) for r in found} == {("BlackCat", "Black Group", "downloading")}
    assert len(neighbours.fetches) == 3
    announced = []
    while not sub.queue.empty():
        announced.append(sub.queue.get_nowait().payload)
    assert {event["voice_label"] for event in announced if event["voice_label"] == "Black Group"} == {"Black Group"}


@pytest.mark.asyncio
async def test_a_group_renamed_gives_the_same_voice_over_a_new_name_and_two_voices_with_other_links_stay_two(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    for source in neighbours.sources:
        if source["studio"] == "BlackCat":
            source["studio"] = "Renamed"
    neighbours.sources.append(linked("kodik", "Other", range(1, 4)))  # a voice-over with links of its own
    layout_changed(probe)
    await pass_over(voices, neighbours, 2)
    found = await stored(voices)
    assert sorted({(r["voice_key"], r["voice_label"]) for r in found}) == [("BlackCat", "Renamed"), ("Other", "Other")]
    assert len([r for r in found if r["voice_key"] == "BlackCat"]) == 3 == len([r for r in found if r["voice_key"] == "Other"])


@pytest.mark.asyncio
async def test_a_row_recorded_under_an_old_key_is_the_voice_over_the_layout_now_calls_by_its_label(dubs):
    probe, neighbours, voices, bus = dubs
    link = "https://site/title.html#kodik-BlackCat-1"
    await voices.repo.upsert(7, 1, 1, 0, "blackcat", "BlackCat", [{"player": "kodik", "studio": "BlackCat", "url": link}],
                             offer=True)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    keys = sorted((r["episode"], r["voice_key"]) for r in await stored(voices))
    assert keys == [(1, "blackcat"), (2, "BlackCat"), (3, "BlackCat")]


@pytest.mark.asyncio
async def test_a_placed_track_follows_the_new_name_of_its_voice_over(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    neighbours.sources[-1]["studio"] = "Red Group"  # RedHeadSound was put into a group
    layout_changed(probe)
    async def moved():
        return "Series s01e01.Red Group.ru.flac" in library_files(neighbours)

    await until_true(moved, library_files(neighbours))
    assert not [name for name in library_files(neighbours) if "RedHeadSound" in name]
    assert {(r["voice_key"], r["voice_label"], r["status"]) for r in await stored(voices)} == {
        ("BlackCat", "BlackCat", "placed"), ("RedHeadSound", "Red Group", "placed")}


@pytest.mark.asyncio
async def test_the_naming_of_the_work_folder_starts_what_waited_for_it(dubs):
    probe, neighbours, voices, bus = dubs
    work = neighbours.settings["conform.work_dir"]
    neighbours.settings["conform.work_dir"] = ""
    probe.send_command("voices.dispatch", {"series_id": 7})

    async def recorded():
        return len(await rows(probe)) == 3

    await until_true(recorded, "the rows were not recorded")
    await quiet(voices)
    assert neighbours.fetches == [] and {r["state"] for r in await rows(probe)} == {"planned"}
    assert {r["admitted"] for r in await stored(voices)} == {1}  # the sync admitted them; the folder is what is missing
    neighbours.settings["conform.work_dir"] = work
    probe.publish_event("conform.settings.changed", {"url": "", "work_dir": work, "work_dir_remote": "",
                                                    "clear_finished": False})
    await settled(neighbours, 3)
    assert {r["status"] for r in await rows(probe)} == {"downloading"}


@pytest.mark.asyncio
async def test_two_parts_of_one_voice_over_are_two_rows_and_the_second_does_not_take_the_chain_of_the_first(dubs):
    _probe, _neighbours, voices, _bus = dubs
    first = [{"player": "kodik", "studio": "BlackCat", "url": "https://site/title.html#part-1"}]
    second = [{"player": "kodik", "studio": "BlackCat", "url": "https://site/title.html#part-2"}]
    await voices.repo.upsert(7, 1, 9, 1, "blackcat", "BlackCat", first, offer=True)
    await voices.repo.upsert(7, 1, 9, 2, "blackcat", "BlackCat", second, offer=True)
    one, two = await voices.repo.for_episode(7, 1, 9, 1), await voices.repo.for_episode(7, 1, 9, 2)
    assert [row["chain"][0]["url"] for row in one] == [first[0]["url"]]
    assert [row["chain"][0]["url"] for row in two] == [second[0]["url"]]
    assert await voices.repo.for_episode(7, 1, 9, 0) == []
