"""A rule «Таблица» made in a browser (tests/ui_stand.py): the user reads the page of each source in the test of the
profile, fills the table of voice-overs by the quick template «V:», adds the rule of the episodes, and syncs a series
whose two sources share the profile. Every file holds both voice-overs, each named by its row of the table."""
import os
import re

import pytest
from playwright.async_api import expect

from core import container as mkv

from ui_stand import SERIES, SERIES_EN, SINGLE, _pick, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui

PROFILE = "серии и озвучки"
VOICES = {"dub": ("AnimeVost", 720), "alt": ("AniStar", 480)}


async def test_a_table_filled_from_the_pages_by_a_template_names_the_voice_overs_of_both_sources(ui):
    for slug, (voice, height) in VOICES.items():
        ui.site.release(slug, voice)
        for episode in (1, 2):
            ui.site.episode(slug, episode, height=height)
    await ui.first_run()
    await ui.set_up([voice for voice, _height in VOICES.values()])

    page = ui.page
    await ui.settings("Правила")
    if not await page.get_by_label(text("rules.profile.new")).is_visible():
        await page.locator("button.accordion-button").first.click()
    await page.get_by_label(text("rules.profile.new")).fill(PROFILE)
    await page.locator("button.btn-add[title='Создать']").click()
    rules_step = page.get_by_text(text("rules.step.rules", profile=PROFILE))
    await rules_step.wait_for()

    await page.get_by_role("button", name=text("rules.table.add")).click()
    card = page.locator(".rule-table-card")
    await card.locator(".rule-name-input").fill("Озвучки")
    # A new table starts as a new rule does: «ТО» excludes and «продолжать» is off.
    await expect(card.locator(".col-then .selected-value")).to_have_text(text("rules.action.exclude"))
    await expect(card.locator(".p-toggleswitch")).not_to_have_class(re.compile(r"p-toggleswitch-checked"))
    await _pick(page, card.locator(".col-then .item-select"), text("rules.action.assign_voiceover"))
    for slug in VOICES:
        await page.get_by_text(text("rules.step.test")).click()
        await page.get_by_label(text("rulesTest.field.address")).fill(ui.site.releases[slug].url)
        await page.get_by_role("button", name=text("rulesTest.fetch.page")).click()
        await expect(page.locator(".parser-test-textarea")).to_have_value(re.compile(r"V:"), timeout=60_000)
        await rules_step.click()
        await card.locator(".template-chip", has_text="V:").click()
    await expect(card.locator("tbody td.col-if")).to_have_count(2)
    for at, voice in enumerate(["AnimeVost", "AniStar"]):
        await expect(card.locator("input.table-value").nth(at)).to_have_value(voice)
    # The dots of the handle and the cross of the removal of a row stand in the middle of their cells.
    for mark in ("td.row-handle svg", "td.col-remove .row-remove"):
        for shift in await card.locator(mark).evaluate_all(
                "marks => marks.map((mark) => [mark.closest('td').getBoundingClientRect(), mark.getBoundingClientRect()])"
                ".map(([c, d]) => [c.x + c.width / 2 - d.x - d.width / 2, c.y + c.height / 2 - d.y - d.height / 2])"):
            assert all(abs(each) <= 1 for each in shift), (mark, shift)
    await ui.shot("t1-table-of-voice-overs")
    # On a phone the table keeps its columns and scrolls in its own box: the window does not, the choice of the action
    # stays whole (it once fell apart into a letter a line).
    await page.set_viewport_size({"width": 390, "height": 900})
    assert await page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
    assert (await card.locator(".col-then .item-select").bounding_box())["height"] < 60
    assert await card.locator(".table-scroll").evaluate("box => box.scrollWidth > box.clientWidth")
    await ui.shot("t1-table-on-a-phone")
    await page.set_viewport_size({"width": 1440, "height": 1000})
    # The table lets the rule of the episodes after it work; the switch saves it, as it does an ordinary rule.
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
            page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
        await card.locator(".p-toggleswitch").click()
    assert (await saved.value).ok, "the table was not saved"
    await ui._rule({"name": "серия", "if": SINGLE, "then": [("extract_single", SINGLE)]})
    await ui.close_settings()

    series_id = await ui.add_series(SERIES, SERIES_EN, [(ui.site.releases[slug].url, PROFILE) for slug in VOICES])
    await ui.open_output()
    await ui.press_sync(series_id)
    await expect(page.locator(".episode-card[data-state='done']")).to_have_count(2)
    view = await ui.check(series_id)
    for asset in view["assets"]:
        held = await mkv.contents(os.path.join(ui.library, asset["name"]))
        assert sorted(track.label for track in held.audio) == ["AniStar", "AnimeVost"]


async def _season_of_the_first_line(page) -> list[str]:
    async with page.expect_response(lambda r: r.url.endswith("/api/parser-profiles/test")):
        await page.get_by_role("button", name=text("rulesTest.run")).click()
    await expect(page.locator(".card-test-result").first).to_be_visible()
    return [each.strip() for each in await page.locator(".card-test-result").first.locator(".pill").all_inner_texts()]


async def test_a_season_assigned_by_a_table_and_by_a_rule_is_saved_as_typed_and_given_to_the_lines(ui):
    """The number of a season is typed into a field of numbers: the table is saved with it (it once was not saved at
    all, in silence), and a rule that assigns it gives the season where the rules before it let the line pass on."""
    ui.site.release("dub", "AnimeVost")
    ui.site.episode("dub", 1, height=720)
    await ui.first_run()
    await ui.set_up(["AnimeVost"])
    page = ui.page
    await ui.settings("Правила")
    if not await page.get_by_label(text("rules.profile.new")).is_visible():
        await page.locator("button.accordion-button").first.click()
    await page.get_by_label(text("rules.profile.new")).fill(PROFILE)
    await page.locator("button.btn-add[title='Создать']").click()
    rules_step = page.get_by_text(text("rules.step.rules", profile=PROFILE))
    test_step = page.get_by_text(text("rules.step.test"))
    await rules_step.wait_for()
    await test_step.click()
    await page.get_by_label(text("rulesTest.field.address")).fill(ui.site.releases["dub"].url)
    await page.get_by_role("button", name=text("rulesTest.fetch.page")).click()
    await expect(page.locator(".parser-test-textarea")).to_have_value(re.compile(r"V:"), timeout=60_000)

    # The table: a row by the quick template, the season typed into its field of «ТО», saved by its button.
    await rules_step.click()
    await page.get_by_role("button", name=text("rules.table.add")).click()
    table = page.locator(".rule-table-card")
    await table.locator(".rule-name-input").fill("сезон")
    await _pick(page, table.locator(".col-then .item-select"), text("rules.action.assign_season"))
    await table.locator(".template-chip", has_text="V:").click()
    await table.locator("input.table-value").fill("7")
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
            page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
        await table.locator(f"button[title='{text('rules.rule.save')}']").click()
    assert (await saved.value).ok, "the table of the season was not saved"
    await expect(page.locator(".rule-table-card input.table-value")).to_have_value("7")
    await test_step.click()
    assert await _season_of_the_first_line(page) == ["Сезон: 7"]

    # The rule of the episode after the table: the table stops the line («продолжать» off), so the episode is not read;
    # with «продолжать» on the line goes on to the rule of the episode and keeps its season.
    await rules_step.click()
    await ui._rule({"name": "серия", "if": SINGLE, "then": [("extract_single", SINGLE)]})
    await test_step.click()
    assert await _season_of_the_first_line(page) == ["Сезон: 7"]
    await rules_step.click()
    async with page.expect_response(lambda r: r.request.method == "PUT" and "/api/parser-rules/" in r.url):
        await page.locator(".rule-table-card .p-toggleswitch").click()
    await test_step.click()
    assert await _season_of_the_first_line(page) == ["Сезон: 7", "Серия: 1"]

    # An ordinary rule that assigns the season, after the rule of the episode that stops: it is not reached.
    await rules_step.click()
    await ui._rule({"name": "другой сезон", "if": [{"type": "text", "value": "Серия"}], "then": [("assign_season", "9")]})
    await expect(page.locator(".rule-card").last.locator(".then-block input.item-input")).to_have_value("9")
    await test_step.click()
    assert await _season_of_the_first_line(page) == ["Сезон: 7", "Серия: 1"]


async def test_cells_joined_in_the_table_are_saved_joined_and_give_their_one_value_to_each_row(ui):
    """«ТО» of two rows joined into one cell: both conditions give its value; the table is read back with the cell joined."""
    for slug, (voice, height) in VOICES.items():
        ui.site.release(slug, voice)
        ui.site.episode(slug, 1, height=height)
    await ui.first_run()
    await ui.set_up([voice for voice, _height in VOICES.values()])
    page = ui.page
    await ui.settings("Правила")
    if not await page.get_by_label(text("rules.profile.new")).is_visible():
        await page.locator("button.accordion-button").first.click()
    await page.get_by_label(text("rules.profile.new")).fill(PROFILE)
    await page.locator("button.btn-add[title='Создать']").click()
    rules_step = page.get_by_text(text("rules.step.rules", profile=PROFILE))
    test_step = page.get_by_text(text("rules.step.test"))
    await rules_step.wait_for()
    await page.get_by_role("button", name=text("rules.table.add")).click()
    table = page.locator(".rule-table-card")
    await table.locator(".rule-name-input").fill("Озвучки")
    await _pick(page, table.locator(".col-then .item-select"), text("rules.action.assign_voiceover"))
    for slug in VOICES:
        await test_step.click()
        await page.get_by_label(text("rulesTest.field.address")).fill(ui.site.releases[slug].url)
        await page.get_by_role("button", name=text("rulesTest.fetch.page")).click()
        await expect(page.locator(".parser-test-textarea")).to_have_value(re.compile(r"V:"), timeout=60_000)
        await rules_step.click()
        await table.locator(".template-chip", has_text="V:").click()
    await expect(table.locator("tbody td.col-then")).to_have_count(2)

    # The two cells «ТО» are chosen together (a click, then Shift+click) and made one by the tool over the table.
    join = table.locator(".cell-tools .cell-join")
    await expect(join).to_be_disabled()
    await table.locator("tbody td.col-then").nth(0).click()
    await table.locator("tbody td.col-then").nth(1).click(modifiers=["Shift"])
    await expect(table.locator("tbody td.col-then.is-picked")).to_have_count(2)
    await ui.shot("t3-cells-chosen")
    await join.click()
    await expect(table.locator("tbody td.col-then")).to_have_count(1)
    await expect(table.locator(".cell-tools .cell-split")).to_be_enabled()
    await expect(table.locator("tbody td.col-then")).to_have_attribute("rowspan", "2")
    async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
            page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
        await table.locator(f"button[title='{text('rules.rule.save')}']").click()
    assert (await saved.value).ok, "the table with a joined cell was not saved"
    # Read back from the core: the cell is still one of two rows, with the value of the upper one.
    await expect(page.locator(".rule-table-card tbody td.col-then")).to_have_attribute("rowspan", "2")
    await expect(page.locator(".rule-table-card input.table-value")).to_have_value("AnimeVost")
    await ui.shot("t3-joined-cell")
    # The page of the second voice-over is still in the test: its line is given the value of the joined cell.
    await test_step.click()
    assert await _season_of_the_first_line(page) == ["Сезон: 1", f"{text('rulesTest.pill.voiceover')}: AnimeVost"]
    await expect(page.locator(".card-test-result .fired-rules").first).to_have_text(re.compile("Озвучки · 2"))
