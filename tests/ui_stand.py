"""The stand of the whole application with its interface: the stand of tests/app_stand.py whose gateway is served on real
HTTP with the built interface (web/dist), and a browser (Firefox through Playwright) as the user. What the user does goes
only through the screens; what the world outside does (the site, the player, the alignment service) is told to its models
as in the stand. Texts of the screens are found by the keys of the catalog web/src/i18n/ru.ts, never typed here."""
from __future__ import annotations

import asyncio
import os
import re
import socket
from pathlib import Path

import pytest
import uvicorn
from playwright.async_api import Page, async_playwright, expect

from app_stand import AppStand

CATALOG = Path(__file__).resolve().parent.parent / "web" / "src" / "i18n" / "ru.ts"
SERIES, SERIES_EN = "Сериал", "Serial"
SINGLE = [{"type": "text", "value": "Серия"}, {"type": "whitespace"}, {"type": "number"}]
SPAN = [{"type": "text", "value": "Серии"}, {"type": "whitespace"}, {"type": "number"}, {"type": "text", "value": "-"},
        {"type": "number"}]


def lines_of(voice: str) -> list[dict]:
    """The rules a user makes for a source of one voice-over: an episode and a compilation, each of its own form."""
    return [{"name": "компиляция", "if": SPAN, "then": [("extract_range", SPAN), ("assign_voiceover", voice)]},
            {"name": "серия", "if": SINGLE, "then": [("extract_single", SINGLE), ("assign_voiceover", voice)]}]


@pytest.fixture
async def ui(tmp_path):
    """The stand with its interface and a browser; a test module takes it by importing it."""
    made = UiStand(str(tmp_path))
    await made.start()
    await made.open()
    yield made
    await made.shut()
    await made.stop()
    made.close()
SHOTS = os.environ.get("ST_UI_SHOTS")  # a folder for a picture of the screen at every step a scenario names, when given


def text(key: str, **values) -> str:
    """The text of a key of the catalog, its {placeholders} filled."""
    found = re.search(rf'^\s*"{re.escape(key)}":\s*"((?:[^"\\]|\\.)*)"', CATALOG.read_text(encoding="utf-8"), re.M)
    if not found:
        raise KeyError(f"no key {key} in the catalog")
    said = found.group(1).replace("\\n", "\n").replace('\\"', '"')
    for name, value in values.items():
        said = said.replace(f"{{{name}}}", str(value))
    return said


class _LoopServer(uvicorn.Server):
    """A server in the loop of the stand: it tells that it listens by an event, not by a pause."""

    def __init__(self, config) -> None:
        super().__init__(config)
        self.ready = asyncio.Event()

    def install_signal_handlers(self) -> None:
        pass  # the process is the test's: its signals are not the server's

    async def startup(self, sockets=None) -> None:
        await super().startup(sockets=sockets)
        self.ready.set()


class UiStand(AppStand):
    """The stand with its interface on HTTP and a browser page; a restart of the process is a restart of its server."""

    def __init__(self, root: str) -> None:
        super().__init__(root)
        self.server: _LoopServer | None = None

    async def open(self) -> Page:
        await self._serve()
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.firefox.launch(headless=True)
        self.page = await self.browser.new_page(base_url=self.base, viewport={"width": 1440, "height": 1000})
        self.page.set_default_timeout(20_000)
        return self.page

    async def _serve(self) -> None:
        """The gateway on HTTP, on the port it had before when it had one: a restart of the process is a new server."""
        listening = socket.socket()
        listening.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listening.bind(("127.0.0.1", getattr(self, "port", 0)))
        self.port = listening.getsockname()[1]
        self.base = f"http://127.0.0.1:{self.port}"
        # The modules are started by the stand, not by the lifespan of the gateway's app.
        self.server = _LoopServer(uvicorn.Config(self.gateway.app, log_level="warning", lifespan="off", loop="asyncio",
                                                 ws="none"))
        self._serving = asyncio.create_task(self.server.serve(sockets=[listening]))
        await asyncio.wait_for(self.server.ready.wait(), 30)

    async def _unserve(self) -> None:
        """The server goes as a process that stops: its connections are cut at once, the stream of events too (a server
        that waits for them waits for ever: the stream does not end by itself)."""
        self.server.should_exit = True
        self.server.force_exit = True
        for connection in list(self.server.server_state.connections):
            connection.transport.close()
        await self._serving

    async def restart(self) -> None:
        await self._unserve()
        await super().restart()
        await self._serve()

    async def shot(self, name: str) -> None:
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            await self.page.screenshot(path=os.path.join(SHOTS, f"{name}.png"), full_page=True)

    async def shut(self) -> None:
        await self.browser.close()
        await self.playwright.stop()
        await self._unserve()

    # --- the user, through the screens ---------------------------------------------------------------------------

    async def first_run(self) -> None:
        """A new database asks for an administrator at the first start."""
        page = self.page
        await page.goto("/", wait_until="domcontentloaded")
        await page.get_by_label("Имя администратора").fill(ADMIN)
        await page.get_by_label("Пароль (от 8 символов)").fill(PASSWORD)
        await page.get_by_label("Повтор пароля").fill(PASSWORD)
        await page.get_by_role("button", name="Создать и войти").click()
        await page.get_by_label("Имя администратора").wait_for(state="detached")

    async def settings(self, tab: str) -> None:
        await self.page.get_by_role("button", name="Настройки").click()
        await self.page.locator(f".st-tab[title='{tab}']").click()

    async def close_settings(self) -> None:
        await self.page.locator(".modal-footer, .modern-footer").get_by_role("button", name="Закрыть").click()

    async def set_up(self, groups: list[str], languages: dict[str, str] | None = None) -> None:
        """What the user sets once: the address of the alignment service and its work folder, the groups of voice-overs,
        each given the language of its voice-over under it (`languages`: group → ISO 639-2; Russian for the others)."""
        page = self.page
        work = os.path.join(self.root, "work")
        connected = self.bus.subscribe("conform.daemon.up")
        await self.settings("Отладка")
        await page.get_by_label(text("conform.settings.url")).fill(self.daemon_http.url)
        await page.get_by_label(text("conform.settings.local")).fill(work)
        await page.get_by_label(text("conform.settings.daemon")).fill(work)
        # The block of the alignment server writes its address and folders when a field is left or Enter is pressed.
        await page.get_by_label(text("conform.settings.daemon")).press("Enter")
        await asyncio.wait_for(connected.queue.get(), 30)
        await expect(page.get_by_text(text("agents.conform.daemon.up"))).to_be_visible()
        await page.locator(f".st-tab[title='{text('settings.tab.voices')}']").click()
        for group in groups:
            await page.get_by_label(text("voices.settings.group.new")).fill(group)
            code = (languages or {}).get(group, "rus")
            await page.get_by_role("button", name=text("voices.settings.group.create")).click()
            await expect(page.locator(".groups-editor .item-select").first.locator(".selected-value")).to_have_text(group)
            await _pick(page, page.locator(".groups-editor .group-language .item-select"), text(f"voices.language.{code}"))
            await expect(page.locator(".groups-editor .group-language .selected-value")).to_have_text(
                text(f"voices.language.{code}"))
        await self.close_settings()

    async def profile(self, name: str, rules: list[dict]) -> None:
        """A profile of the constructor, made by hand: each rule its conditions and actions, every template dragged
        block by block from the palette. A rule: {"name", "if": [blocks], "then": [(kind, blocks or value)]}."""
        page = self.page
        await self.settings("Фильтры VK")
        if not await page.locator("#new-profile-name").is_visible():
            await page.locator("button.accordion-button").first.click()
        await page.locator("#new-profile-name").fill(name)
        await page.locator("button.btn-add[title='Создать']").click()
        await page.get_by_text(f"Шаг 2: Правила для профиля «{name}»").wait_for()
        for rule in rules:
            await self._rule(rule)
        await self.close_settings()

    async def _rule(self, rule: dict) -> None:
        page = self.page
        cards = page.locator(".rule-card")
        before = await cards.count()
        await page.get_by_role("button", name="Добавить правило").click()
        await expect(cards).to_have_count(before + 1)
        card = cards.last
        await card.locator(".rule-name-input").fill(rule["name"])
        await _template(card.locator(".if-block .condition-group").first.locator(".pattern-constructor"), rule["if"])
        for index, (kind, value) in enumerate(rule["then"]):
            if index:
                await card.locator(".then-block .btn-add[title='Добавить действие']").click()
                await expect(card.locator(".then-block .condition-group")).to_have_count(index + 1)
            group = card.locator(".then-block .condition-group").nth(index)
            await _pick(page, group.locator(".item-select").first, ACTION_LABEL[kind])
            if kind.startswith("extract_"):
                await _template(group.locator(".pattern-constructor"), value)
            else:
                await group.locator("input.item-input").fill(value)
        # The window saves the rule and reads the rules of the profile again: both answers of the server are the signal.
        async with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
                page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
            await card.locator("button[title='Сохранить правило']").click()
        assert (await saved.value).ok, "the rule was not saved"
        await expect(page.locator(".parser-loading")).to_have_count(0)

    async def add_series(self, name: str, name_en: str, sources: list[tuple[str, str]]) -> int:
        """A series added in its window: the address of each source and the profile of its lines; the window reads each
        table before the series is saved."""
        page = self.page
        await page.get_by_role("button", name="Добавить сериал").click()
        for index, (url, profile) in enumerate(sources):
            if index == 0:
                await page.get_by_label("URL для парсинга").fill(url)
                await page.locator(".add-url-row button.btn-add", has_text=text("sources.add.button")).click()
            else:
                await page.get_by_label(text("sources.add.label")).fill(url)
                await page.locator(".source-add button.btn-add", has_text=text("sources.add.button")).click()
            card = page.locator(".source-card").nth(index)
            await card.wait_for()
            await _pick(page, card.locator(".item-select").first, profile)
            await expect(card.locator(".source-state")).to_have_text(text("sources.state.ready"), timeout=60_000)
        await page.get_by_label("Название (RU)").fill(name)
        await page.get_by_label("Название (EN)").fill(name_en)
        await page.get_by_label("Путь сохранения").fill(self.library)
        await page.locator(".modern-footer").get_by_role("button", name="Добавить", exact=True).click()
        await page.locator(".series-card", has_text=name).first.wait_for()
        listed = await self.call("GET", "/api/series")
        return next(each["id"] for each in listed if each["name"] == name)

    async def make_series(self, slugs: list[str], also: tuple[str, ...] = ()) -> int:
        """The user's way to a series of the releases of these slugs: first start, the service, the groups of their
        voice-overs (and of `also`: voice-overs of sources added later), a profile of each in the constructor, the series
        in its window with a source per release."""
        voices = list(dict.fromkeys([self.site.releases[slug].voice for slug in slugs] + list(also)))
        await self.first_run()
        await self.set_up(voices)
        for voice in voices:
            await self.profile(f"серии {voice}", lines_of(voice))
        return await self.add_series(SERIES, SERIES_EN, [(self.site.releases[slug].url, f"серии {self.site.releases[slug].voice}")
                                                        for slug in slugs])

    async def tab(self, title: str) -> None:
        await self.page.locator(f".st-tab[title='{title}']").click()

    async def add_source(self, slug: str) -> None:
        """A source added to the series in its properties: its address, then the profile of its voice-over; the table is
        read before the source counts."""
        page = self.page
        release = self.site.releases[slug]
        await self.tab("Свойства")
        cards = page.locator(".source-card")
        before = await cards.count()
        await page.get_by_label(text("sources.add.label")).fill(release.url)
        await page.locator(".source-add button.btn-add", has_text=text("sources.add.button")).click()
        await expect(cards).to_have_count(before + 1)
        card = cards.nth(before)
        await _pick(page, card.locator(".item-select").first, f"серии {release.voice}")
        await expect(card.locator(".source-state")).to_have_text(text("sources.state.ready"), timeout=60_000)

    async def remove_source(self, slug: str) -> None:
        """A source taken out of the series in its properties («Удалить» on its card): at once, by its own request."""
        page = self.page
        await self.tab("Свойства")
        cards = page.locator(".source-card")
        before = await cards.count()
        card = cards.filter(has=page.locator(f"input[value='{self.site.releases[slug].url}']"))
        await card.locator(".source-remove").click()
        await expect(cards).to_have_count(before - 1)

    async def open_output(self, name: str = SERIES) -> None:
        await self.page.locator(".series-card", has_text=name).first.locator("[title='Статус']").click()
        await self.tab(text("output.tab"))

    async def drag(self, source, target) -> None:
        """An item of an ordered list dragged as a hand does it: pressed on the source, carried over the target in steps
        and let go on its first half, which puts it before the target (the engine moves an item once the pointer
        crosses into the item it passes)."""
        box = await target.bounding_box()
        await source.hover()
        await self.page.mouse.down()
        await self.page.mouse.move(box["x"] + box["width"] / 4, box["y"] + box["height"] / 4, steps=12)
        await self.page.mouse.up()

    async def press_sync(self, series_id: int) -> None:
        """«Синхронизировать» in the section «Выход», to the end of the run the service tells (the world's signal: the
        service said it is done); what the screen shows then is the check of the scenario."""
        ended = self.bus.subscribe("scan.run.changed")
        await self.page.locator(".output-sync").click()
        await self._run_end(ended, series_id)


ADMIN, PASSWORD = "stand", "stand-throwaway"  # of the database of the stand, which goes with it
ACTION_LABEL = {"extract_single": "Извлечь номер серии", "extract_range": "Извлечь диапазон серий",
                "assign_voiceover": "Назначить озвучку/тег", "assign_season": "Назначить номер сезона"}


def _option(page: Page, label: str):
    """The item of an opened list whose whole text is `label`."""
    return page.locator(".options-list .option").filter(has_text=re.compile(rf"^\s*{re.escape(label)}\s*$"))


async def _pick(page: Page, select, label: str) -> None:
    await select.click()
    await _option(page, label).click()
    await expect(select.locator(".selected-value")).to_have_text(label)


async def _template(editor, blocks: list[dict]) -> None:
    """The blocks of a template made again, in order: each dragged from the palette to the end, its text typed in."""
    container = editor.locator(".pattern-blocks-container")
    while count := await container.locator(".pattern-block").count():
        await container.locator(".pattern-block-remove").last.click()
        await expect(container.locator(".pattern-block")).to_have_count(count - 1)
    for index, block in enumerate(blocks):
        box = await container.bounding_box()
        await editor.locator(f".palette-btn.block-type-{block['type']}").drag_to(
            container, target_position={"x": max(box["width"] - 6, 2), "y": box["height"] / 2})
        await expect(container.locator(".pattern-block")).to_have_count(index + 1)
        if block.get("value") is not None:
            field = container.locator(".pattern-block").nth(index).locator(".pattern-block-input")
            await field.click()
            await field.press("Control+a")
            await field.type(block["value"])
            await field.press("Enter")  # the value is taken on blur
