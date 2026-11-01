"""M-11 in the browser: the test window of a profile on the tables of the three sources, with the range filter at work.

    ui_range.py --work <dir> [--safeguard 170]

The stand is the one of `live_three.py` (its database keeps the tables and the three profiles). In the settings of the parser
the profile of the channel and the profile of dunhuatv.ru are tried on their pages: the cards the window shows must be the
rows the range filter cut (marked as excluded) and the rows it left, with the numbers and the voice-over the profile gave.
The last line printed is `ИЗМЕРЕНИЕ M-11: PASS|FAIL ...`. Run it only through `progon_runner.py`.
"""
from __future__ import annotations

import argparse
import re
import sys
import traceback
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_three import REAL_DB, SOURCES  # noqa: E402
from stand import Stand  # noqa: E402

CATALOG = Path(__file__).resolve().parents[6] / "web" / "src" / "i18n" / "ru.ts"


def text_of(key: str) -> str:
    found = re.search(rf'^\s*"{re.escape(key)}":\s*"((?:[^"\\]|\\.)*)"', CATALOG.read_text(encoding="utf-8"), re.M)
    if not found:
        raise KeyError(key)
    return found.group(1)


def first_run(page: Page) -> None:
    if page.get_by_label("Имя администратора").count():
        page.get_by_label("Имя администратора").fill("stage10")
        page.get_by_label("Пароль (от 8 символов)").fill("stage10-throwaway")
        page.get_by_label("Повтор пароля").fill("stage10-throwaway")
        page.get_by_role("button", name="Создать и войти").click()
        page.get_by_label("Имя администратора").wait_for(state="detached", timeout=20000)


def try_profile(page: Page, profile: str, address: str, query: str) -> dict:
    if not page.locator(".accordion-item .item-select").first.is_visible():
        page.locator("button.accordion-button").first.click()
    page.locator(".accordion-item .item-select").first.click()
    page.locator(".options-list .option", has_text=profile).click()
    page.locator("button.accordion-button", has_text="Шаг 3").click()
    page.get_by_label(text_of("rulesTest.field.address")).fill(address)
    page.get_by_label(text_of("rulesTest.field.query")).fill(query)
    page.locator("button.btn-search").click()
    page.wait_for_selector(".card-test-result", timeout=120000)
    page.wait_for_function("() => !document.querySelector('.parser-test .fieldset-hint .pi-spin')", timeout=120000)
    cards = page.locator(".card-test-result")
    total = cards.count()
    excluded = page.locator(".card-test-result.status-excluded").count()
    kept = page.locator(".card-test-result.status-success")
    episodes = sorted({int(m.group(1)) for text in kept.locator(".pill").all_inner_texts()
                       for m in [re.search(rf"{text_of('rulesTest.pill.episode')}:\s*(\d+)", text)] if m})
    voiceovers = sorted({text.split(":", 1)[1].strip() for text in kept.locator(".pill").all_inner_texts()
                         if text.startswith(text_of("rulesTest.pill.voiceover"))})
    return {"profile": profile, "cards": total, "excluded": excluded, "kept": kept.count(), "episodes": episodes, "voiceovers": voiceovers}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    verdict = "FAIL прибор"
    try:
        with Stand(credentials_from=REAL_DB, work=args.work) as stand, sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
            try:
                page.goto(stand.base + "/", wait_until="domcontentloaded")
                first_run(page)
                page.get_by_role("button", name="Настройки").click(timeout=30000)
                page.locator(".st-tab[title='Фильтры VK']").click()
                channel = try_profile(page, "vk 5", "https://vkvideo.ru/@donghuaworld", "Расколотая битвой синева небес")
                print("  канал:", channel, flush=True)
                site = try_profile(page, "dunhuatv 5", SOURCES[1][0], "")
                print("  dunhuatv:", site, flush=True)
                ok = (channel["excluded"] == 3 and channel["episodes"] == [208, 212, 217] and channel["voiceovers"] == ["AniStar"]
                      and site["excluded"] > 1000 and site["episodes"] and min(site["episodes"]) >= 208 and max(site["episodes"]) <= 217)
                verdict = (f"PASS окно теста: канал {channel['excluded']} исключено, остались серии {channel['episodes']} с озвучкой "
                           f"{channel['voiceovers']}; dunhuatv {site['excluded']} исключено, остались серии {site['episodes'][0]}–{site['episodes'][-1]}"
                           if ok else f"FAIL канал {channel}; dunhuatv {site}")
            except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
                verdict = f"FAIL {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                traceback.print_exc()
                page.screenshot(path=str(Path(args.work) / "range_fail.png"))
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        verdict = f"FAIL прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    print(f"ИЗМЕРЕНИЕ M-11: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
