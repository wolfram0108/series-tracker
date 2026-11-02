"""A voice-over that leaves the plan (the user takes it off, its source is gone) leaves whatever step it is at: a
download under way is cancelled, a track already placed next to the reference is removed."""
import os

import pytest

from core.contracts import EffectiveLever, Level, group_target, variant_target
from test_voices import (db_path, dubs, layout_changed, library_files, pass_over, placed_set, quiet, rows,  # noqa: F401
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
    layout_changed(probe)
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
    layout_changed(probe)
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
    layout_changed(probe)
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
    layout_changed(probe)
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
    layout_changed(probe)
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
    layout_changed(probe)
    await pass_over(voices, neighbours, asks)

    async def one_track():
        return [name for name in library_files(neighbours) if name.endswith(".flac")] == ["Series s01e01.BlackCat.ru.flac"]

    await until_true(one_track, ("tracks", library_files(neighbours)))
    [kept] = await voices.repo.for_series(7)
    assert kept["voice_key"] in before and kept["id"] in {each["id"] for each in before.values()}
    assert kept["status"] == "placed" and os.path.isfile(kept["output_path"]) and "BlackCat" in kept["output_path"]
