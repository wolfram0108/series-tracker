"""The chips of the tracks of a season (components/status/VoiceOrder.vue) on the page of the samples, in a browser (tests/ui_stand.py): at
every width a chip is one line within its room, a long name is clipped before the numbers, and a voice-over holds its episodes as
`from–to` — where it has gaps the rest is in a card by hover and by focus, and a whole one has no card."""
from __future__ import annotations

import pytest
from playwright.async_api import expect

from ui_stand import ui  # noqa: F401 - the fixture

pytestmark = [pytest.mark.ui, pytest.mark.asyncio]

# The widths of the window the chips are walked at, in steps of 10 px: from a phone up.
WIDTHS = range(320, 1290, 10)

# What a chip may not do: stand out of its room, break into lines, let its numbers run out of it, or hold a list of runs.
DETECT = """() => {
  const problems = [];
  for (const room of document.querySelectorAll('.voice-samples .fold-demo-room')) {
    const edge = room.getBoundingClientRect();
    for (const chip of room.querySelectorAll('.voice-order-item')) {
      const box = chip.getBoundingClientRect(), name = chip.textContent.trim().slice(0, 30);
      if (box.right > edge.right + 1 || box.left < edge.left - 1) problems.push('the chip is out of its room: ' + name);
      const centres = [...chip.querySelectorAll('.figure-mark, .voice-order-name, .voice-order-episodes')]
        .map((each) => { const part = each.getBoundingClientRect(); return (part.top + part.bottom) / 2; });
      if (Math.max(...centres) - Math.min(...centres) > 4) problems.push('the chip is in more than one line: ' + name);
      const reach = chip.querySelector('.voice-order-episodes');
      if (reach.getBoundingClientRect().right > box.right + 1) problems.push('the numbers run out of the chip: ' + name);
      if (reach.textContent.includes(',')) problems.push('the chip holds a list of runs: ' + name);
    }
  }
  return problems;
}"""


async def _samples(ui):  # noqa: F811
    page = ui.page
    await ui.first_run()
    await page.set_viewport_size({"width": 1280, "height": 1000})
    await page.goto("/#gallery")
    await page.reload()  # the page of the samples is chosen by the anchor when the application starts
    await expect(page.locator(".voice-samples .voice-order-item").first).to_be_visible()
    return page


async def test_a_chip_of_a_track_is_one_line_within_its_room_at_every_width(ui):  # noqa: F811
    page = await _samples(ui)
    await ui.shot_of(page.locator(".voice-samples"), "57-voice-chips")
    for width in WIDTHS:
        await page.set_viewport_size({"width": width, "height": 1000})
        found = await page.evaluate(DETECT)
        assert found == [], (width, found)


async def test_the_gaps_of_a_voice_over_are_in_a_card_by_hover_and_by_focus_and_a_whole_one_has_none(ui):  # noqa: F811
    page = await _samples(ui)
    room = page.locator(".voice-samples .fold-demo-room").nth(2)  # the widest: every chip is whole in it
    gapped = room.locator(".voice-order-episodes.has-more")
    await expect(gapped).to_have_count(3)  # ruchime, PVA and the long-named one: those with gaps
    await expect(room.locator(".voice-order-episodes:not(.has-more)")).to_have_text(["509", "0–656"])
    await expect(page.locator(".hover-note")).to_have_count(0)

    await gapped.first.hover()
    card = page.locator(".hover-note")
    await expect(card).to_be_visible()
    await expect(card).to_contain_text("1–154, 158–162, 165–168, 170–174, 176–656")
    await expect(card).to_contain_text("155–157, 163–164, 169, 175")
    await ui.shot_around(page.locator(".voice-samples"), "58-voice-chips-card", below=120)  # the card is in the body, not in the section
    await page.mouse.move(2, 2)
    await expect(card).to_have_count(0)

    await gapped.nth(1).focus()
    await expect(card).to_be_visible()
    await expect(card).to_contain_text("526")  # the one gap of 1–525, 527–656
    await page.keyboard.press("Tab")
    await expect(card).to_have_count(0)

    # The name is clipped, the whole of it is in its hint; the numbers of the chip are whole.
    long_name = room.locator(".voice-order-name").last
    assert await long_name.evaluate("el => el.scrollWidth > el.clientWidth")
    assert (await long_name.get_attribute("title")).startswith("Dub with a very long name the user gave to the track in the containers")
