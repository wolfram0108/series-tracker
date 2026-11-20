"""Sources that lose what they gave, in a browser (tests/ui_stand.py): a row, a part of the list, the whole page, the source
itself, a compilation; what the files keep, what is made anew and what the window says. The work of the service is counted
by its models: the files the site gave out (`host.served`) and the jobs of the alignment service (`daemon.jobs`)."""
import pytest
from playwright.async_api import expect

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
