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

import uvicorn
from playwright.async_api import Page, async_playwright, expect

from app_stand import AppStand

CATALOG = Path(__file__).resolve().parent.parent / "web" / "src" / "i18n" / "ru.ts"
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

    async def set_up(self, groups: list[str]) -> None:
        """What the user sets once: the address of the alignment service and its work folder, the groups of voice-overs."""
        page = self.page
        work = os.path.join(self.root, "work")
        connected = self.bus.subscribe("conform.daemon.up")
        await self.settings("Авторизация")
        await page.get_by_label(text("conform.settings.url")).fill(self.daemon_http.url)
        await page.get_by_label(text("conform.settings.local")).fill(work)
        await page.get_by_label(text("conform.settings.daemon")).fill(work)
        await page.get_by_role("button", name="Сохранить").click()
        await asyncio.wait_for(connected.queue.get(), 30)
        await expect(page.get_by_text(text("agents.conform.daemon.up"))).to_be_visible()
        await page.locator(f".st-tab[title='{text('settings.tab.voices')}']").click()
        for group in groups:
            await page.get_by_label(text("voices.settings.group.new")).fill(group)
            await page.get_by_role("button", name=text("voices.settings.group.create")).click()
            await expect(page.locator(".groups-editor .selected-value")).to_have_text(group)
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

    async def open_output(self, name: str) -> None:
        await self.page.locator(".series-card", has_text=name).first.locator("[title='Статус']").click()
        await self.page.locator(f".st-tab[title='{text('output.tab')}']").click()

    async def press_sync(self, series_id: int) -> None:
        """«Синхронизировать» in the section «Выход», to the end of the run the service tells (the world's signal: the
        service said it is done); what the screen shows then is the check of the scenario."""
        ended = self.bus.subscribe("scan.run.changed")
        await self.page.locator(".output-sync").click()
        await self._run_end(ended, series_id)


ADMIN, PASSWORD = "stand", "stand-throwaway"  # of the database of the stand, which goes with it
ACTION_LABEL = {"extract_single": "Извлечь номер серии", "extract_range": "Извлечь диапазон серий",
                "assign_voiceover": "Назначить озвучку/тег"}


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
