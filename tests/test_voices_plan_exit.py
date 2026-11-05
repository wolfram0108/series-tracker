"""A voice-over that leaves the plan (the user takes it off, its source is gone) leaves whatever step it is at: a
download under way is cancelled, a track already placed next to the reference is removed."""
import os

import pytest

from core.contracts import CompositionRow, EffectiveLever, Level, ReferenceMark, RowState, group_target, variant_target
from test_voices import (synced, db_path, dubs, layout_changed, library_files, pass_over, placed_set, quiet, rows,  # noqa: F401
                         settled, until_true)


def take_off(neighbours, voice, season=1, episode=1):
    neighbours.levers[(season, episode)] = [
        EffectiveLever(lever="voice.take", target=group_target(voice), value=False, level=Level.EPISODE)]


@pytest.mark.asyncio
async def test_a_voice_over_the_user_takes_off_while_it_downloads_is_dropped_and_its_download_cancelled(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    taken = {r["episode"]: r["id"] for r in await voices.repo.for_series(7)}
    assert all(r["status"] == "downloading" and r["file_id"] is None for r in await voices.repo.for_series(7))
    take_off(neighbours, "BlackCat")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)
    assert sorted(r["episode"] for r in await voices.repo.for_series(7)) == [2, 3]
    assert neighbours.cancelled_keys == [f"dub:{taken[1]}"]


@pytest.mark.asyncio
async def test_a_track_already_placed_is_removed_when_its_voice_over_leaves_the_plan(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    before = library_files(neighbours)
    tracks = [name for name in before if "RedHeadSound" in name]
    assert tracks and any("BlackCat" in name for name in before)
    take_off(neighbours, "RedHeadSound")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    async def gone():
        return not any("RedHeadSound" in name for name in library_files(neighbours))

    await until_true(gone, ("the track stayed", library_files(neighbours)))
    assert [r["voice_key"] for r in await voices.repo.for_series(7)] == ["BlackCat"]
    assert any("BlackCat" in name for name in library_files(neighbours)) and os.path.isfile(ref)


def leave_out(neighbours, voice, player, season=1, episode=1):
    """The user leaves the variant (voice-over, source 1, player) out of the episode."""
    neighbours.levers.setdefault((season, episode), []).append(
        EffectiveLever(lever="variant.allow", target=variant_target(voice, 1, player), value=False, level=Level.EPISODE))


def players_of(row):
    return [each["player"] for each in row["chain"]]


@pytest.mark.asyncio
async def test_a_variant_the_user_leaves_out_stops_the_download_that_runs_from_it_and_the_voice_goes_on_from_the_others(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert players_of(before[1]) == ["cvh", "kodik"] and before[1]["status"] == "downloading"
    leave_out(neighbours, "BlackCat", "cvh")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert neighbours.cancelled_keys == [f"dub:{before[1]['id']}"]
    assert after[1]["id"] != before[1]["id"] and after[1]["status"] == "pending" and players_of(after[1]) == ["kodik"]
    assert {n: after[n]["id"] for n in (2, 3)} == {n: before[n]["id"] for n in (2, 3)}  # the other episodes are left alone


@pytest.mark.asyncio
async def test_a_variant_left_out_is_taken_out_of_the_chain_of_work_that_runs_from_another(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    leave_out(neighbours, "BlackCat", "kodik")  # the next candidate: the download under way is from cvh
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert after[1]["id"] == before[1]["id"] and after[1]["status"] == "downloading"
    assert players_of(after[1]) == ["cvh"] and after[1]["attempt"] == 0
    assert neighbours.cancelled_keys == [] and neighbours.cancels == []


@pytest.mark.asyncio
async def test_a_track_made_from_a_variant_the_user_leaves_out_is_removed_and_the_voice_is_planned_again(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    assert any("BlackCat" in name for name in library_files(neighbours))
    black = next(r for r in await voices.repo.for_series(7) if r["voice_key"] == "BlackCat")
    used = black["chain"][black["attempt"]]["player"]
    leave_out(neighbours, "BlackCat", used)
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    async def gone():
        return not any("BlackCat" in name for name in library_files(neighbours))

    await until_true(gone, ("the track stayed", library_files(neighbours)))
    black = next(r for r in await voices.repo.for_series(7) if r["voice_key"] == "BlackCat")
    assert black["status"] == "pending" and used not in players_of(black) and players_of(black)
    assert any("RedHeadSound" in name for name in library_files(neighbours))  # the other voice-over is not touched


@pytest.mark.asyncio
async def test_two_voice_overs_the_user_makes_one_leave_one_track_under_the_name_of_the_group_and_remove_the_other(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    before = {r["voice_key"]: r for r in await voices.repo.for_series(7)}
    assert set(before) == {"BlackCat", "RedHeadSound"}
    for source in neighbours.sources:  # the user says RedHeadSound is BlackCat: one group, one name
        if source["studio"] == "RedHeadSound":
            source["studio"] = "BlackCat"
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    async def one_track():
        return [name for name in library_files(neighbours) if name.endswith(".flac")] == ["Series s01e01.BlackCat.ru.flac"]

    await until_true(one_track, ("tracks", library_files(neighbours)))
    [kept] = await voices.repo.for_series(7)
    assert kept["voice_key"] in before and kept["id"] in {each["id"] for each in before.values()}
    assert kept["status"] == "placed" and os.path.isfile(kept["output_path"]) and "BlackCat" in kept["output_path"]


async def merged_into_blackcat(dubs):
    """BlackCat has no track (the service refused it), RedHeadSound has one; then the source of RedHeadSound is named BlackCat."""
    from test_voices import job_done, statuses_until, two_voices_ready
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])
    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1", BlackCat={"ok": False, "refused": True}))

    async def one_placed_one_refused():
        return {r["voice_key"]: r["status"] for r in await voices.repo.for_series(7)} == {
            "BlackCat": "rejected", "RedHeadSound": "placed"}

    await until_true(one_placed_one_refused, ("the track was not placed", await voices.repo.for_series(7)))
    await quiet(voices)
    # A new player for the source keeps the rows of the layout apart; the link of the row is the one the row has had.
    neighbours.sources[-1]["studio"], neighbours.sources[-1]["player"] = "BlackCat", "alloha"
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)
    return probe, neighbours, voices, ref


@pytest.mark.asyncio
async def test_two_voice_overs_that_became_one_keep_the_track_that_was_placed_though_the_other_row_has_the_name(dubs):
    probe, neighbours, voices, ref = await merged_into_blackcat(dubs)

    assert [(r["voice_key"], r["voice_label"], r["status"]) for r in await voices.repo.for_series(7)] == [
        ("RedHeadSound", "BlackCat", "placed")]

    async def track_follows_the_name():
        names = library_files(neighbours)
        return any(name.endswith(".BlackCat.ru.flac") for name in names) and not any("RedHeadSound" in name for name in names)

    await until_true(track_follows_the_name, ("the track did not follow the name", library_files(neighbours)))


@pytest.mark.asyncio
async def test_a_variant_the_user_leaves_out_stops_the_download_that_runs_from_it_after_the_link_of_its_row_changed(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert players_of(before[1]) == ["cvh", "kodik"] and before[1]["status"] == "downloading"
    neighbours.sources[2]["links"]["1"] = "https://site/moved#cvh-BlackCat-1"  # the page gave the row another address
    leave_out(neighbours, "BlackCat", "cvh")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert neighbours.cancelled_keys == [f"dub:{before[1]['id']}"]
    assert after[1]["id"] != before[1]["id"] and after[1]["status"] == "pending" and players_of(after[1]) == ["kodik"]


def leave_voices(neighbours, *voices):
    neighbours.levers[(1, 1)] = [EffectiveLever(lever="voice.take", target=group_target(voice), value=False, level=Level.EPISODE)
                                 for voice in voices]


@pytest.mark.asyncio
async def test_a_job_two_voice_overs_share_goes_on_for_the_one_that_is_left_and_is_cancelled_when_the_last_goes(dubs):
    from test_voices import job_done, statuses_until, two_voices_ready
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])

    leave_voices(neighbours, "RedHeadSound")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)
    [left] = await voices.repo.for_series(7)
    assert left["voice_key"] == "BlackCat" and left["status"] == "aligning" and neighbours.conform_cancels == []

    probe.publish_event("conform.job.finished", job_done(neighbours, "job-1"))

    async def placed():
        return [r["status"] for r in await voices.repo.for_series(7)] == ["placed"]

    await until_true(placed, ("the voice that stayed was not placed", await voices.repo.for_series(7)))
    assert neighbours.conform_cancels == [] and not any("RedHeadSound" in name for name in library_files(neighbours))


@pytest.mark.asyncio
async def test_the_job_is_cancelled_when_every_voice_over_that_shared_it_has_gone(dubs):
    from test_voices import statuses_until, two_voices_ready
    probe, neighbours, voices, bus, sub, ref = await two_voices_ready(dubs)
    await statuses_until(sub, ["ready", "ready", "aligning", "aligning"])

    leave_voices(neighbours, "RedHeadSound", "BlackCat")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    async def cancelled():
        return neighbours.conform_cancels == ["job-1"]

    await until_true(cancelled, ("the job was not cancelled", neighbours.conform_cancels))


@pytest.mark.asyncio
async def test_a_chain_recorded_before_candidates_said_their_source_is_judged_safely_after_the_link_of_its_row_changed(dubs):
    """The row of the stored chain has no source: a variant the user left out is found by its player, so that no work from a
    variant that is left out goes on because its link changed."""
    import json
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert players_of(before[1]) == ["cvh", "kodik"] and before[1]["status"] == "downloading"
    legacy = json.dumps([{key: value for key, value in each.items() if key != "source_id"} for each in before[1]["chain"]])
    await voices.repo._db.execute("UPDATE voice_dubs SET chain=? WHERE id=?", (legacy, before[1]["id"]))
    neighbours.sources[2]["links"]["1"] = "https://site/moved#cvh-BlackCat-1"
    leave_out(neighbours, "BlackCat", "cvh")
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert neighbours.cancelled_keys == [f"dub:{before[1]['id']}"]
    assert after[1]["id"] != before[1]["id"] and players_of(after[1]) == ["kodik"]


def shift_cvh_black_cat(neighbours, keep=True):
    """The layout gives the videos of the voice-over BlackCat of CVH, the rows of episodes 1 and 2, to the episodes after them
    (the row keys and the links are the same); with `keep` False the layout has no such video at all."""
    source = neighbours.sources[2]
    moved_rows = [CompositionRow(
        series_id=7, source_id=1, row_key=f"cvh:BlackCat:{number}", season=1, episode_start=number + 1,
        episode_end=number + 1, voice_label="BlackCat", player="cvh", heights=[720], state=RowState.PLAYABLE,
        link=source["links"][str(number)]) for number in (1, 2)]
    source["episodes"] = []
    neighbours.extra_rows = moved_rows if keep else []


@pytest.mark.asyncio
async def test_work_on_a_video_the_layout_gives_to_another_episode_is_taken_back_and_planned_again_and_the_rest_stands(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert players_of(before[1]) == ["cvh", "kodik"] and players_of(before[3]) == ["kodik"]
    assert all(r["status"] == "downloading" for r in before.values())
    shift_cvh_black_cat(neighbours)
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert sorted(neighbours.cancelled_keys) == sorted(f"dub:{before[number]['id']}" for number in (1, 2))
    assert after[1]["id"] != before[1]["id"] and after[2]["id"] != before[2]["id"]
    assert [after[number]["status"] for number in (1, 2)] == ["pending", "pending"]
    assert players_of(after[1]) == ["kodik"] and players_of(after[2]) == ["cvh", "kodik"]
    assert after[2]["chain"][0]["row_key"] == "cvh:BlackCat:1"  # the video that was episode 1 is episode 2 now
    assert after[3]["id"] == before[3]["id"] and after[3]["status"] == "downloading"  # its own video is where it was


@pytest.mark.asyncio
async def test_work_on_a_video_the_layout_does_not_have_at_all_stands_as_a_page_read_short_is_no_evidence(dubs):
    probe, neighbours, voices, bus = dubs
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    before = {r["episode"]: r for r in await voices.repo.for_series(7)}
    shift_cvh_black_cat(neighbours, keep=False)
    asks = neighbours.layout_asks + 1
    layout_changed(probe)
    await pass_over(voices, neighbours, asks)

    after = {r["episode"]: r for r in await voices.repo.for_series(7)}
    assert {n: after[n]["id"] for n in (1, 2, 3)} == {n: before[n]["id"] for n in (1, 2, 3)}
    assert all(r["status"] == "downloading" for r in after.values()) and neighbours.cancelled_keys == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["take_off", "leave_out", "reference_gone"])
async def test_a_change_without_a_sync_takes_nothing_away_and_the_sync_does(dubs, change):
    """И-59: a rule saved, a voice-over taken off, a variant left out, a reference gone — a recount of the plan does not
    cancel a download, remove a track or a file; the sync of the series does."""
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    before = {(r["episode"], r["voice_key"]): r for r in await voices.repo.for_series(7)}
    files = sorted(library_files(neighbours))
    if change == "take_off":
        take_off(neighbours, "BlackCat")
    elif change == "leave_out":
        black = next(r for r in before.values() if r["voice_key"] == "BlackCat")
        leave_out(neighbours, "BlackCat", black["chain"][black["attempt"]]["player"])
    else:
        neighbours.marks[(1, 1)] = ReferenceMark.NONE
    asks = neighbours.layout_asks + 1
    layout_changed(probe)
    await pass_over(voices, neighbours, asks)
    after = {(r["episode"], r["voice_key"]): r for r in await voices.repo.for_series(7)}
    assert {key: (row["id"], row["status"]) for key, row in after.items()} == {
        key: (row["id"], row["status"]) for key, row in before.items()}
    assert sorted(library_files(neighbours)) == files and neighbours.cancelled_keys == [] and neighbours.conform_cancels == []

    if change == "reference_gone":
        return  # without a reference only the temporary files go, at the sync (_drop_without_reference)
    asks = neighbours.layout_asks + 1
    synced(probe)
    await pass_over(voices, neighbours, asks)
    assert {(r["episode"], r["voice_key"]): r["id"] for r in await voices.repo.for_series(7)} != {
        key: row["id"] for key, row in before.items()}


@pytest.mark.asyncio
async def test_the_run_of_a_series_hears_whether_voices_has_work_of_it(dubs):
    """A voice-over a sync admitted and not at its end is work of the run of the series (ЭТАП_11); placed ones are not."""
    probe, neighbours, voices, bus = dubs
    idle = await probe.request("voices.work.get", {"series_id": 7}, timeout=5)
    assert idle["active"] is False
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    busy = await probe.request("voices.work.get", {"series_id": 7}, timeout=5)
    # Every state counted is numbered after the one before: the run orders a reply and an event by it (I-60).
    assert {key: busy[key] for key in ("owner", "series_id", "active")} == {"owner": "voices", "series_id": 7, "active": True}
    assert busy["seq"] > idle["seq"]
    # The work names its episodes: the run packs an episode when no executor holds work of it.
    rows = await voices.repo.for_series(7)
    assert sorted(map(tuple, busy["busy"])) == sorted({(row["season"], row["episode"], row["part"] or 0) for row in rows
                                                       if row["admitted"] and row["status"] not in ("placed", "rejected", "failed")})
    assert busy["busy"]


@pytest.mark.asyncio
async def test_the_status_of_a_series_shows_alignment_while_its_voice_overs_are_worked_on(dubs):
    probe, neighbours, voices, bus = dubs
    contributions = bus.subscribe("series.status.contribution")
    probe.send_command("voices.dispatch", {"series_id": 7})
    await settled(neighbours, 3)
    await quiet(voices)
    told = []
    while not contributions.queue.empty():
        payload = contributions.queue.get_nowait().payload
        if payload["source"] == "voices" and payload["series_id"] == 7:
            told.append(payload["flags"])
    assert told and told[-1] == {"aligning": True}  # told once when it changes, not on every event of a row
    assert len(told) == len([each for index, each in enumerate(told) if index == 0 or told[index - 1] != each])


@pytest.mark.asyncio
async def test_a_series_whose_voice_overs_are_all_placed_has_no_voices_work(dubs):
    probe, neighbours, bus, sub, ref = await placed_set(dubs)
    voices = dubs[2]
    rows = await voices.repo.for_series(7)
    active = any(row["admitted"] and row["status"] not in ("placed", "rejected", "failed") for row in rows)
    assert (await probe.request("voices.work.get", {"series_id": 7}, timeout=5))["active"] is active
