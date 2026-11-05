"""The video of a source row that covers several episodes (a compilation): one file, downloaded once, read by the
voice-overs of every episode it covers, each aligned to the reference of its own episode (M-17, S-3, S-6, M-26)."""
import asyncio
import os

import pytest

from core.contracts import CompositionRow, RowState
from modules.voices import VoicesModule
from core.db import Database
from test_voices import (synced, db_path, dubs, finish, job_result, layout_changed, linked, quiet, reference_on_disk, restarted,  # noqa: F401
                         rows, settled, statuses_until, touch, until_true, downloads)

LINK = "https://site/title.html#kodik-Red-1-3"
CHANGED = "https://site/title.html#kodik-Red-1-4"


def compilation(first=1, last=3, *, voice="Red", player="kodik", link=LINK):
    return CompositionRow(
        series_id=7, source_id=1, row_key=f"{player}:{voice}:{first}-{last}", season=1, episode_start=first,
        episode_end=last, voice_label=voice, player=player, heights=[720], state=RowState.PLAYABLE, link=link)


def world(neighbours, *, videos=(1, 2, 3)):
    """The reference source (episodes 1-3) and one voice-over, Red, that has a single compilation row for 1-3."""
    del neighbours.sources[1:]
    neighbours.extra_rows = [compilation()]
    for number in videos:
        reference_on_disk(neighbours, number)


async def files(voices):
    return await voices.repo.files_of(7)


async def finish_job(probe, voices, settled_sub, neighbours, submit):
    """The job of this submit ends: its id is the one the service gave it, whatever order the episodes were sent in."""
    probe.publish_event("conform.job.finished", job_result(submit, neighbours.sent_keys[submit["key"]]))
    await asyncio.wait_for(settled_sub.queue.get(), 5)
    await quiet(voices)


@pytest.mark.asyncio
async def test_a_compilation_is_downloaded_once_and_each_episode_it_covers_is_aligned_to_its_own_reference(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    fetch = neighbours.fetches[0]
    assert len(neighbours.fetches) == 1 and fetch["key"] == "file:1" and fetch["url"] == LINK
    assert fetch["max_height"] == 720 and fetch["path"] == os.path.join(downloads(neighbours), "7", "f1", "7_f1_Red_kodik_site.mp4")
    assert [(r["episode"], r["voice_key"], r["status"], r["temp_path"]) for r in await rows(probe)] == [
        (number, "Red", "downloading", fetch["path"]) for number in (1, 2, 3)]
    touch(fetch["path"], b"video")
    finish(probe, "file:1")
    await statuses_until(sub, ["ready"] * 3 + ["aligning"] * 3)
    # One job a episode, each with the reference of its episode and the one file of the compilation.
    assert sorted(os.path.basename(submit["ref"]) for submit in neighbours.submits) == [
        f"7_s01e0{number}_ref.mp4" for number in (1, 2, 3)]
    assert all(submit["dubs"] == [fetch["path"]] for submit in neighbours.submits)
    for number, submit in enumerate(neighbours.submits, start=1):
        await finish_job(probe, voices, settled_sub, neighbours, submit)
        if number < 3:  # the file stays while a later episode reads it
            assert os.path.isfile(fetch["path"]) and [file["id"] for file in await files(voices)] == [1]
    assert not os.path.exists(os.path.dirname(fetch["path"])) and await files(voices) == []
    assert await voices.repo.cleanup_list() == []
    assert {r["status"] for r in await rows(probe)} == {"placed"}
    placed = sorted(r["output_path"] for r in await rows(probe))
    assert [os.path.basename(path) for path in placed] == [f"Series s01e0{n}.Red.ru.flac" for n in (1, 2, 3)]


@pytest.mark.asyncio
async def test_the_voice_overs_of_the_episodes_wait_on_one_transfer_and_show_it(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    assert {(r["state"]) for r in await rows(probe)} == {"download_queued"}
    probe.publish_event("downloads.fetch.started", {"key": "file:1", "series_id": 7, "path": ""})
    shown = {}
    while len(shown) < 3:
        event = (await asyncio.wait_for(sub.queue.get(), 3)).payload
        if event["state"] == "downloading":
            shown[event["episode"]] = event["file"]
    assert set(shown) == {1, 2, 3} and set(shown.values()) == {"7_f1_Red_kodik_site.mp4"}  # the queue names the file it reads
    assert {r["state"] for r in await rows(probe)} == {"downloading"}


@pytest.mark.asyncio
async def test_a_changed_row_is_a_file_of_its_own_and_the_old_file_stays_while_a_voice_over_waits_for_it(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours, videos=(1,))  # the videos of episodes 2 and 3 are not there: their voice-overs wait
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    first = neighbours.fetches[0]
    touch(first["path"], b"video")
    finish(probe, "file:1")
    await statuses_until(sub, ["ready"] * 3 + ["aligning"])
    await finish_job(probe, voices, settled_sub, neighbours, neighbours.submits[0])
    assert os.path.isfile(first["path"]) and [file["id"] for file in await files(voices)] == [1]
    # The table is read again: the row has another video and a fourth episode.
    reference = neighbours.sources[0]
    reference["episodes"].append(4)
    reference["links"]["4"] = "https://site/title.html#cvh-FumoDub-4"
    neighbours.plan.append((1, 4))
    neighbours.extra_rows = [compilation(1, 4, link=CHANGED)]
    layout_changed(probe)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    second = neighbours.fetches[1]
    assert second["key"] == "file:2" and second["url"] == CHANGED
    assert [(file["id"], file["url"]) for file in await files(voices)] == [(1, LINK), (2, CHANGED)]
    assert os.path.isfile(first["path"])  # episodes 2 and 3 still read the old file
    # Their videos come; the old file goes with the last of them, the new one stays for episode 4.
    for number in (2, 3):
        reference_on_disk(neighbours, number)
    probe.publish_event("series.downloaded.changed", {"series_id": 7, "count": 2})

    async def sent():
        return len(neighbours.submits) == 3

    await until_true(sent, len(neighbours.submits))
    await finish_job(probe, voices, settled_sub, neighbours, neighbours.submits[1])
    assert os.path.isfile(first["path"])
    await finish_job(probe, voices, settled_sub, neighbours, neighbours.submits[2])
    assert not os.path.exists(os.path.dirname(first["path"]))
    assert [file["id"] for file in await files(voices)] == [2]


@pytest.mark.asyncio
async def test_deleting_the_series_cancels_the_transfer_of_a_file_and_hands_its_folder_to_the_journal(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    partial = neighbours.fetches[0]["path"]
    touch(partial, b"half")
    purged = bus.subscribe("series.purged")
    probe.publish_event("series.deleted", {"series_id": 7})
    await asyncio.wait_for(purged.queue.get(), 3)
    assert neighbours.cancels == [7] and await rows(probe) == [] and await files(voices) == []
    assert not os.path.exists(os.path.dirname(partial)) and await voices.repo.cleanup_list() == []


@pytest.mark.asyncio
async def test_a_source_that_left_the_layout_during_a_transfer_takes_its_voice_overs_cancels_it_and_frees_the_file(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    path = neighbours.fetches[0]["path"]
    touch(path, b"half")  # what the transfer has written so far
    neighbours.extra_rows = []
    synced(probe)
    await statuses_until(sub, ["removed"] * 3)

    async def freed():
        return await files(voices) == [] and not os.path.exists(os.path.dirname(path))

    await until_true(freed, "the file was not freed")
    assert await rows(probe) == [] and neighbours.cancelled_keys == ["file:1"] and neighbours.cancels == []
    assert await voices.repo.cleanup_list() == []
    # The cancel came too late: the transfer ended all the same. The video it wrote has no owner and goes too.
    touch(path, b"video")
    probe.publish_event("downloads.fetch.finished", {"key": "file:1", "ok": True, "error": "", "transient": False,
                                                    "path": path, "series_id": 7})
    await until_true(freed, "the late video was not removed")
    assert await voices.repo.cleanup_list() == [] and await files(voices) == []
    # The source is back: the voice-overs need a file again and get a transfer of their own.
    neighbours.extra_rows = [compilation()]
    synced(probe)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 2)
    again = neighbours.fetches[1]
    assert again["key"] == "file:2" and again["url"] == LINK and again["path"] != path
    assert [file["id"] for file in await files(voices)] == [2]


@pytest.mark.asyncio
async def test_a_file_that_cannot_be_downloaded_sends_every_voice_over_to_its_next_candidate(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    neighbours.sources.append(linked("kodik", "Red", [1, 2]))  # the player of the compilation comes first
    neighbours.extra_rows = [compilation(player="cvh", link="https://site/title.html#cvh-Red-1-3")]
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    assert neighbours.fetches[0]["key"] == "file:1"
    finish(probe, "file:1", ok=False, error="gone")
    await settled(neighbours, 3)
    await quiet(voices)
    assert [(fetch["key"], fetch["url"]) for fetch in neighbours.fetches[1:]] == [
        ("dub:1", "https://site/title.html#kodik-Red-1"), ("dub:2", "https://site/title.html#kodik-Red-2")]
    by_episode = {r["episode"]: r for r in await rows(probe)}
    assert by_episode[1]["status"] == by_episode[2]["status"] == "downloading" and by_episode[1]["attempt"] == 1
    assert by_episode[3]["status"] == "failed" and by_episode[3]["error"] == "gone"

    async def freed():
        return await files(voices) == []

    # Nobody reads the failed file: it goes with the last that gave it up, a step after that voice-over is marked failed.
    await until_true(freed, "the failed file was not freed")


@pytest.mark.asyncio
async def test_a_file_that_failed_for_every_candidate_fails_every_voice_over_with_the_reason(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    finish(probe, "file:1", ok=False, error="timed out")
    await statuses_until(sub, ["failed"] * 3)
    assert {(r["status"], r["error"]) for r in await rows(probe)} == {("failed", "timed out")}

    async def released():
        return await files(voices) == []

    await until_true(released, "the failed file was not released")


@pytest.mark.asyncio
async def test_a_restart_in_the_middle_of_a_file_download_goes_on_with_the_same_file(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await voices.stop()
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        await settled(neighbours, 2)
        await quiet(again)
        assert [fetch["key"] for fetch in neighbours.fetches] == ["file:1", "file:1"]  # one file, sent again once
        assert [file["id"] for file in await again.repo.files_of(7)] == [1]
        touch(neighbours.fetches[1]["path"], b"video")
        finish(probe, "file:1")

        async def all_ready():
            return {r["status"] for r in await rows(probe)} <= {"ready", "aligning"}

        await until_true(all_ready, await rows(probe))
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_a_restart_in_the_middle_of_alignment_keeps_the_file_until_the_last_voice_over_is_placed(db_path, dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    sub = bus.subscribe("voices.dub.changed")
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    path = neighbours.fetches[0]["path"]
    touch(path, b"video")
    finish(probe, "file:1")
    await statuses_until(sub, ["ready"] * 3 + ["aligning"] * 3)
    await quiet(voices)
    await voices.stop()
    for number, submit in enumerate(neighbours.submits, start=1):  # the service ended the jobs while we were away
        neighbours.jobs[f"job-{number}"] = job_result(submit, f"job-{number}")
    again = VoicesModule(bus, Database(db_path))
    await restarted(again)
    try:
        for _ in range(3):
            await asyncio.wait_for(settled_sub.queue.get(), 10)
        await quiet(again)
        assert {r["status"] for r in await rows(probe)} == {"placed"}
        assert await again.repo.files_of(7) == [] and not os.path.exists(os.path.dirname(path))
    finally:
        await again.stop()


@pytest.mark.asyncio
async def test_with_a_limit_the_file_stays_for_the_episodes_that_wait_for_their_turn_and_is_downloaded_once(dubs):
    probe, neighbours, voices, bus = dubs
    world(neighbours)
    neighbours.settings["voices.max_temp_episodes"] = "1"
    settled_sub = bus.subscribe("voices.episode.settled")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 1)
    await quiet(voices)
    fetch = neighbours.fetches[0]
    assert {r["episode"]: r["status"] for r in await rows(probe)} == {1: "downloading", 2: "pending", 3: "pending"}
    touch(fetch["path"], b"video")
    finish(probe, "file:1")
    for number in (1, 2, 3):

        async def sent():
            return len(neighbours.submits) >= number

        await until_true(sent, (number, len(neighbours.submits)))
        await finish_job(probe, voices, settled_sub, neighbours, neighbours.submits[number - 1])
        if number < 3:  # the episodes that wait still read the file
            assert os.path.isfile(fetch["path"])
    assert len(neighbours.fetches) == 1 and await files(voices) == []
    assert {r["status"] for r in await rows(probe)} == {"placed"}
