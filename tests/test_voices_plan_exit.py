"""A voice-over that leaves the plan (the user takes it off, its source is gone) leaves whatever step it is at: a
download under way is cancelled, a track already placed next to the reference is removed."""
import os

import pytest

from core.contracts import EffectiveLever, Level, group_target
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
