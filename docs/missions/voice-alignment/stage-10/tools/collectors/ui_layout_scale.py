"""M-29, the layout part: the windows on the scale of widths 320-1200 in steps of 10.

    ui_layout_scale.py [--url <address>] [--step 10] [--safeguard 170]

The stand and the series are those of `ui_journey.py`. In each state of each window (the section "Output" with its levers
open, the properties of the series, the add window with a source, the voice-overs of the settings) the width of the
window of the browser goes over the scale, and at every width a detector reads the layout: something that reaches out of
the screen or out of the box that clips it, and a text that its box cuts. Waits are by frames of the browser, not by time.

Raw results: `raw/measurements/M-29__<date>.jsonl`; the last line printed is `ИЗМЕРЕНИЕ M-29: PASS ...` or `FAIL ...`.
Run it only through `progon_runner.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stand import RAW, Stand  # noqa: E402
from ui_journey import PROFILE, PROFILES, SERIES_NAME, Journey, text_of  # noqa: E402

HEIGHT = 900
FRAMES = "() => new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))"
DETECT = """() => {
  const width = window.innerWidth
  const found = []
  const name = (e) => e.tagName.toLowerCase() + (typeof e.className === 'string' && e.className.trim()
    ? '.' + e.className.trim().split(/\\s+/).slice(0, 2).join('.') : '')
  if (document.documentElement.scrollWidth > width + 1) {
    found.push({ kind: 'page-scroll', el: 'html', by: document.documentElement.scrollWidth - width })
  }
  const clipOf = (e) => {
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== 'visible') return p
    }
    return null
  }
  // Not seen: an element or an ancestor with no opacity (the label of a tab that is folded into its icon).
  const unseen = (e) => {
    for (let p = e; p && p !== document.body; p = p.parentElement) {
      if (getComputedStyle(p).opacity === '0') return true
    }
    return false
  }
  const scope = document.querySelector('.modal-overlay') || document.body
  for (const e of scope.querySelectorAll('*')) {
    const cs = getComputedStyle(e)
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.position === 'fixed' || unseen(e)) continue
    const r = e.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    const clip = clipOf(e)
    const box = clip ? clip.getBoundingClientRect() : { left: 0, right: width }
    // The strip of tabs scrolls by design when even the icons do not fit, so as not to push the close button out.
    if (!e.closest('.st-tabs') && (r.right > box.right + 1 || r.left < box.left - 1)) {
      found.push({ kind: 'outside', el: name(e), by: Math.round(Math.max(r.right - box.right, box.left - r.left)) })
    }
    if ((cs.overflowX === 'hidden' || cs.textOverflow === 'ellipsis') && e.scrollWidth > e.clientWidth + 1 && e.textContent.trim()) {
      found.push({ kind: 'cut-text', el: name(e), by: e.scrollWidth - e.clientWidth })
    }
  }
  return found.slice(0, 30)
}"""


def sweep(page: Page, state: str, widths: range, results: list[dict]) -> int:
    """The detector at every width; what it finds is summed by (kind, element) with the widths it appears at."""
    seen: dict[tuple[str, str], dict] = {}
    for width in widths:
        page.set_viewport_size({"width": width, "height": HEIGHT})
        page.evaluate(FRAMES)
        for each in page.evaluate(DETECT):
            entry = seen.setdefault((each["kind"], each["el"]), {"state": state, "kind": each["kind"], "el": each["el"],
                                                                 "widths": [], "by": 0})
            entry["widths"].append(width)
            entry["by"] = max(entry["by"], each["by"])
    for entry in seen.values():
        results.append({**entry, "from": min(entry["widths"]), "to": max(entry["widths"]), "count": len(entry["widths"])})
        del results[-1]["widths"]
    print(f"  {state}: {len(seen)} находок на {len(widths)} ширинах", flush=True)
    return len(seen)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="https://1.ruchime.org/release/asura-bezumnyi-korol-2")
    parser.add_argument("--step", type=int, default=10)
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    widths = range(320, 1201, args.step)
    results: list[dict] = []
    verdict, detail = "FAIL", ""
    try:
        with Stand(profiles=PROFILES) as stand, sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": HEIGHT}).new_page()
            journey = Journey(stand, args.url, PROFILE, args.safeguard)
            journey.began = time.monotonic()
            try:
                journey.create(page)
                journey.open_output(page, SERIES_NAME)
                page.wait_for_selector(".output-episode .episode-marks li", timeout=90000)
                page.locator(".level-levers").first.click()
                page.locator(".output-episode").first.locator("button").first.click()
                sweep(page, "окно сериала: «Выход», рычаги открыты", widths, results)
                page.locator(".st-tab[title='Свойства']").click()
                sweep(page, "окно сериала: свойства", widths, results)
                page.locator(".modern-close").last.click()
                page.get_by_role("button", name="Добавить сериал").click()
                page.get_by_label("URL для парсинга").fill(args.url)
                page.locator("button.source-action", has_text=text_of("sources.add.button")).click(timeout=20000)
                page.wait_for_selector(".source-card .source-state.is-ready, .source-card .source-state.is-noProfile", timeout=60000)
                sweep(page, "окно добавления: источник", widths, results)
                page.locator(".modern-footer").get_by_role("button", name="Отмена").click()
                page.get_by_role("button", name="Настройки").click()
                page.locator(f".st-tab[title='{text_of('settings.tab.voices')}']").click()
                sweep(page, "настройки: озвучки", widths, results)
                verdict = "PASS" if not results else "FAIL"
                detail = f"находок {len(results)} на шкале {widths.start}-{widths.stop - 1} шаг {args.step}"
            except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
                detail = f"{type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                RAW.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(RAW / f"M-29__{datetime.now(timezone.utc):%Y%m%dT%H%M%S}.png"))
                traceback.print_exc()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001
        detail = f"прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"M-29__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"verdict": verdict, "detail": detail, "findings": results}, ensure_ascii=False) + "\n")
    for each in results[:40]:
        print(f"  НАХОДКА {each['state']} | {each['kind']} {each['el']} | {each['from']}-{each['to']} ({each['count']} ширин) на {each['by']} px")
    print(f"ИЗМЕРЕНИЕ M-29: {verdict} {detail}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
