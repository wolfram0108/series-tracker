"""The whole application on its stand (tests/app_stand.py): the user's way through the API, every module real, the site,
the player and the alignment service as models — the full circle of the plan of a series."""
import asyncio
import json
import os
import subprocess

import pytest

from app_stand import STAND_LIMIT_S, AppStand
from core import container as mkv
from core.envelope import Envelope
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


async def test_an_episode_switched_off_hides_its_file_and_switched_on_shows_it_with_no_work(stand):
    series_id = await series_of(stand, (1, 2))
    await stand.sync(series_id)
    await stand.call("PUT", f"/api/series/{series_id}/output/switch", {"season": 1, "episode": 2, "off": True})
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert [name.endswith(".mkv.off") for name in stand.files()] == [False, True]
    assert {each["mark"] for each in view["references"] if each["episode"] == 2} == {"off"}
    await stand.call("PUT", f"/api/series/{series_id}/output/switch", {"season": 1, "episode": 2, "off": False})
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert not any(name.endswith(".off") for name in stand.files())
    assert {track["kind"] for episode in view["episodes"] for track in episode["tracks"]} == {"reuse"}


async def test_a_stop_of_the_process_in_the_middle_of_a_sync_is_carried_to_the_end_after_the_start(stand):
    """The service stops while the planner works (a download, an alignment under way): after the start the run of the
    sync and the sync of the plan are taken up again from what is written, and end with the files whole."""
    series_id = await series_of(stand, (1, 2))
    working = stand.bus.subscribe("planner.series.changed")
    await stand.call("POST", f"/api/series/{series_id}/scan")
    while not (await asyncio.wait_for(working.queue.get(), STAND_LIMIT_S)).payload["running"]:
        pass
    await stand.restart()
    await stand.until_run_ends(series_id)
    view = await stand.check(series_id)
    assert len(view["assets"]) == 2 and not view["running"]


async def test_a_stop_while_the_service_aligns_takes_its_job_up_again_and_sends_none_twice(stand):
    """The process stops while the service works on the alignment of an episode: after the start the sync finds the
    job by its key and takes its end; no job is sent twice, and the folder of the job keeps what the service wrote
    (the plots) while its record is in the service's history."""
    series_id = await series_of(stand, (1,))
    stand.daemon.hold = True
    await stand.call("POST", f"/api/series/{series_id}/scan")
    await asyncio.wait_for(asyncio.to_thread(stand.daemon.made.wait), STAND_LIMIT_S)
    await stand.restart()
    stand.daemon.release()
    await stand.until_run_ends(series_id)
    view = await stand.check(series_id)
    assert len(view["assets"]) == 1 and len(stand.daemon.jobs) == 1, list(stand.daemon.jobs.values())
    (job,) = stand.daemon.jobs.values()
    kept = [folder for folder, _, files in os.walk(os.path.join(stand.root, "work")) if files]
    assert kept and all(job["label"].rsplit("#", 1)[1] in folder for folder in kept), kept
    del stand.daemon.jobs[job["id"]]
    stand.bus.publish(Envelope(topic="conform.job.removed", kind="event", payload={"job_id": job["id"]}))
    await stand.bus.settled()
    assert not [folder for folder, _, files in os.walk(os.path.join(stand.root, "work")) if files]


async def test_episodes_a_higher_compilation_holds_are_cut_out_of_it_by_the_service(stand):
    """A compilation of episodes 1–3 in 2160p over single episodes in 720p: the planner downloads it and the alignment
    service (its model, through the real conform) cuts each episode out of it by the single video of the episode."""
    stand.site.release("ref", "AniStar")
    for number in (1, 2, 3):
        stand.site.episode("ref", number, height=720)
    stand.site.compilation("ref", 1, 3, height=2160)
    await stand.configure(["AniStar"])
    series_id = await stand.series("Сериал", ["ref"])
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert sorted(asset["episode"][1] for asset in view["assets"] if asset["video"]["cut_of"]) == [1, 2, 3]
    assert all(asset["video"]["height"] == 2160 for asset in view["assets"])


async def test_a_video_deleted_is_downloaded_anew_while_its_row_is_online(stand):
    """«Удалить» the video of a file whose row is online: «скачать заново» — the file goes for a fresh download of it."""
    series_id = await series_of(stand, (1,))
    (asset,) = (await stand.sync(series_id))["assets"]
    await stand.call("POST", f"/api/series/{series_id}/plan/delete", {"kind": "video", "name": asset["name"]})
    (planned,) = (await stand.call("GET", f"/api/series/{series_id}/plan"))["episodes"]
    assert planned["video"]["kind"] == "download" and planned["video"]["row"] == asset["video"]["row"]
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert view["assets"][0]["video"]["row"] == asset["video"]["row"] and not view["commands"]


@pytest.mark.parametrize("seconds, aligned", [(3, 1), (1, 0)])
async def test_a_video_downloaded_anew_to_another_length_has_its_voice_overs_aligned_to_it_again(stand, seconds, aligned):
    """The host recut the video of a row (the same row, another length): «Удалить» the video downloads it anew, its
    length is known only then, and the voice-over aligned to the file before is aligned to it again; a video of the same
    length keeps the voice-over as it is."""
    series_id = await series_of(stand, (1,))
    (asset,) = (await stand.sync(series_id))["assets"]
    path = os.path.join(stand.site.root, f"ref-ep1-truth{truth_of(1, 1)}-file.mp4")
    os.remove(path)
    await asyncio.to_thread(stand.codec.make, path, [truth_of(1, 1)] * seconds)
    jobs = len(stand.daemon.jobs)
    await stand.call("POST", f"/api/series/{series_id}/plan/delete", {"kind": "video", "name": asset["name"]})
    await stand.sync(series_id)
    view = await stand.check(series_id)
    assert len(stand.daemon.jobs) - jobs == aligned, list(stand.daemon.jobs.values())
    (built,) = view["assets"]
    assert built["video"]["row"] == asset["video"]["row"] and [each["group"] for each in built["tracks"]] == ["AnimeVost"]
    assert await asyncio.to_thread(stand.codec.seconds, os.path.join(stand.library, built["name"])) == seconds
    again = await stand.sync(series_id)
    assert {track["kind"] for episode in again["episodes"] for track in episode["tracks"]} == {"reuse"}
    assert len(stand.daemon.jobs) - jobs == aligned


async def test_a_video_deleted_whose_row_the_source_forgot_gives_its_place_to_another_source(stand):
    """The source forgot the row of the video (a page read whole, with episode 2 on it), «Удалить» the video: the deleted
    video is the last candidate, the video of the other source gives the picture, every sound on the new picture."""
    series_id = await series_of(stand, (1, 2))
    await stand.sync(series_id)
    stand.site.drop("ref", 1)
    stand.later()
    await stand.sync(series_id)
    (asset,) = [each for each in (await stand.check(series_id))["assets"] if each["episode"] == [1, 1]]
    first = asset["video"]["row"]
    await stand.call("POST", f"/api/series/{series_id}/plan/delete", {"kind": "video", "name": asset["name"]})
    await stand.sync(series_id)
    view = await stand.check(series_id)
    (asset,) = [each for each in view["assets"] if each["episode"] == [1, 1]]
    assert asset["video"]["row"] != first and asset["video"]["height"] == 480 and not view["commands"]


async def test_a_download_that_failed_is_passed_over_until_the_failures_are_reset(stand):
    series_id = await series_of(stand, (1,))
    stand.site.gone("dub", 1)
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert view["assets"][0]["tracks"] == [] and [each["kind"] for each in view["failures"]] == ["download"]
    again = await stand.sync(series_id)
    assert again["failures"] == view["failures"] and again["assets"] == view["assets"]
    stand.site.gone("dub", 1, False)
    assert (await stand.call("POST", f"/api/series/{series_id}/failures/reset"))["reset"] == 1
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert [track["group"] for track in view["assets"][0]["tracks"]] == ["AnimeVost"] and not view["failures"]


async def test_a_voice_over_the_service_refused_to_align_is_passed_over_until_the_failures_are_reset(stand):
    series_id = await series_of(stand, (1,))
    stand.daemon.answers["Сериал s01e01 AnimeVost"] = "mismatch"
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert view["assets"][0]["tracks"] == [] and [each["kind"] for each in view["failures"]] == ["align"]
    # The episode lacks the voice-over the attempt was for: the series is an error.
    assert not view["failures"][0]["covered"]
    assert "error" in (await stand.call("GET", f"/api/series/{series_id}"))["statuses"]
    jobs = len(stand.daemon.jobs)
    await stand.sync(series_id)
    assert len(stand.daemon.jobs) == jobs  # nothing is asked of the service again
    del stand.daemon.answers["Сериал s01e01 AnimeVost"]
    await stand.call("POST", f"/api/series/{series_id}/failures/reset")
    view = await stand.sync(series_id)
    await stand.check(series_id)
    assert [track["group"] for track in view["assets"][0]["tracks"]] == ["AnimeVost"] and not view["failures"]
    assert "error" not in (await stand.call("GET", f"/api/series/{series_id}"))["statuses"]


async def test_a_voice_over_file_shorter_than_any_episode_is_broken_and_the_next_source_gives_it(stand):
    """A host broke the upload of an episode (a studio's intro of seconds in place of it): the file is whole and as long as
    the host says, and shorter than any episode (core/notices.EPISODE_MIN_S, of the scale of the stand). Its download
    fails, nothing of it reaches the service, and the same voice-over comes from the next source."""
    stand.site.release("ref", "AniStar")
    stand.site.release("dub", "AnimeVost")
    stand.site.release("dub2", "AnimeVost")
    truth = truth_of(1, 1)
    broken = os.path.join(stand.site.root, f"dub-ep1-truth{truth}-file.mp4")
    whole = os.path.join(stand.site.root, "whole.mp4")
    await asyncio.to_thread(stand.codec.make, whole, [truth])
    await asyncio.to_thread(subprocess.run, ["ffmpeg", "-v", "error", "-y", "-i", whole, "-t", "0.25", "-c", "copy", broken],
                            check=True, timeout=60)
    for slug in ("ref", "dub", "dub2"):
        stand.site.episode(slug, 1)
    await stand.configure(["AniStar", "AnimeVost"])
    series_id = await stand.series("Сериал", ["ref", "dub", "dub2"])
    view = await stand.sync(series_id)
    await stand.check(series_id)
    # The service was asked once: for the voice-over of the next source.
    sent = [dub for job in stand.daemon.jobs.values() for dub in job.get("dubs") or []]
    assert len(sent) == 1, sent
    ((failure,), (asset,)) = view["failures"], view["assets"]
    (track,) = asset["tracks"]
    assert failure["kind"] == "download" and failure["covered"] and track["group"] == "AnimeVost", view
    assert track["row"] != failure["row"], (track, failure)


async def test_the_voice_overs_of_an_episode_go_to_the_service_as_one_job_and_one_that_did_not_come_holds_none_back(stand):
    """The reference of an episode is worked out once for all its voice-overs: they go to the service as one job, once
    none of them is still being downloaded; a voice-over whose file the site lost is not waited for."""
    stand.site.release("ref", "AniStar")
    stand.site.episode("ref", 1)
    voices = {"dub": "AnimeVost", "d2": "Kansai", "d3": "JAM"}
    for slug, voice in voices.items():
        stand.site.release(slug, voice)
        stand.site.episode(slug, 1, height=480)
    stand.site.gone("d3", 1)
    await stand.configure(["AniStar", *voices.values()])
    series_id = await stand.series("Сериал", ["ref", *voices])
    await stand.sync(series_id)
    (job,) = stand.daemon.jobs.values()
    assert len(job["dubs"]) == 2, job["dubs"]
    view = await stand.check(series_id)
    (asset,) = view["assets"]
    assert sorted(track["group"] for track in asset["tracks"]) == ["AnimeVost", "Kansai"]


def programs_with(name: str) -> list[str]:
    """The programs running now whose command line names `name` (a download caught in its middle)."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as file:
                line = file.read().replace(b"\0", b" ").decode(errors="replace")
        except OSError:
            continue
        if name in line:
            found.append(line)
    return found


def files_in(folder: str) -> list[str]:
    return [os.path.join(path, name) for path, _, names in os.walk(folder) for name in names]


async def nothing_taken_up_after_a_start(stand, series_id: int) -> None:
    """The stopped sync is not taken up again: after a start of the process nothing of it runs, nothing is fetched."""
    served, jobs = len(stand.host.served), len(stand.daemon.jobs)
    await stand.restart()
    await stand.bus.settled()
    view = await stand.call("GET", f"/api/series/{series_id}/plan")
    assert not view["running"] and not view["assets"]
    assert (len(stand.host.served), len(stand.daemon.jobs)) == (served, jobs)


async def test_a_sync_stopped_while_it_downloads_stops_the_download_with_its_program_and_applies_nothing(stand):
    """The user stops the sync while the video of the reference is half downloaded: the download stops with its program
    and what it wrote goes, the run closes, nothing is applied, and the sync is not taken up again after a start."""
    series_id = await series_of(stand, (1,))
    held = f"ref-ep1-truth{truth_of(1, 1)}-file.mp4"
    stand.host.hold.add(held)
    await stand.call("POST", f"/api/series/{series_id}/scan")
    await asyncio.wait_for(asyncio.to_thread(stand.host.holding.wait), STAND_LIMIT_S)
    stopped = await stand.call("POST", f"/api/series/{series_id}/scan/cancel")
    assert stopped == {"success": True, "cancelled": True}
    assert programs_with(stand.host.url(held)) == []  # the address of this stand: a neighbour's test has its own
    view = await stand.call("GET", f"/api/series/{series_id}/plan")
    assert not view["running"] and not view["assets"] and stand.files() == []
    assert files_in(os.path.join(stand.root, "work")) == []
    series = await stand.call("GET", f"/api/series/{series_id}")
    assert not series["is_busy"]
    stand.host.hold.clear()
    stand.host.released.set()
    await nothing_taken_up_after_a_start(stand, series_id)


async def test_a_sync_stopped_while_the_service_aligns_cancels_its_job_and_applies_nothing(stand):
    """The user stops the sync while the service aligns an episode: the job is cancelled and leaves the service's
    history, its folder goes, nothing is applied, and the sync is not taken up again after a start."""
    series_id = await series_of(stand, (1,))
    stand.daemon.hold = True
    await stand.call("POST", f"/api/series/{series_id}/scan")
    await asyncio.wait_for(asyncio.to_thread(stand.daemon.made.wait), STAND_LIMIT_S)
    stopped = await stand.call("POST", f"/api/series/{series_id}/scan/cancel")
    assert stopped == {"success": True, "cancelled": True}
    await stand.bus.settled()
    assert stand.daemon.jobs == {}
    view = await stand.call("GET", f"/api/series/{series_id}/plan")
    assert not view["running"] and not view["assets"] and stand.files() == []
    assert files_in(os.path.join(stand.root, "work")) == []
    stand.daemon.hold = False
    await nothing_taken_up_after_a_start(stand, series_id)


async def test_a_card_whose_parser_gives_a_video_at_an_address_without_an_extension_is_measured_downloaded_and_built(stand):
    """A parser of an application gives the address of an MP4 with no extension: the start of what its host serves tells
    a file from a playlist, its frame is the one ffprobe reads of it, and the download takes it as the file it is."""
    stand.site.app_card(219017, "万界独尊", {1: 1080}, files=True)
    await stand.configure(["Оригинал"], {"Оригинал": "chi"})
    made = await stand.call("POST", "/api/parser-profiles", {"name": "серии 半日"})
    label = json.dumps([{"type": "text", "value": "第"}, {"type": "number"}, {"type": "text", "value": "集"}],
                       ensure_ascii=False)
    await stand.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
        "name": "серия", "continue_after_match": False,
        "action_pattern": json.dumps([{"action_type": "extract_single", "action_pattern": label},
                                      {"action_type": "assign_voiceover", "action_pattern": "Оригинал"}]),
        "conditions": [{"condition_type": "contains", "pattern": label, "logical_operator": "AND"}]})
    series = await stand.call("POST", "/api/series", {
        "name": "Сериал", "name_en": "Serial", "site": "banri", "save_path": stand.library,
        "sources": [{"url": "app://banri/219017", "source_type": "web_video", "parser_profile_id": made["id"]}]})
    await stand.sync(series["series_id"])
    view = await stand.check(series["series_id"])
    (asset,) = view["assets"]
    assert "1080p" in asset["name"], asset["name"]
    held = await mkv.contents(os.path.join(stand.library, asset["name"]))
    assert [track.language for track in held.audio] == ["chi"]


async def test_a_title_whose_voice_over_plays_in_alloha_is_downloaded_by_its_player_and_built(stand):
    """The player of Alloha takes its episodes itself: each is opened as the player opens it, plays (the socket gives the
    access to the CDN), and its segments come from the HLS of the host into the file of the episode at its own height,
    then built as any other."""
    url = stand.site.alloha_title("bratik", 700, "Братик", "AniStar", {1: 720, 2: 480})
    await stand.configure(["AniStar"])
    made = await stand.call("POST", "/api/parser-profiles", {"name": "серии Alloha"})
    label = json.dumps([{"type": "number"}, {"type": "text", "value": " серия"}], ensure_ascii=False)
    await stand.call("POST", f"/api/parser-profiles/{made['id']}/rules", {
        "name": "серия", "continue_after_match": False,
        "action_pattern": json.dumps([{"action_type": "extract_single", "action_pattern": label},
                                      {"action_type": "assign_voiceover", "action_pattern": "AniStar"}]),
        "conditions": [{"condition_type": "contains", "pattern": label, "logical_operator": "AND"}]})
    series = await stand.call("POST", "/api/series", {
        "name": "Братик", "name_en": "Bratik", "site": "yummyani", "save_path": stand.library,
        "sources": [{"url": url, "source_type": "web_video", "parser_profile_id": made["id"]}]})

    await stand.sync(series["series_id"])
    view = await stand.check(series["series_id"])

    assert sorted(("720p" in each["name"], "480p" in each["name"]) for each in view["assets"]) == [(False, True),
                                                                                                    (True, False)]
    model = stand.site.alloha
    # Each episode was its own session: it said it plays, and every request of the CDN carried the access it was given.
    assert len(model.sockets) == 2 and all(each.said[:2] == ["playback_start", "resumed"] for each in model.sockets)
    assert {each["Accepts-Controls"] for each in model.media} == {"E1"} and model.closed == 2
