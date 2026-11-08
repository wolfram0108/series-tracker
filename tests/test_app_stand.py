"""The whole application on its stand (tests/app_stand.py): the user's way through the API, every module real, the site,
the player and the alignment service as models — the full circle of the plan of a series."""
import pytest

from app_stand import AppStand
from plan_world import truth_of


@pytest.fixture
async def stand(tmp_path):
    made = AppStand(str(tmp_path))
    await made.start()
    yield made
    await made.stop()
    made.close()


async def series_of(stand, episodes=(1, 2), *, ref_height=720, dub_height=480):
    stand.site.release("ref", "AniStar")
    stand.site.release("dub", "AnimeVost")
    for number in episodes:
        stand.site.episode("ref", number, height=ref_height)
        stand.site.episode("dub", number, height=dub_height)
    await stand.configure(["AniStar", "AnimeVost"])
    return await stand.series("Сериал", ["ref", "dub"])


async def test_a_series_added_and_synced_is_downloaded_aligned_and_built(stand):
    series_id = await series_of(stand)
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert len(view["assets"]) == 2
    assert all([track["group"] for track in asset["tracks"]] == ["AnimeVost"] for asset in view["assets"]), view
    assert {each["mark"] for each in view["references"]} == {"by_level"}
    again = await stand.sync(series_id)
    assert {track["kind"] for episode in again["episodes"] for track in episode["tracks"]} == {"reuse"}


async def test_a_track_deleted_comes_back_by_the_next_sync(stand):
    series_id = await series_of(stand, (1,))
    view = await stand.sync(series_id)
    (asset,) = view["assets"]
    await stand.call("POST", f"/api/series/{series_id}/plan/delete",
                     {"kind": "track", "name": asset["name"], "dub": asset["tracks"][0]["row"]})
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert [track["group"] for track in view["assets"][0]["tracks"]] == ["AnimeVost"] and not view["commands"]


async def test_a_better_reference_takes_the_place_and_the_voice_over_whose_source_went_follows_it(stand):
    """The voice-over of episode 1 is gone from its source (a page read whole, with episode 2 on it): its track is an
    offline row, aligned anew out of the old file onto the better reference."""
    series_id = await series_of(stand, (1, 2))
    await stand.sync(series_id)
    stand.site.drop("dub", 1)
    stand.site.episode("ref", 1, height=1080, truth=truth_of(1, 1))
    stand.later()
    await stand.sync(series_id)
    view = await stand.check(series_id)
    (asset,) = [each for each in view["assets"] if each["episode"] == [1, 1]]
    assert asset["video"]["height"] == 1080 and [track["group"] for track in asset["tracks"]] == ["AnimeVost"]
    assert asset["tracks"][0]["online"] is False


async def test_a_voice_over_switched_off_for_the_series_goes_from_the_files(stand):
    series_id = await series_of(stand, (1,))
    await stand.sync(series_id)
    await stand.call("PUT", f"/api/series/{series_id}/output/settings", {
        "season": None, "lever": "voice.take", "target": "group:AnimeVost", "value": False})
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert view["assets"][0]["tracks"] == []
