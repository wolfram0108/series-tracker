"""Sources that lose what they gave, in a browser (tests/ui_stand.py): a row, a part of the list, the whole page, the source
itself, a compilation; what the files keep, what is made anew and what the window says. The work of the service is counted
by its models: the files the site gave out (`host.served`) and the jobs of the alignment service (`daemon.jobs`)."""
import os
import re

import pytest
from playwright.async_api import expect

from core import container as mkv
from ui_stand import state, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui


def made(ui) -> tuple[int, int]:
    """What the service has asked of the world so far: files downloaded, jobs of the alignment service."""
    return len(ui.host.served), len(ui.daemon.jobs)


async def synced(ui, slugs: list[str], episodes, *, also=()) -> int:
    for slug, voice, height in (("ref", "AniStar", 720), ("dub", "AnimeVost", 480)):
        if slug in slugs:
            ui.site.release(slug, voice)
            for number in episodes:
                ui.site.episode(slug, number, height=height)
    series_id = await ui.make_series(slugs, also=also)
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(len(episodes))
    return series_id


def row(ui, episode: int, group: str):
    return ui.page.locator(f".episode-detail[data-episode='{episode}'] tr[data-group='{group}']")


async def opened(ui, episode: int) -> None:
    detail = ui.page.locator(f".episode-detail[data-episode='{episode}']")
    if not await detail.count():
        await ui.page.locator(f".episode-card[data-episode='{episode}']").dblclick()
    await detail.wait_for()


async def test_the_source_forgot_the_reference_the_file_stays_and_its_video_is_marked_offline(ui):
    series_id = await synced(ui, ["ref", "dub"], (1, 2))
    files, before = ui.files(), made(ui)
    ui.site.drop("ref", 1)
    ui.later()
    await ui.press_sync(series_id)
    assert ui.files() == files and made(ui) == before  # nothing goes, nothing is made
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    card = ui.page.locator(".episode-card[data-episode='1']")
    # The corner of presence: grey on the mark of the reference and on the cell of its track, green on the voice-over;
    # the marks of the legend stand for sources, not rows of a file, and carry none.
    await expect(card.locator(".card-ref-shown .figure-mark.presence.is-offline")).to_have_count(1)
    await expect(card.locator(".track-cell.is-reference.presence.is-offline")).to_have_count(1)
    await expect(card.locator(".track-cell:not(.is-reference).presence.is-online")).to_have_count(1)
    await expect(ui.page.locator(".episode-card[data-episode='2'] .presence.is-offline")).to_have_count(0)
    await expect(ui.page.locator(".output-legend .presence")).to_have_count(0)
    await opened(ui, 1)
    await expect(row(ui, 1, "AniStar").locator(".track-offline")).to_have_text(text("output.plan.offline"))
    await expect(row(ui, 1, "AniStar").locator(".figure-mark.presence.is-offline")).to_have_count(1)
    await expect(row(ui, 1, "AnimeVost").locator(".track-offline")).to_have_count(0)
    await expect(row(ui, 1, "AnimeVost").locator(".figure-mark.presence.is-online")).to_have_count(1)
    await ui.shot("s1-reference-offline")
    await ui.check(series_id)


async def test_a_source_that_cut_its_list_short_leaves_the_files_whole_and_marks_what_it_forgot(ui):
    series_id = await synced(ui, ["ref", "dub"], (1, 2, 3, 4))
    files, before = ui.files(), made(ui)
    for number in (3, 4):
        ui.site.drop("dub", number)
    ui.later()
    await ui.press_sync(series_id)
    assert ui.files() == files and made(ui) == before
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(4)
    for episode, offline in ((2, 0), (3, 1), (4, 1)):
        await expect(ui.page.locator(f".episode-card[data-episode='{episode}'] .track-cell.presence.is-offline")).to_have_count(offline)
        await opened(ui, episode)
        await expect(row(ui, episode, "AnimeVost").locator(".pill")).to_have_text(state("done"))
        await expect(row(ui, episode, "AnimeVost").locator(".track-offline")).to_have_count(offline)
    await ui.shot("s2-list-cut-short")
    view = await ui.check(series_id)
    assert all([track["group"] for track in asset["tracks"]] == ["AnimeVost"] for asset in view["assets"])


async def test_a_page_that_does_not_open_changes_nothing(ui):
    series_id = await synced(ui, ["ref", "dub"], (1, 2))
    files, before = ui.files(), made(ui)
    ui.site.releases["dub"].down = True
    ui.later()
    await ui.press_sync(series_id)
    assert ui.files() == files and made(ui) == before
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await opened(ui, 1)
    await ui.shot("s3-page-down")
    await expect(row(ui, 1, "AnimeVost").locator(".pill")).to_have_text(state("done"))
    await ui.check(series_id)


async def test_the_source_of_the_reference_taken_out_keeps_the_file_until_the_user_takes_another_video(ui):
    """The source of the reference no longer works and the user takes it out of the series: the file keeps its video
    (offline: no source gives it); its button is «Удалить» (nothing can come again from no source): the plan takes the video
    of the other source, and the voice-over of the old video is aligned out of the old file onto the new one."""
    series_id = await synced(ui, ["ref", "dub"], (1,))
    await ui.remove_source("ref")
    await ui.tab(text("output.tab"))
    await expect(ui.page.locator(".output-legend .output-source")).to_have_count(1)  # the source is gone from the window
    before = made(ui)
    await ui.press_sync(series_id)
    assert made(ui) == before
    await opened(ui, 1)
    await expect(row(ui, 1, "AniStar").locator(".track-offline")).to_have_text(text("output.plan.offline"))
    await ui.shot("s4a-source-taken-out")
    await expect(row(ui, 1, "AniStar").locator(".row-action")).to_have_text(text("output.action.delete"))
    await row(ui, 1, "AniStar").locator(".row-action.do-delete").click()
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(1)
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    assert asset["video"]["group"] == "AnimeVost" and asset["video"]["height"] == 480
    assert [track["group"] for track in asset["tracks"]] == ["AniStar"] and asset["tracks"][0]["online"] is False
    await ui.shot("s4b-other-video")


async def test_a_compilation_gone_from_its_source_keeps_its_cuts_and_new_voice_overs_are_laid_on_them(ui):
    """Episodes cut out of a compilation; the compilation goes from its source and a voice-over comes out on another
    site: it is aligned onto the cut videos the files hold, nothing is cut or downloaded again."""
    ui.site.release("ref", "AniStar")
    for number in (1, 2, 3):
        ui.site.episode("ref", number, height=720)
    ui.site.compilation("ref", 1, 3, height=2160)
    series_id = await ui.make_series(["ref"], also=("AnimeVost",))
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(3)
    ui.site.drop_compilation("ref", 1)
    ui.site.release("dub", "AnimeVost")
    for number in (1, 2, 3):
        ui.site.episode("dub", number, height=480)
    ui.later()
    await ui.add_source("dub")
    await ui.tab(text("output.tab"))
    cuts = len([job for job in ui.daemon.jobs.values() if job["kind"] == "reverse"])
    served = len(ui.host.served)
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(3)
    assert len([job for job in ui.daemon.jobs.values() if job["kind"] == "reverse"]) == cuts  # nothing cut again
    assert all(name.startswith("dub-") for name in ui.host.served[served:])  # only the new voice-overs downloaded
    view = await ui.check(series_id)
    assert all(asset["video"]["height"] == 2160 and [track["group"] for track in asset["tracks"]] == ["AnimeVost"]
               for asset in view["assets"])
    await opened(ui, 1)
    await expect(row(ui, 1, "AniStar").locator(".track-offline")).to_have_text(text("output.plan.offline"))
    await ui.shot("s5-compilation-gone-new-voice")


async def test_a_voice_over_that_was_only_in_a_gone_compilation_is_aligned_anew_onto_the_replacement(ui):
    """Episodes cut out of a compilation whose own voice-over (Kansai) no other source has; the source of the compilation
    is taken out, and a better video comes from another source: the new reference takes the place, and the voice-over of
    the compilation is aligned out of the old file onto it."""
    ui.site.release("ref", "AniStar")
    for number in (1, 2):
        ui.site.episode("ref", number, height=720)
    ui.site.release("comp", "Kansai")
    ui.site.compilation("comp", 1, 2, height=1080)
    series_id = await ui.make_series(["ref", "comp"], also=("AnimeVost",))
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    view = await ui.check(series_id)
    assert all(asset["video"]["cut_of"] and asset["video"]["group"] == "Kansai" for asset in view["assets"])
    await ui.remove_source("comp")
    ui.site.release("hd", "AnimeVost")
    for number in (1, 2):
        ui.site.episode("hd", number, height=2160)
    await ui.add_source("hd")
    await ui.tab(text("output.tab"))
    await ui.press_sync(series_id)
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    view = await ui.check(series_id)
    for asset in view["assets"]:
        assert asset["video"]["height"] == 2160 and asset["video"]["group"] == "AnimeVost"
        assert {track["group"]: track["online"] for track in asset["tracks"]} == {"AniStar": True, "Kansai": False}
    await opened(ui, 1)
    await expect(row(ui, 1, "Kansai").locator(".track-offline")).to_have_text(text("output.plan.offline"))
    await ui.shot("s6-compilation-voice-on-new-reference")


async def test_a_voice_over_its_source_forgot_is_deleted_by_its_button_with_no_alignment(ui):
    """A voice-over its source forgot is offline: its button is «Удалить», and the sync takes it out of the file — no job
    of the service, nothing downloaded (Д-1)."""
    series_id = await synced(ui, ["ref", "dub"], (1, 2))
    ui.site.drop("dub", 1)
    ui.later()
    await ui.press_sync(series_id)
    await opened(ui, 1)
    await expect(row(ui, 1, "AnimeVost").locator(".track-offline")).to_have_count(1)
    await expect(row(ui, 1, "AnimeVost").locator(".row-action")).to_have_text(text("output.action.delete"))
    await row(ui, 1, "AnimeVost").locator(".row-action.do-delete").click()
    jobs, served = len(ui.daemon.jobs), len(ui.host.served)
    await ui.press_sync(series_id)
    assert len(ui.daemon.jobs) == jobs and len(ui.host.served) == served  # removed: nothing aligned, nothing downloaded
    view = await ui.check(series_id)
    assert next(each for each in view["assets"] if each["episode"] == [1, 1])["tracks"] == []
    await ui.shot("s7-deleted-offline-voice-over")


async def test_a_player_that_stopped_giving_the_video_changes_nothing_in_the_files(ui):
    series_id = await synced(ui, ["ref", "dub"], (1, 2))
    files, before = ui.files(), made(ui)
    ui.site.releases["dub"].silent.add(1)
    ui.later()
    await ui.press_sync(series_id)
    assert ui.files() == files and made(ui) == before
    await expect(ui.page.locator(".episode-card[data-state='done']")).to_have_count(2)
    await opened(ui, 1)
    await ui.shot("s8-player-silent")
    await ui.check(series_id)


async def test_a_row_the_source_forgot_and_gave_again_is_online_again_with_no_work(ui):
    series_id = await synced(ui, ["ref", "dub"], (1, 2))
    files, before = ui.files(), made(ui)
    ui.site.drop("dub", 1)
    ui.later()
    await ui.press_sync(series_id)
    await opened(ui, 1)
    await expect(row(ui, 1, "AnimeVost").locator(".track-offline")).to_have_count(1)
    ui.site.episode("dub", 1, height=480)
    ui.later()
    await ui.press_sync(series_id)
    await expect(row(ui, 1, "AnimeVost").locator(".track-offline")).to_have_count(0)
    assert ui.files() == files and made(ui) == before
    await ui.check(series_id)


async def test_a_voice_over_only_its_file_holds_is_moved_in_the_order_of_the_season_with_no_refusal(ui):
    """The source of a voice-over is taken out: its track stays in the file and in the order of the season, where the user
    moves it like any other with no refusal; the next sync puts the sounds of the file in that order, the voice-over of
    the reference among them."""
    series_id = await synced(ui, ["ref", "dub"], (1,))
    await ui.remove_source("dub")
    await ui.tab(text("output.tab"))
    items = ui.page.locator(".voice-order-item")
    labels = items.locator("span:nth-child(2)")
    await expect(labels).to_have_text(["AniStar", "AnimeVost"])
    marks = items.locator(".figure-mark")
    await expect(marks.nth(0)).to_have_class(re.compile(r"\bis-online\b"))
    await expect(marks.nth(1)).to_have_class(re.compile(r"\bis-offline\b"))
    await ui.shot("s10-held-voice-offline")
    await ui.drag(items.nth(1), items.nth(0))
    await expect(labels).to_have_text(["AnimeVost", "AniStar"])
    await expect(ui.page.locator(".output-failure")).to_have_count(0)
    await ui.press_sync(series_id)
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert [track.label for track in held.audio] == ["AnimeVost", "AniStar"]
    assert held.audio[0].default
    await ui.shot("s10-held-voice-moved")
