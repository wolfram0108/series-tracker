"""A card of a video application (tests/app99_model.py) as the source of the reference, in a browser (tests/ui_stand.py):
the user finds the title by the search of the application in the window of a new series, adds the card it gave, makes the
rule of its labels («第N集») beside a source of a voice-over, and syncs. Every episode of the card is measured in its read,
as every row of every source is: its stream asked of the parser of its line, its frame read from the picture. The frame
of the application beats the one the voice-over states, so the file is the picture of the application; its stream is
asked again right before each download (the address of a stream expires).
"""
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
# Whether a card of the search keeps its parts apart and inside it at a width of the window: its action never over its
# words, no part out of its frame, the page never scrolled sideways.
CARD_LAYOUT = """() => {
  const bad = [];
  const box = (el) => el.getBoundingClientRect();
  const cross = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
  document.querySelectorAll('.app-card').forEach((card) => {
    const frame = box(card), words = card.querySelector('.app-body'), act = card.querySelector('.app-act');
    if (cross(box(words), box(act))) bad.push('action over words');
    card.querySelectorAll('*').forEach((el) => {
      const part = box(el);
      if (part.width && (part.right > frame.right + 1 || part.left < frame.left - 1)) bad.push(`${el.className} out`);
    });
  });
  if (document.documentElement.scrollWidth > window.innerWidth) bad.push('page scrolls sideways');
  return [...new Set(bad)];
}"""


def frame_height(path: str) -> int:
    probed = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height", "-of",
                             "json", path], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(probed.stdout)["streams"][0]["height"]


async def test_a_card_found_by_the_search_of_an_application_is_the_reference_by_the_frame_measured_in_its_read(ui):
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
    size = page.viewport_size
    faults = {}
    for width in range(360, 1450, 10):
        await page.set_viewport_size({"width": width, "height": size["height"]})
        if found_here := await page.evaluate(CARD_LAYOUT):
            faults[width] = found_here
    await page.set_viewport_size(size)
    assert not faults, faults
    await found.locator(".app-add").click()

    source = page.locator(".source-card").first
    # The head names the source as its site does (the application and the title); its field is its address, the one writing
    # a field of an address takes.
    await expect(source.locator(".source-place")).to_have_text("半日 · 万界独尊 (2021)")
    await expect(source.locator(".source-app-card input")).to_have_value("app://banri/219017")
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
    # The read of the card measured its episode: one parse of its stream, its frame in the table.
    assert [fields["url"] for path, fields in ui.site.app.calls if path == "/app/vodParser"] == ["tok-219017-1"]

    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card.is-ready")).to_have_count(1)
    await ui.shot("a3-synced")
    view = await ui.check(series_id)
    (asset,) = view["assets"]
    assert frame_height(os.path.join(ui.library, asset["name"])) == 1080, "the measured frame of the application wins"
    held = await mkv.contents(os.path.join(ui.library, asset["name"]))
    assert {track.label: track.language for track in held.audio} == {ORIGINAL: "chi", "AnimeVost": "rus"}
    # Each download asks its stream right before it: the measure of the read, then the downloads of the sync.
    parsed = [fields["url"] for path, fields in ui.site.app.calls if path == "/app/vodParser"]
    assert parsed[0] == "tok-219017-1" and len(parsed) >= 2 and set(parsed) == {"tok-219017-1"}
