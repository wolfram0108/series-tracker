"""A card of a video application (tests/app99_model.py) as the source of the reference, in a browser (tests/ui_stand.py):
the user finds the title by the search of the application in the window of a new series, adds the card it gave, makes the
rule of its labels («第N集») beside a source of a voice-over, and syncs. The read of the card asks no parser of the
application (a parser allows a limited number of parses a day), so the frame of its line is not known before a download
and the reference goes by the frame the other source states. The user puts the application first in the queue of the
reference of the episode: the next sync parses the stream of the episode right before its download, and the file becomes
the picture of the application."""
import json
import os
import subprocess

import pytest
from playwright.async_api import expect

from core import container as mkv

from ui_stand import SERIES, SERIES_EN, _pick, lines_of, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui

LABEL = [{"type": "text", "value": "第"}, {"type": "number"}, {"type": "text", "value": "集"}]
ORIGINAL = "Оригинал"


def frame_height(path: str) -> int:
    probed = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of",
                             "json", path], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(probed.stdout)["streams"][0]["height"]


async def test_a_card_found_by_the_search_of_an_application_is_the_reference_its_streams_parsed_only_to_download(ui):
    ui.site.app_card(219017, "万界独尊", {1: 1080})
    ui.site.release("dub", "AnimeVost")
    ui.site.episode("dub", 1, height=480)
    await ui.first_run()
    await ui.set_up([ORIGINAL, "AnimeVost"], languages={ORIGINAL: "chi", "AnimeVost": "rus"})
    await ui.profile("серии 半日", [{"name": "серия", "if": LABEL,
                                    "then": [("extract_single", LABEL), ("assign_voiceover", ORIGINAL)]}])
    await ui.profile("серии AnimeVost", lines_of("AnimeVost"))

    page = ui.page
    await page.get_by_role("button", name="Добавить сериал").click()
    await page.locator(".add-url-row .source-mode .st-tab", has_text=text("sources.mode.apps")).click()
    await page.get_by_label(text("sources.apps.query")).fill("万界独尊")
    await page.locator(".app-find").click()
    found = page.locator(".app-card[data-card='219017']")
    await expect(found).to_contain_text("更新至第1集")
    await ui.shot("a1-search")
    await found.locator(".app-add").click()

    source = page.locator(".source-card").first
    await expect(source.locator(".source-place")).to_have_text(text("sources.apps.cardValue", app="半日", card="219017"))
    await _pick(page, source.locator(".item-select").first, "серии 半日")
    await expect(source.locator(".source-state")).to_have_text(text("sources.state.ready"), timeout=60_000)
    await expect(source.locator(".player-tab")).to_have_text(["蓝光1"])
    # The search stays at hand in the sources: the card already added is marked by its place.
    sources = page.locator(".sources")
    await sources.locator(".source-mode .st-tab", has_text=text("sources.mode.apps")).click()
    await sources.locator(".app-find").click()
    await expect(sources.locator(".app-card[data-card='219017'] .app-added")).to_have_text(text("sources.apps.added", n=1))
    await ui.shot("a2-added")
    await sources.locator(".source-mode .st-tab", has_text=text("sources.mode.address")).click()
    await page.get_by_label(text("sources.add.label")).fill(ui.site.releases["dub"].url)
    await page.locator(".source-add button.btn-add", has_text=text("sources.add.button")).click()
    dub = page.locator(".source-card").nth(1)
    await _pick(page, dub.locator(".item-select").first, "серии AnimeVost")
    await expect(dub.locator(".source-state")).to_have_text(text("sources.state.ready"), timeout=60_000)
    await page.get_by_label("Название (RU)").fill(SERIES)
    await page.get_by_label("Название (EN)").fill(SERIES_EN)
    await page.get_by_label("Путь сохранения").fill(ui.library)
    await page.locator(".modern-footer").get_by_role("button", name="Добавить", exact=True).click()
    await page.locator(".series-card", has_text=SERIES).first.wait_for()
    listed = await ui.call("GET", "/api/series")
    series_id = next(each["id"] for each in listed if each["name"] == SERIES)
    assert "/app/vodParser" not in ui.site.app.paths(), "reading the card asks no parser"

    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    await ui.shot("a3-first-sync")
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    assert frame_height(os.path.join(ui.library, asset["name"])) != 1080, "a frame not known does not beat a stated one"
    # The original sound is the application's: its stream was parsed for that download, once.
    assert [fields["url"] for path, fields in ui.site.app.calls if path == "/app/vodParser"] == ["tok-219017-1"]

    await page.locator(".episode-card").first.click()
    detail = page.locator(".episode-detail")
    await detail.locator(".st-tab", has_text=text("output.tab.reference")).click()
    rows = detail.locator(".queue-table .variant-row")
    await expect(rows).to_have_count(2)
    groups = rows.locator("td:nth-child(6)")
    await expect(groups).to_have_text(["AnimeVost", ORIGINAL])
    # The order is the core's once it answered: the queue it gives again is the end of the wait.
    async with page.expect_response(lambda response: "/output/queues" in response.url) as reread:
        await ui.drag(rows.nth(1).locator(".order-handle"), rows.nth(0))
    await reread.value
    await expect(groups).to_have_text([ORIGINAL, "AnimeVost"])
    await ui.shot("a4-reference-order")
    ui.later()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    assert frame_height(os.path.join(ui.library, asset["name"])) == 1080
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert {track.label: track.language for track in held.audio} == {ORIGINAL: "chi", "AnimeVost": "rus"}
    # One parse for each download, right before it (the address of a stream expires): the sound, then the picture.
    parsed = [fields["url"] for path, fields in ui.site.app.calls if path == "/app/vodParser"]
    assert parsed == ["tok-219017-1", "tok-219017-1"]
