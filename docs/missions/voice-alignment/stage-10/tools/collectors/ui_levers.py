"""Acceptance of the levers of the output through the interface, on the tables of two sources of one series (the dunhuatv.ru
page and the VK channel of «Расколотая битвой синева небес»; ruchime joins when it answers).

    ui_levers.py setup  --work <dir> [--safeguard 170]   the profiles, the series with its two sources, made in the browser
    ui_levers.py levers --work <dir> [--safeguard 170]   every lever at every level, one scenario after another, in the browser

The stand is the one of `live_three.py` (`read` keeps the tables in its database, so no site is read again while the hour
of a table lasts). Every action is a click or a typed text in a headless browser; what the action did is read back from the
window and, where the window says nothing, from the API and the database of the stand (read-only). After every scenario a
screenshot is kept in `<work>/shots`. A scenario that fails does not stop the others; the last line printed is
`ИЗМЕРЕНИЕ M-33: PASS ...` or `FAIL ...` with the numbers. Run it only through `progon_runner.py`.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx
from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_three import (CUT, EXCLUDE, FIRST, LAST, NUMBER_FIRST, OUT_NUMBER_FIRST, PROFILES as LIVE_PROFILES, REAL_DB, SERIES,
                        SERIES_EN, SOURCES, _extract, _rule)  # noqa: E402
from stand import RAW, Stand  # noqa: E402
from ui_journey import ADMIN, PASSWORD, text_of  # noqa: E402

DUNHUA = SOURCES[1][0]
CHANNEL = "https://vkvideo.ru/@donghuaworld"
QUERY = "Расколотая битвой синева небес"
PROFILE_SITE, PROFILE_VK = "dunhuatv 5", "vk 5, без озвучки"
VK_RULES = [_rule("Исключить до 207", [OUT_NUMBER_FIRST], EXCLUDE), _rule("N серия", [NUMBER_FIRST], [_extract(NUMBER_FIRST)])]
SEASON = 5


class Campaign:
    def __init__(self, stand: Stand, work: Path, safeguard: float) -> None:
        self.stand, self.work = stand, work
        self.deadline = time.monotonic() + safeguard
        self.results: list[dict] = []
        self.shots = work / "shots"
        self.shots.mkdir(parents=True, exist_ok=True)
        self.http = httpx.Client(base_url=stand.base, timeout=60)
        self.series_id = 0

    # --- the clock and the record ---------------------------------------------------------------------------------

    def left_ms(self) -> int:
        return max(1000, int((self.deadline - time.monotonic()) * 1000))

    def scenario(self, name: str, page: Page, run) -> bool:
        """One scenario: a failure is a result, not the end of the run."""
        began = time.monotonic()
        try:
            facts = run() or {}
            ok, detail = True, facts
        except Exception as exc:  # noqa: BLE001 - the others go on
            ok, detail = False, {"error": f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"}
            traceback.print_exc()
        shot = self.shots / f"{len(self.results) + 1:02d}.png"
        try:
            page.screenshot(path=str(shot), full_page=True)
        except Exception:  # noqa: BLE001 - a screenshot is evidence, not a part of the scenario
            shot = None
        self.results.append({"scenario": name, "ok": ok, "detail": detail, "shot": str(shot) if shot else None,
                             "seconds": round(time.monotonic() - began, 1)})
        print(f"  {'✓' if ok else '✗'} {name} {json.dumps(detail, ensure_ascii=False)[:420]}", flush=True)
        return ok

    # --- the stand ---------------------------------------------------------------------------------------------------

    def api(self, path: str):
        response = self.http.get(path)
        response.raise_for_status()
        return response.json()

    def db(self, query: str, params=()) -> list[sqlite3.Row]:
        conn = sqlite3.connect(f"file:{self.work}/app.db?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(query, params))
        finally:
            conn.close()

    def profile_ids(self) -> dict[str, int]:
        existing = {each["name"]: each["id"] for each in self.api("/api/parser-profiles")}
        for name, rules in ((PROFILE_SITE, LIVE_PROFILES["dunhuatv"]), (PROFILE_VK, VK_RULES)):
            if name in existing:
                continue
            made = self.http.post("/api/parser-profiles", json={"name": name}).json()
            for rule in rules:
                self.http.post(f"/api/parser-profiles/{made['id']}/rules", json=rule).raise_for_status()
            existing[name] = made["id"]
        return existing


def first_run(page: Page) -> None:
    if page.get_by_label("Имя администратора").count():
        page.get_by_label("Имя администратора").fill(ADMIN)
        page.get_by_label("Пароль (от 8 символов)").fill(PASSWORD)
        page.get_by_label("Повтор пароля").fill(PASSWORD)
        page.get_by_role("button", name="Создать и войти").click()
        page.get_by_label("Имя администратора").wait_for(state="detached", timeout=20000)


def reading_done(page: Page, c: Campaign) -> None:
    page.wait_for_function("() => !document.querySelector('.source-card .source-state.is-reading')", timeout=c.left_ms())


def pick_profile(page: Page, card: int, name: str) -> None:
    page.locator(".source-card").nth(card).locator(".item-select").first.click()
    page.locator(".options-list .option", has_text=name).click()
    page.wait_for_function("(n) => document.querySelectorAll('.source-card')[n].querySelector('.source-state.is-ready, .source-state.is-failed')",
                           arg=card, timeout=60000)


def setup(c: Campaign, page: Page) -> None:
    c.profile_ids()
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)
    if page.locator(".series-card", has_text=SERIES).count():
        # Only a setup that did not come to its end is started again; the series of a finished one is kept.
        existing = next(each for each in c.api("/api/series") if each["name"] == SERIES)
        if len(c.api(f"/api/series/{existing['id']}/sources")) >= 2:
            print("  сериал уже есть: setup пропущен", flush=True)
            return
        c.http.delete(f"/api/series/{existing['id']}").raise_for_status()
        page.reload(wait_until="domcontentloaded")

    def make() -> dict:
        page.get_by_role("button", name="Добавить сериал").click(timeout=30000)
        page.get_by_label("URL для парсинга").fill(DUNHUA)
        page.locator("button.source-action", has_text=text_of("sources.add.button")).click(timeout=20000)
        page.wait_for_selector(".source-card", timeout=20000)
        reading_done(page, c)
        players = page.locator(".source-card").nth(0).locator("li.player").count()
        voices = page.locator(".source-card").nth(0).locator("li.voice").count()
        assert players == 2 and voices >= 6, f"dunhuatv: players {players}, voices {voices}"
        return {"players": players, "voices": voices}

    c.scenario("источник dunhuatv.ru добавлен по адресу; плееры и озвучки видны, у озвучек «нет группы»", page, make)

    def profile_site() -> dict:
        pick_profile(page, 0, PROFILE_SITE)
        return {"state": page.locator(".source-card").nth(0).locator(".source-state").inner_text()}

    c.scenario("профиль назначен источнику dunhuatv.ru", page, profile_site)

    def channel() -> dict:
        page.get_by_label(text_of("sources.add.label")).fill(CHANNEL)
        page.locator("button.source-action", has_text=text_of("sources.add.button")).click(timeout=20000)
        page.wait_for_function("() => document.querySelectorAll('.source-card').length === 2", timeout=20000)
        fields = page.locator(".source-card").nth(1).locator(".grid-fit input")
        fields.nth(1).fill(QUERY)
        fields.nth(1).press("Tab")
        page.wait_for_function("() => !document.querySelector('.source-card .source-state.is-reading')", timeout=c.left_ms())
        return {"cards": page.locator(".source-card").count(), "address": fields.nth(0).input_value(), "query": fields.nth(1).input_value()}

    c.scenario("второй источник — канал VK: адрес и поисковый запрос набраны в карточке", page, channel)

    def profile_vk() -> dict:
        pick_profile(page, 1, PROFILE_VK)
        return {"state": page.locator(".source-card").nth(1).locator(".source-state").inner_text()}

    c.scenario("профиль без озвучки назначен каналу VK", page, profile_vk)

    def series() -> dict:
        page.get_by_label("Название (RU)").fill(SERIES)
        page.get_by_label("Название (EN)").fill(SERIES_EN)
        page.get_by_label("Путь сохранения").fill(str(c.work / "library" / SERIES_EN))
        page.get_by_label("Сезон (формат s01)").fill(f"s0{SEASON}")
        page.locator(".modern-footer").get_by_role("button", name="Добавить", exact=True).click(timeout=15000)
        page.wait_for_selector(".modal-title", state="detached", timeout=60000)
        page.locator(".series-card", has_text=SERIES).first.wait_for(timeout=30000)
        listed = c.api("/api/series")
        c.series_id = next(each["id"] for each in listed if each["name"] == SERIES)
        return {"series_id": c.series_id}

    c.scenario("сериал с двумя источниками создан в окне добавления", page, series)


def open_output(page: Page, base: str | None = None) -> None:
    if base:
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector(".series-card", timeout=30000)
    page.locator(".series-card", has_text=SERIES).first.locator("[title='Статус']").click()
    page.locator(".st-tab[title='Выход']").click()
    page.wait_for_selector(".output-episode .episode-marks li", timeout=60000)


def series_levers(page: Page):
    """The levers of the whole series, opened."""
    level = page.locator("section.output-level[data-scope='series']")
    if not level.locator(":scope > .output-levers").count():
        level.locator(":scope > header .level-levers").click()
    return level.locator(":scope > .output-levers")


def explore(c: Campaign, page: Page) -> dict:
    grid = series_levers(page)
    columns = {}
    for column in grid.locator(".voice-column").all():
        columns[column.get_attribute("data-group")] = [each.inner_text().split("\n")[0] for each in column.locator(".variant-name").all()]
    return {"columns": columns, "episodes": page.locator(".output-episode").count()}


def levers(c: Campaign, page: Page, context) -> None:
    c.series_id = next(each["id"] for each in c.api("/api/series") if each["name"] == SERIES)
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)
    c.scenario("«Выход» открыт: серии с референсом по событиям", page, lambda: (open_output(page), {"episodes": page.locator(".output-episode").count()})[1])
    c.scenario("разведка сетки рычагов сериала", page, lambda: explore(c, page))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("setup", "levers"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    verdict = "FAIL прибор"
    results: list[dict] = []
    try:
        with Stand(credentials_from=REAL_DB, work=args.work) as stand, sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            page = context.new_page()
            campaign = Campaign(stand, Path(args.work), args.safeguard - 15)
            try:
                if args.phase == "setup":
                    setup(campaign, page)
                else:
                    levers(campaign, page, context)  # noqa: F821
            finally:
                results = campaign.results
                browser.close()
        failed = [each["scenario"] for each in results if not each["ok"]]
        verdict = (f"PASS сценариев {len(results)}" if results and not failed
                   else f"FAIL сценариев {len(results)}, не прошло {len(failed)}: {failed[:4]}")
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        verdict = f"FAIL прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"M-33__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"phase": args.phase, "verdict": verdict, "results": results}, ensure_ascii=False) + "\n")
    print(f"ИЗМЕРЕНИЕ M-33: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
