"""M-35: the profiles of the parser made by hand in the constructor of the settings, as a user makes them.

    ui_profiles.py --work <dir> [--safeguard 170]

The stand is the one of `live_three.py` (its database keeps the tables of dunhuatv.ru and of the VK channel, so no site is read
again). For each of the three profiles of `live_three.PROFILES` the browser does what a user does: types the name and presses
"create", adds the rules, builds every condition and every action by dragging the blocks from the palette, types the texts and
the ranges into the blocks, switches "and"/"or", picks the kind of the action, saves the rule. What the browser made is read
back from the API and must be the very rules the profile of the live runs has; the profiles of dunhuatv.ru and of the channel
are then tried in the test window on their tables. A profile is renamed and deleted the same way. After each scenario a
screenshot is kept in `<work>/shots`. The last line printed is `ИЗМЕРЕНИЕ M-35: PASS|FAIL ...`. Run it only through
`progon_runner.py`; the work folder must hold a database with no profile of these names.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import Locator, Page, expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_three import PROFILES, REAL_DB, SOURCES  # noqa: E402
from stand import RAW, Stand  # noqa: E402
from ui_journey import option_named  # noqa: E402
from ui_levers import Campaign, first_run  # noqa: E402
from ui_range import try_profile  # noqa: E402

SUFFIX = " (вручную)"
CHANNEL = "https://vkvideo.ru/@donghuaworld"
QUERY = "Расколотая битвой синева небес"
PALETTE_LABEL = {"text": "Текст", "number": "Число", "range": "Диапазон", "whitespace": "Пробел", "any_text": "*"}
ACTION_LABEL = {"exclude": "Исключить видео", "extract_single": "Извлечь номер серии", "assign_voiceover": "Назначить озвучку/тег"}
EXTRACT = {"extract_single", "extract_range", "extract_season"}


# --- what the rules are, read the same way on both sides -----------------------------------------------------------------

def plain(blocks: list[dict]) -> list[dict]:
    return [{key: value for key, value in block.items() if key != "id"} for block in blocks]


def shape(rule: dict) -> dict:
    """A rule without the numbers the database gives: its name, the conditions and the actions with their texts opened."""
    actions = []
    for action in json.loads(rule["action_pattern"] or "[]"):
        pattern = action["action_pattern"]
        actions.append((action["action_type"], plain(json.loads(pattern)) if action["action_type"] in EXTRACT else pattern))
    return {"name": rule["name"], "continue": bool(rule["continue_after_match"]),
            "conditions": [(each["condition_type"], each["logical_operator"], plain(json.loads(each["pattern"])))
                           for each in rule["conditions"]],
            "actions": actions}


# --- the constructor, as the hand works it -----------------------------------------------------------------------------

def block_types(container: Locator) -> list[str]:
    return [re.search(r"block-type-(\w+)", each).group(1).replace("operation", "add")
            for each in container.locator(".pattern-block").evaluate_all("els => els.map(e => e.className)")]


def drag_in(page: Page, editor: Locator, kind: str) -> None:
    """One block from the palette of the editor to the end of its template."""
    container = editor.locator(".pattern-blocks-container")
    before = container.locator(".pattern-block").count()
    palette = editor.locator(f".palette-btn.block-type-{kind}")
    box = container.bounding_box()
    assert box, "template is not on the screen"
    palette.drag_to(container, target_position={"x": max(box["width"] - 6, 2), "y": box["height"] / 2})
    expect(container.locator(".pattern-block")).to_have_count(before + 1, timeout=5000)


def type_into(block: Locator, text: str) -> None:
    field = block.locator(".pattern-block-input")
    field.click()
    field.press("Control+a")
    field.type(text)
    field.press("Enter")  # blur: the value is taken on blur


def build_template(page: Page, editor: Locator, blocks: list[dict]) -> list[str]:
    """The blocks of the template are removed and made again, in order, with the texts the block carries."""
    container = editor.locator(".pattern-blocks-container")
    while container.locator(".pattern-block").count():
        count = container.locator(".pattern-block").count()
        container.locator(".pattern-block-remove").last.click()
        expect(container.locator(".pattern-block")).to_have_count(count - 1, timeout=5000)
    for index, block in enumerate(blocks):
        drag_in(page, editor, block["type"])
        if block.get("value") is not None and block["type"] in ("text", "range"):
            type_into(container.locator(".pattern-block").nth(index), block["value"])
    made = block_types(container)
    assert made == [block["type"] for block in blocks], f"template {made}, wanted {[b['type'] for b in blocks]}"
    return made


def pick(page: Page, select: Locator, label: str) -> None:
    select.click()
    option_named(page, label).click()
    expect(select.locator(".selected-value")).to_have_text(label)


def make_rule(page: Page, wanted: dict) -> None:
    """One rule of `live_three.PROFILES`, entered in the card that "add rule" opens."""
    cards = page.locator(".rule-card")
    before = cards.count()
    page.get_by_role("button", name="Добавить правило").click()
    expect(cards).to_have_count(before + 1)
    card = cards.last
    card.locator(".rule-name-input").fill(wanted["name"])

    conditions = wanted["conditions"]
    for index, condition in enumerate(conditions):
        group = card.locator(".if-block .condition-group").nth(index)
        build_template(page, group.locator(".pattern-constructor"), json.loads(condition["pattern"]))
        if index < len(conditions) - 1:
            group.locator(".btn-add[title='Добавить условие']").click()
            expect(card.locator(".if-block .condition-group")).to_have_count(index + 2)
            wanted_operator = "ИЛИ" if condition["logical_operator"] == "OR" else "И"
            card.locator(".logical-operator-container").nth(index).get_by_role("button", name=wanted_operator, exact=True).click()

    actions = json.loads(wanted["action_pattern"])
    for index, action in enumerate(actions):
        if index:
            card.locator(".then-block .btn-add[title='Добавить действие']").click()
            expect(card.locator(".then-block .condition-group")).to_have_count(index + 1)
        group = card.locator(".then-block .condition-group").nth(index)
        if action["action_type"] != "exclude":
            pick(page, group.locator(".item-select").first, ACTION_LABEL[action["action_type"]])
        if action["action_type"] in EXTRACT:
            build_template(page, group.locator(".pattern-constructor"), json.loads(action["action_pattern"]))
        elif action["action_type"] != "exclude":
            group.locator("input.item-input").fill(action["action_pattern"])
    # The window saves the rule and reads the rules of the profile again: both answers of the server are the signal.
    with page.expect_response(lambda r: r.request.method == "POST" and r.url.endswith("/rules")) as saved, \
            page.expect_response(lambda r: r.request.method == "GET" and r.url.endswith("/rules")):
        card.locator("button[title='Сохранить правило']").click()
    assert saved.value.ok, f"the rule was not saved: {saved.value.status}"
    expect(cards).to_have_count(before + 1)
    expect(page.locator(".parser-loading")).to_have_count(0)


def open_parser(page: Page) -> None:
    page.get_by_role("button", name="Настройки").click(timeout=30000)
    page.locator(".st-tab[title='Фильтры VK']").click()


def make_profile(page: Page, name: str) -> None:
    if not page.locator("#new-profile-name").is_visible():
        page.locator("button.accordion-button").first.click()
    page.locator("#new-profile-name").fill(name)
    page.locator("button.btn-add[title='Создать']").click()
    page.get_by_text(f"Шаг 2: Правила для профиля «{name}»").wait_for(timeout=15000)


def saved_rules(c: Campaign, name: str) -> list[dict]:
    profile = next(each for each in c.api("/api/parser-profiles") if each["name"] == name)
    return c.api(f"/api/parser-profiles/{profile['id']}/rules")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", required=True)
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    verdict = "FAIL прибор"
    results: list[dict] = []
    try:
        with Stand(credentials_from=REAL_DB, work=args.work) as stand, sync_playwright() as pw:
            browser = pw.firefox.launch(headless=True)
            page = browser.new_context(viewport={"width": 1280, "height": 1200}).new_page()
            c = Campaign(stand, Path(args.work), args.safeguard - 15)
            try:
                page.goto(stand.base + "/", wait_until="domcontentloaded")
                first_run(page)
                open_parser(page)
                for key, rules in PROFILES.items():
                    name = key + SUFFIX

                    def build(name=name, rules=rules) -> dict:
                        make_profile(page, name)
                        assert any(each["name"] == name for each in c.api("/api/parser-profiles")), "profile is not in the database"
                        for rule in rules:
                            make_rule(page, rule)
                        return {"rules": len(rules), "cards": page.locator(".rule-card").count()}

                    def check(name=name, rules=rules) -> dict:
                        made = [shape(each) for each in saved_rules(c, name)]
                        wanted = [shape(each) for each in rules]
                        assert made == wanted, f"made {json.dumps(made, ensure_ascii=False)[:600]}\nwanted {json.dumps(wanted, ensure_ascii=False)[:600]}"
                        return {"rules": len(made), "same_as_live_profile": True}

                    c.scenario(f"{key}: профиль и правила собраны в конструкторе руками", page, build)
                    c.scenario(f"{key}: сохранённые правила совпадают с правилами живых прогонов", page, check)

                # The two profiles whose tables are in the database are tried in the test window.
                def window(key: str, url: str, query: str, within: set[int] | None = None, voiceovers: list[str] | None = None) -> dict:
                    found = try_profile(page, key + SUFFIX, url, query)
                    assert found["excluded"] > 0 and found["kept"] > 0, found
                    assert found["episodes"] and found["episodes"][0] >= 208 and found["episodes"][-1] <= 217, found
                    # The channel is searched live and does not always return the same videos: the episodes kept are among those it has.
                    assert within is None or set(found["episodes"]) <= within, found
                    assert voiceovers is None or found["voiceovers"] == voiceovers, found
                    return found

                c.scenario("dunhuatv: окно теста на таблице сайта — диапазон срезал серии до 207", page,
                           lambda: window("dunhuatv", SOURCES[1][0], ""))
                c.scenario("vk: окно теста на таблице канала — серии не ниже 208 с озвучкой AniStar", page,
                           lambda: window("vk", CHANNEL, QUERY, {208, 212, 217}, ["AniStar"]))

                def rename_and_delete() -> dict:
                    if not page.locator(".field-group .item-select").first.is_visible():
                        page.locator("button.accordion-button", has_text="Шаг 1").click()
                    page.locator(".field-group .item-select").first.click()
                    option_named(page, "ruchime" + SUFFIX).click()
                    # Picking a profile opens its rules; the controls of the profile are in Step 1, which the user opens again.
                    page.locator("button.accordion-button", has_text="Шаг 1").click()
                    page.locator("button.btn-edit").click()
                    page.locator(".parser-accordion .accordion-item").first.locator("input.item-input").fill("ruchime — переименован")
                    page.locator("button.btn-confirm").click()
                    page.wait_for_function("() => [...document.querySelectorAll('.selected-value')].some(e => e.innerText.includes('переименован'))",
                                           timeout=15000)
                    assert any(each["name"] == "ruchime — переименован" for each in c.api("/api/parser-profiles"))
                    page.locator("button.btn-delete").first.click()
                    page.get_by_role("button", name="Подтвердить").click()
                    page.wait_for_function("() => ![...document.querySelectorAll('.selected-value')].some(e => e.innerText.includes('переименован'))",
                                           timeout=15000)
                    assert not any(each["name"] == "ruchime — переименован" for each in c.api("/api/parser-profiles"))
                    return {"renamed_then_deleted": True}

                c.scenario("профиль переименован и удалён в Шаге 1", page, rename_and_delete)
            finally:
                results = c.results
                browser.close()
        failed = [each["scenario"] for each in results if not each["ok"]]
        verdict = (f"PASS сценариев {len(results)}" if results and not failed
                   else f"FAIL сценариев {len(results)}, не прошло {len(failed)}: {failed[:4]}")
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        verdict = f"FAIL прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"M-35__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"verdict": verdict, "results": results}, ensure_ascii=False) + "\n")
    print(f"ИЗМЕРЕНИЕ M-35: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
