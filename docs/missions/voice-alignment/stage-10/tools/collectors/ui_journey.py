"""M-27 and M-28: the life of a series in a browser, and two windows of one series.

    ui_journey.py [--url <address>] [--safeguard 170]

A new database with one general profile (the number of the episode after the word "Серия") is migrated and served by
the stand (`stand.py`), so that no series of a real database is read at the first run; a headless browser adds a series from one
site address, picks the profile, opens the section "Output", and goes the steps of `SPEC.md`, section 6, that a small
release can show: reading with its progress (Ж-2), the profile (Ж-7), the references and voice-overs of each episode
(Ж-7), a manual reference (Ж-8), an episode switched off, and the second window that follows by events (M-28).
Texts are read from the catalog `web/src/i18n/ru.ts`, never typed here. The sync with downloads (Ж-9) and the conform
service (M-25) are not in this run: they download and need the service.

Raw results go to `raw/measurements/M-27__<date>.jsonl`; the last two lines printed in any outcome are
`ИЗМЕРЕНИЕ M-28: ...` and `ИЗМЕРЕНИЕ M-27: PASS ...` or `FAIL ...`. Start it only through `progon_runner.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stand import RAW, ROOT, Stand  # noqa: E402

CATALOG = ROOT / "web" / "src" / "i18n" / "ru.ts"
SERIES_NAME = "Тест этапа 10"
PROFILE = "общий"
QUIET_S = 12
GROUP = "Ruchime"
UNKNOWN_VOICE = "Неизвестная озвучка"  # the core words a voice-over of no group so
# The line of the constructor ends with the word for the episode and its number; the season comes from the series.
PROFILES = [{"name": PROFILE, "rules": [{
    "name": "серия", "conditions": [[{"type": "text", "value": "Серия"}]],
    "actions": [{"action_type": "extract_single", "action_pattern": json.dumps(
        [{"type": "text", "value": "Серия"}, {"type": "whitespace"}, {"type": "number"}])}]}]}]
ADMIN, PASSWORD = "stage10", "stage10-throwaway"  # of the database of the stand, which is deleted with it


def text_of(key: str) -> str:
    found = re.search(rf'^\s*"{re.escape(key)}":\s*"((?:[^"\\]|\\.)*)"', CATALOG.read_text(encoding="utf-8"), re.M)
    if not found:
        raise KeyError(f"no key {key} in the catalog")
    return found.group(1).replace("\\n", "\n")


def option_named(page: Page, text: str):
    """The item of an opened list whose whole text is `text`: «vk 5» must not take «vk 5, без озвучки» too."""
    return page.locator(".options-list .option").filter(has_text=re.compile(rf"^\s*{re.escape(text)}\s*$"))


class Journey:
    def __init__(self, stand: Stand, url: str, profile: str, safeguard: float) -> None:
        self.stand, self.url, self.profile = stand, url, profile
        self.deadline = time.monotonic() + safeguard
        self.steps: list[dict] = []
        self.requests_of_second_window: list[str] = []

    def left_ms(self) -> int:
        return max(1000, int((self.deadline - time.monotonic()) * 1000))

    def step(self, name: str, **facts) -> None:
        self.steps.append({"step": name, "t": round(time.monotonic() - self.began, 1), **facts})
        print(f"  {name} {facts or ''}", flush=True)

    def run(self, page: Page, second: Page) -> None:
        self.began = time.monotonic()
        base = self.stand.base
        self.create(page)
        self.exercise(page, second)

    def create(self, page: Page) -> int:
        """First start, the source of a site, its profile, the series: what every run of the browser begins with."""
        base = self.stand.base
        page.goto(base + "/", wait_until="domcontentloaded")
        self.first_run(page)
        page.get_by_role("button", name="Добавить сериал").click(timeout=30000)
        page.get_by_label("URL для парсинга").fill(self.url)
        page.locator("button.source-action", has_text=text_of("sources.add.button")).click(timeout=20000)
        page.wait_for_selector(".source-card", timeout=20000)
        page.wait_for_selector(".source-card .source-state.is-reading", timeout=20000)
        progress = page.locator(".source-card .source-state").first.inner_text()
        self.step("Ж-2 чтение идёт, ход виден", shown=progress)
        page.wait_for_function("() => !document.querySelector('.source-card .source-state.is-reading')", timeout=self.left_ms())
        players = page.locator(".source-card li.player").count()
        voices = page.locator(".source-card li.voice").count()
        assert players >= 2 and voices >= 2, f"players {players}, voices {voices}"
        group_note = page.locator(".source-card").inner_text()
        assert text_of("sources.voice.noGroup") in group_note, "an unknown voice-over is not marked as without a group"
        self.step("Ж-2 таблица готова: плееры, озвучки без группы", players=players, voices=voices)

        page.locator(".source-card .item-select").first.click()
        option_named(page, self.profile).click()
        page.wait_for_selector(".source-card .source-state.is-ready", timeout=60000)
        self.step("Ж-7 профиль назначен, строки получили номера")

        page.get_by_label("Название (RU)").fill(SERIES_NAME)
        page.get_by_label("Название (EN)").fill("Stage 10 test")
        page.get_by_label("Путь сохранения").fill("/tmp/stage10-journey")
        page.get_by_label("Сезон (формат s01)").fill("s01")
        footer = page.locator(".modern-footer").get_by_role("button", name="Добавить", exact=True)
        footer.click(timeout=15000)
        page.wait_for_selector(".modal-title", state="detached", timeout=60000)
        card = page.locator(".series-card", has_text=SERIES_NAME)
        card.first.wait_for(timeout=30000)
        listed = httpx.get(base + "/api/series", timeout=30).json()
        series_id = next(each["id"] for each in listed if each["name"] == SERIES_NAME)
        self.step("сериал создан", series_id=series_id)
        return series_id

    def exercise(self, page: Page, second: Page) -> None:
        base = self.stand.base
        self.open_output(page, SERIES_NAME)
        page.wait_for_selector(".output-episode .episode-marks li", timeout=90000)
        episodes = page.locator(".output-episode").count()
        marks = page.locator(".output-episode .episode-marks li").first.inner_text()
        assert episodes >= 2, f"episodes {episodes}"
        assert text_of("output.mark.by_level") in marks, f"first mark {marks!r}"
        self.step("Ж-7 раздел «Выход»: серии с референсом и отметкой по событиям", episodes=episodes, mark=marks)

        heading = page.locator(".output-episode").first.locator("header").inner_text()
        assert UNKNOWN_VOICE in heading, f"before the group: {heading!r}"
        third = page.context.new_page()
        third.goto(base + "/", wait_until="domcontentloaded")
        third.get_by_role("button", name="Настройки").click(timeout=30000)
        third.locator(f".st-tab[title='{text_of('settings.tab.voices')}']").click()
        third.get_by_label(text_of("voices.settings.group.new")).fill(GROUP)
        third.get_by_role("button", name=text_of("voices.settings.group.create")).click()
        page.locator(".output-episode").first.locator("header", has_text=f"{GROUP} —").wait_for(timeout=30000)
        third.close()
        self.step("Ж-3 группа озвучки заведена в настройках; окно сериала показало её событием, без перезагрузки", group=GROUP)

        first = page.locator(".output-episode").first
        buttons = first.locator("button").all_inner_texts()
        assert text_of("output.levers.show") in buttons[0], f"buttons of the first episode: {buttons}"
        first.locator("button").first.click()
        # The levers are a grid: a column per voice-over group, in it the variants (source and player) with the mark "реф".
        variants = first.locator(".voice-column .variant-reference:not(:disabled)")
        options = variants.count()
        assert options >= 2, f"variants that can be the reference {options}"
        self.open_output(second, SERIES_NAME, base)
        second.wait_for_selector(".output-episode .episode-marks li", timeout=30000)
        second.on("request", lambda request: self.requests_of_second_window.append(f"{request.method} {request.url}"))
        variants.nth(1).click()
        second.locator(".output-episode").first.locator(".episode-marks li", has_text=text_of("output.mark.own")).wait_for(timeout=20000)
        page.locator(".output-episode").first.locator(".episode-marks li", has_text=text_of("output.mark.own")).wait_for(timeout=20000)
        self.step("Ж-8 ручной выбор референса серии; второе окно увидело его событием", options=options)

        deviations = page.locator(".output-deviations input")
        deviations.check()
        shown = page.locator(".output-episode").count()
        assert 1 <= shown < episodes, f"filter shows {shown} of {episodes}"
        deviations.uncheck()
        self.step("фильтр «только отклонения»", shown=shown, of=episodes)

        target = page.locator(".output-episode").nth(1)
        target.locator(".episode-switch", has_text=text_of("output.episode.disable")).click()
        second.locator(".output-episode").nth(1).locator(".episode-marks li", has_text=text_of("output.mark.off")).wait_for(timeout=20000)
        assert "is-off" in (second.locator(".output-episode").nth(1).get_attribute("class") or "")
        self.step("серия отключена; второе окно увидело отключение событием")
        page.locator(".output-episode").nth(1).locator(".episode-switch", has_text=text_of("output.episode.enable")).click()
        second.locator(".output-episode").nth(1).locator(".episode-marks li", has_text=text_of("output.mark.by_level")).wait_for(timeout=20000)
        self.step("серия включена обратно; референс вернулся")

        def asked() -> int:
            return len([each for each in self.requests_of_second_window if each.startswith("GET")
                        and "/api/series/" in each and ("/output" in each or "/composition" in each)])

        # The reads the second window made came after events (a lever moved the layout); with nothing happening it asks
        # nothing: a quiet window of the length of any poll that a screen could have, measured here as a subject of the run.
        before = asked()
        second.wait_for_timeout(QUIET_S * 1000)
        assert asked() == before, f"the second window asked the API {asked() - before} times with nothing happening"
        self.step("M-28 второе окно читает только по событиям и молчит без них", reads_after_events=before, quiet_s=QUIET_S)

    def first_run(self, page: Page) -> None:
        """A new database asks for an administrator at the first start; the stand's is a throw-away one."""
        page.get_by_label("Имя администратора").fill(ADMIN)
        page.get_by_label("Пароль (от 8 символов)").fill(PASSWORD)
        page.get_by_label("Повтор пароля").fill(PASSWORD)
        page.get_by_role("button", name="Создать и войти").click()
        page.get_by_label("Имя администратора").wait_for(state="detached", timeout=20000)
        self.step("первый запуск: администратор создан, вход выполнен")

    def open_output(self, page: Page, name: str, base: str | None = None) -> None:
        if base:
            page.goto(base + "/", wait_until="domcontentloaded")
            page.wait_for_selector(".series-card", timeout=30000)
        page.locator(".series-card", has_text=name).first.locator("[title='Статус']").click()
        page.locator(".st-tab[title='Выход']").click()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://1.ruchime.org/release/asura-bezumnyi-korol-2")
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    verdict, detail, steps = "FAIL", "", []
    journey = None
    try:
        with Stand(profiles=PROFILES) as stand, sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page, second = context.new_page(), context.new_page()
            journey = Journey(stand, args.url, PROFILE, args.safeguard)
            try:
                journey.run(page, second)
                verdict = "PASS"
            except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
                detail = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                RAW.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(RAW / f"M-27__{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.png"))
                traceback.print_exc()
            finally:
                steps = journey.steps
                browser.close()
    except Exception as exc:  # noqa: BLE001
        detail = f"прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"M-27__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"verdict": verdict, "detail": detail, "steps": steps}, ensure_ascii=False) + "\n")
    names = [each["step"] for each in steps]
    second_window = [name for name in names if name.startswith("M-28") or "второе окно" in name]
    print(f"ИЗМЕРЕНИЕ M-28: {'PASS' if verdict == 'PASS' else 'FAIL'} шагов второго окна {len(second_window)} {detail}")
    print(f"ИЗМЕРЕНИЕ M-27: {verdict} шагов {len(steps)} {detail}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
