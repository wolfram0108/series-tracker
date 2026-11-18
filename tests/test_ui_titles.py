"""A title of the provider of external sources as the source of a series, in a browser (tests/ui_stand.py). The provider of the socket is a
model of it by the specification (tests/provider_mock.py) at an address the user names in the window of the extensions: the user
finds the title by the search of the provider in the window of a new series, adds the title it gave, reads it by its button,
puts the rule of its labels («第N集») to it and sees its line as a player. The title of the search keeps its parts at every width."""
import pytest
from playwright.async_api import expect

from provider_mock import MockProvider, serving
from ui_stand import _pick, state, text, ui  # noqa: F401 - the fixture

pytestmark = pytest.mark.ui

LABEL = [{"type": "text", "value": "第"}, {"type": "number"}, {"type": "text", "value": "集"}]
ORIGINAL = "Оригинал"
# Whether a card of the search keeps its parts apart and inside it at a width of the window: its action never over its
# words, no part out of its frame, the page never scrolled sideways.
CARD_LAYOUT = """() => {
  const bad = [];
  const box = (el) => el.getBoundingClientRect();
  const cross = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1;
  document.querySelectorAll('.ts-card').forEach((card) => {
    const frame = box(card), words = card.querySelector('.ts-body');
    card.querySelectorAll('.ts-line').forEach((line) => { if (box(line).right > box(words).right + 1) bad.push('line out of its words'); });
    card.querySelectorAll('*').forEach((el) => {
      const part = box(el);
      if (part.width && (part.right > frame.right + 1 || part.left < frame.left - 1)) bad.push(`${el.className} out`);
    });
  });
  if (document.documentElement.scrollWidth > window.innerWidth) bad.push('page scrolls sideways');
  return [...new Set(bad)];
}"""


async def test_a_title_found_by_the_search_of_the_provider_is_added_read_by_its_button_and_its_line_is_a_player(ui):  # noqa: F811
    mock = MockProvider()
    mock.add_site("chines", "Чинес")
    mock.title("chines", "k3f9a1", ["万界独尊"], [("第1集", "BLU", "1", [1080])], title="万界独尊 (2021)")
    mock.sites["chines"].found = [{"id": "k3f9a1", "name": "万界独尊"}]
    await ui.first_run()
    await ui.set_up([ORIGINAL], languages={ORIGINAL: "chi"})
    await ui.profile("серии 半日", [{"name": "серия", "if": LABEL,
                                    "then": [("extract_single", LABEL), ("assign_voiceover", ORIGINAL)]}])
    async with serving(mock) as server:
        await ui.use_provider(server.url)
        page = ui.page
        await page.get_by_role("button", name="Добавить сериал").click()
        await page.locator(".add-url-row .source-mode .st-tab", has_text=text("sources.mode.titles")).click()
        await page.get_by_label(text("sources.titles.query")).fill("万界独尊")
        await page.locator(".ts-find").click()
        found = page.locator(".ts-card[data-card='k3f9a1']")
        await expect(found).to_contain_text("万界独尊")
        await ui.shot("a1-search")
        size = page.viewport_size
        faults = {}
        for width in range(360, 1450, 10):
            await page.set_viewport_size({"width": width, "height": size["height"]})
            if found_here := await page.evaluate(CARD_LAYOUT):
                faults[width] = found_here
        await page.set_viewport_size(size)
        assert not faults, faults
        await found.locator(".ts-line[data-line='BLU'] .ts-add").click()

        source = page.locator(".source-card").first
        # A title of the provider is read when the user asks (its button), not when it is added.
        await expect(source.locator(".source-state")).to_have_text(state("notRead"))
        assert not [each for each in mock.requests if each[0] == "read"]
        await source.locator(".source-reread").click()
        # The head names the source by whose it is, which line it is and the title as its source gives it; the fields say the same
        # part by part, and the address is not written where a person reads.
        await expect(source.locator(".source-place")).to_have_text("Чинес · BLU · 万界独尊 (2021)")
        await expect(source.locator(".source-provider .item-value")).to_have_text("Чинес")
        await expect(source.locator(".source-line-code .item-value")).to_have_text("BLU")
        await expect(source.locator(".source-name .item-value")).to_have_text("万界独尊 (2021)")
        assert "title://" not in await source.inner_text()
        await _pick(page, source.locator(".item-select").first, "серии 半日")
        await expect(source.locator(".source-state")).to_have_text(state("readDone"), timeout=60_000)
        await expect(source.locator(".player-tab")).to_have_text(["BLU1"])  # a line is named by its code
        assert len([each for each in mock.requests if each[0] == "read"]) == 1
        # The search stays at hand in the sources: the title already added is marked by its place.
        sources = page.locator(".sources")
        await sources.locator(".source-mode .st-tab", has_text=text("sources.mode.titles")).click()
        await sources.locator(".ts-find").click()
        await expect(sources.locator(".ts-card[data-card='k3f9a1'] .ts-line[data-line='BLU'] .ts-added")).to_contain_text(text("sources.titles.added", n=1))
        await ui.shot("a2-added")
