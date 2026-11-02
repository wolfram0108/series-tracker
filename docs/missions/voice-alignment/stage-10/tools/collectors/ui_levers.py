"""Acceptance of the levers of the output through the interface, on the tables of two sources of one series (the dunhuatv.ru
page and the VK channel of «Расколотая битвой синева небес»; ruchime joins when it answers).

    ui_levers.py reset  --work <dir>                     the series and the groups of voice-overs are taken away (the tables stay)
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
from live_sync import LIVE_STATES, Live, conform_address  # noqa: E402
from live_three import (CUT, EXCLUDE, FIRST, KEEP, LAST, LIBRARY, LOCAL, NUMBER_FIRST, OUT_NUMBER_FIRST, PROFILES as LIVE_PROFILES,
                        REAL_DB, REMOTE, SERIES, SERIES_EN, SOURCES, _extract, _rule, two_phase)  # noqa: E402
from stand import RAW, Stand  # noqa: E402
from ui_journey import ADMIN, PASSWORD, option_named, text_of  # noqa: E402

DUNHUA = SOURCES[1][0]
CHANNEL = "https://vkvideo.ru/@donghuaworld"
QUERY = "Расколотая битвой синева небес"
PROFILE_SITE, PROFILE_VK = "dunhuatv 5", "vk 5, без озвучки"
# The profiles `ui_profiles.py` makes by hand in the constructor; `--profiles hand` takes these instead of the ones made through the API.
HAND_SITE, HAND_VK = "dunhuatv (вручную)", "vk (вручную)"
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
        self.library = work / "library" / SERIES_EN
        self.hand = False
        self.pending: list[str] | None = None

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
        if self.hand:
            missing = {HAND_SITE, HAND_VK} - set(existing)
            assert not missing, f"profiles made by hand are not in the database (run ui_profiles.py first): {sorted(missing)}"
            return existing
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
    option_named(page, name).click()
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
        pick_profile(page, 0, HAND_SITE if c.hand else PROFILE_SITE)
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
        pick_profile(page, 1, HAND_VK if c.hand else PROFILE_VK)
        return {"state": page.locator(".source-card").nth(1).locator(".source-state").inner_text()}

    c.scenario("профиль назначен каналу VK" + ("" if c.hand else " (без озвучки)"), page, profile_vk)

    def series() -> dict:
        page.get_by_label("Название (RU)").fill(SERIES)
        page.get_by_label("Название (EN)").fill(SERIES_EN)
        page.get_by_label("Путь сохранения").fill(str(c.library))
        page.get_by_label("Сезон (формат s01)").fill(f"s0{SEASON}")
        page.locator(".modern-footer").get_by_role("button", name="Добавить", exact=True).click(timeout=15000)
        page.wait_for_selector(".modal-title", state="detached", timeout=60000)
        page.locator(".series-card", has_text=SERIES).first.wait_for(timeout=30000)
        listed = c.api("/api/series")
        c.series_id = next(each["id"] for each in listed if each["name"] == SERIES)
        return {"series_id": c.series_id}

    c.scenario("сериал с двумя источниками создан в окне добавления", page, series)


PLAYER = {"vk": "VK", "cvh": "CVH", "kodik": "Kodik"}
GROUPS = ["AniStar", "Anton Shanteau", "Animy", "Студия Константина Исаева", "AnimeVost"]
# raw name of a voice-over -> the group the user makes of it and whether it is the group's own name
MAKE = [("AniStar", "AniStar"), ("Anton Shanteau", "Anton Shanteau"), ("AniMy / RuChiMe", "Animy"),
        ("Студия Константина Исаева", "Студия Константина Исаева"), ("AnimeVost", "AnimeVost"),
        ("AniStar Многоголосый", "AniStar")]


def open_output(page: Page, base: str | None = None) -> None:
    if base:
        page.goto(base + "/", wait_until="domcontentloaded")
        page.wait_for_selector(".series-card", timeout=30000)
    page.locator(".series-card", has_text=SERIES).first.locator("[title='Статус']").click()
    page.locator(".st-tab[title='Выход']").click()
    page.wait_for_selector(".output-episode .episode-marks li", timeout=60000)


def level_root(page: Page, scope: str, episode: int | None = None):
    if scope == "series":
        return page.locator("section.output-level[data-scope='series']")
    if scope == "season":
        return page.locator(f"section.output-level[data-scope='season'][data-season='{SEASON}']")
    return page.locator(f"article.output-episode[data-season='{SEASON}'][data-episode='{episode}']")


def grid(page: Page, scope: str, episode: int | None = None):
    """The levers of a level, opened: of the series, of its season, or of one episode."""
    root = level_root(page, scope, episode)
    levers = root.locator(":scope > .output-levers")
    if not levers.count():
        (root.locator(".episode-toggle:not(.episode-switch)") if scope == "episode" else root.locator(":scope > header .level-levers")).first.click()
        levers.first.wait_for(timeout=15000)
    return levers.first


def column(page: Page, root, group: str):
    return root.locator(f'.voice-column[data-group="{group}"]')


def variant(page: Page, root, group: str, player: str, source_id: int):
    return column(page, root, group).locator(".variant").filter(
        has=page.locator(".variant-name", has_text=f"{PLAYER[player]} — источник {source_id}"))


def source_ids(c: Campaign) -> dict[str, int]:
    found = {"site": 0, "channel": 0}
    for each in c.api(f"/api/series/{c.series_id}/sources"):
        found["channel" if "vkvideo" in each["url"] else "site"] = each["id"]
    return found


def refs(c: Campaign) -> dict[int, dict]:
    return {each["episode"]: each for each in c.api(f"/api/series/{c.series_id}/output")}


def layout_rows(c: Campaign) -> list[dict]:
    return c.api(f"/api/series/{c.series_id}/composition")["rows"]


def chain_of(c: Campaign, episode: int, group: str) -> list[str] | None:
    """The players of the sources a voice-over of an episode may be had from, in the order they are tried (None: no such voice-over)."""
    found = c.db("SELECT chain FROM voice_dubs WHERE series_id=? AND season=? AND episode=? AND voice_label=?",
                 (c.series_id, SEASON, episode, group))
    return [each["player"] for each in json.loads(found[0]["chain"])] if found else None


def eventually(c: Campaign, check, what: str):
    """The database is read from outside and gives no signal: it is looked at until the check holds or the run's time is out."""
    while True:
        value = check()
        if value:
            return value
        if c.left_ms() <= 1500:
            raise AssertionError(f"not reached: {what}")
        time.sleep(0.25)


def clean(c: Campaign) -> None:
    """A run that comes after another starts from the series as set up: every lever of every level goes back to the level above
    (the names of the voice-overs and the groups are the user's setup and stay)."""
    c.http.post(f"/api/series/{c.series_id}/output/settings/reset", json={}).raise_for_status()
    c.http.post(f"/api/series/{c.series_id}/output/settings/reset", json={"season": SEASON}).raise_for_status()
    for episode in range(FIRST, LAST + 1):
        c.http.post(f"/api/series/{c.series_id}/output/settings/reset", json={"season": SEASON, "episode": episode}).raise_for_status()


def make_groups(c: Campaign, page: Page) -> dict:
    """The groups of the voice-overs, made the way a user makes them: in the column of a voice-over of the output, by its button."""
    made = {}
    for raw, group in MAKE:
        label = next(r["voice_label"] for r in layout_rows(c) if r["voice_name"] == raw)
        root = grid(page, "series")
        box = column(page, root, label)
        if not box.locator(".group-maker-name").count():
            made[raw] = group  # the voice-over is in a group already (the name of the group covers it)
            continue
        box.locator(".group-maker-name").fill(group)
        box.locator(".group-maker-make").click()
        # The column of the new group is there, and the column the voice-over left is gone.
        page.wait_for_function(
            "(a) => { const r = document.querySelector('section.output-level[data-scope=series] > .output-levers');"
            " const has = (g) => r.querySelector('.voice-column[data-group=\"' + g + '\"]');"
            " return has(a.group) && (a.label === a.group || !has(a.label)) }",
            arg={"group": group, "label": label}, timeout=c.left_ms())
        made[raw] = group
    columns = sorted(each.get_attribute("data-group") for each in grid(page, "series").locator(".voice-column").all())
    assert columns == sorted(GROUPS), f"columns {columns}"
    return {"columns": columns}


def levers(c: Campaign, page: Page, context) -> None:
    c.series_id = next(each["id"] for each in c.api("/api/series") if each["name"] == SERIES)
    ids = source_ids(c)
    site, channel = ids["site"], ids["channel"]
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)
    clean(c)

    c.scenario("«Выход» открыт: десять серий 208–217 с референсом, пришедшим событиями", page,
               lambda: (open_output(page), {"episodes": page.locator(".output-episode").count()})[1])

    # --- the names: a name for the voice-over of the channel, the groups made where the voice-overs are shown ---------

    def name_the_channel() -> dict:
        page.locator(".st-tab[title='Свойства']").click()
        card = page.locator(".source-card").filter(has=page.locator(".vk-mode-group"))
        field = card.locator(".source-voice-name input")
        field.fill("AniStar")
        field.press("Tab")
        page.locator(".st-tab[title='Выход']").click()
        eventually(c, lambda: any(r["source_id"] == channel and r["voice_name"] == "AniStar" for r in layout_rows(c)), "the name of the channel")
        labels = {r["voice_label"] for r in layout_rows(c) if r["voice_name"] == "AniStar"}
        assert len(labels) == 1, f"the channel and the CVH voice AniStar are one voice-over: {labels}"
        return {"label": sorted(labels)[0]}

    c.scenario("озвучка канала VK названа в карточке источника «AniStar» и слилась с озвучкой AniStar плеера CVH", page, name_the_channel)

    c.scenario("пять групп озвучек заведены кнопкой «Завести группу» в колонках «Выхода», без настроек", page, lambda: make_groups(c, page))

    def variants_of_groups() -> dict:
        root = grid(page, "series")
        found = {group: [each.inner_text().split("\n")[0] for each in column(page, root, group).locator(".variant-name").all()] for group in GROUPS}
        assert len(found["AniStar"]) == 3, found["AniStar"]
        assert found["Anton Shanteau"] and found["Animy"] and len(found["Студия Константина Исаева"]) == 1 and len(found["AnimeVost"]) == 1, found
        return {"variants": {group: len(names) for group, names in found.items()}, "AniStar": found["AniStar"]}

    c.scenario("в колонке AniStar три варианта (канал VK, CVH, Kodik), в остальных — по своим плеерам", page, variants_of_groups)

    # --- the reference ------------------------------------------------------------------------------------------------

    channel_episodes = sorted({r["episode_start"] for r in layout_rows(c) if r["source_id"] == channel and not r["excluded"]})

    def series_reference() -> dict:
        root = grid(page, "series")
        variant(page, root, "AniStar", "vk", channel).locator(".variant-reference").click()
        others = [e for e in refs(c) if e not in channel_episodes]
        eventually(c, lambda: all(refs(c)[e]["source_id"] == channel for e in channel_episodes)
                   and all(refs(c)[e]["mark"] == "other_player" for e in others), "the reference of the channel and the others by another player")
        now = refs(c)
        assert all(now[e]["voice_group"] == "AniStar" and now[e]["mark"] == "other_player" for e in others), {e: now[e]["mark"] for e in others}
        assert variant(page, root, "AniStar", "vk", channel).locator(".variant-reference").get_attribute("aria-pressed") == "true"
        root.locator(".lever-reference .lever-level", has_text=text_of("output.level.series")).wait_for(timeout=20000)
        return {"channel_episodes": channel_episodes, "others_mark": "other_player", "level_text": text_of("output.level.series")}

    c.scenario("референс сериала: AniStar из канала VK; серии без него берут AniStar другого плеера с отметкой «другой плеер»", page, series_reference)

    def episode_reference() -> dict:
        root = grid(page, "episode", 210)
        variant(page, root, "Animy", "cvh", site).locator(".variant-reference").click()
        page.locator("article.output-episode[data-episode='210'] .episode-marks li", has_text=text_of("output.mark.own")).wait_for(timeout=20000)
        now = refs(c)[210]
        assert now["voice_group"] == "Animy" and now["source_id"] == site and now["mark"] == "own", now
        return {"mark": now["mark"], "group": now["voice_group"], "player": now["player"]}

    c.scenario("референс серии 210: Animy из CVH своей настройкой; отметка «своя настройка серии»", page, episode_reference)

    def second_window() -> dict:
        other = context.new_page()
        open_output(other, c.stand.base)
        marks = other.locator("article.output-episode[data-episode='210'] .episode-marks li").all_inner_texts()
        assert text_of("output.mark.own") in " ".join(marks), marks
        root = grid(page, "episode", 210)
        variant(page, root, "AniStar", "kodik", site).locator(".variant-reference").click()
        other.locator("article.output-episode[data-episode='210'] .episode-marks li", has_text=text_of("output.mark.own")).wait_for(timeout=20000)
        eventually(c, lambda: refs(c)[210]["player"] == "kodik", "the pick of the first window")
        # the other window follows the pick by the event and shows the reference of the episode
        # The window of the second page shows the player of the reference in the head of the episode, by the event.
        other.locator("article.output-episode[data-episode='210'] header .episode-reference", has_text="Kodik").wait_for(timeout=20000)
        other.close()
        return {"player": refs(c)[210]["player"]}

    c.scenario("второе окно видит выбор референса серии 210 событием, без перезагрузки", page, second_window)

    def episode_reset() -> dict:
        root = grid(page, "episode", 210)
        root.locator(".lever-reference .lever-reset").click()
        page.locator("article.output-episode[data-episode='210'] .episode-marks li", has_text=text_of("output.mark.other_player")).wait_for(timeout=20000)
        now = refs(c)[210]
        assert now["mark"] == "other_player" and now["voice_group"] == "AniStar", now
        return {"mark": now["mark"]}

    c.scenario("«как уровнем выше» у серии 210 возвращает референс сериала", page, episode_reset)

    def episode_auto() -> dict:
        root = grid(page, "episode", 208)
        variant(page, root, "Animy", "cvh", site).locator(".variant-reference").click()
        page.locator("article.output-episode[data-episode='208'] .episode-marks li", has_text=text_of("output.mark.own")).wait_for(timeout=20000)
        root.locator(".lever-reference .lever-auto").click()
        eventually(c, lambda: refs(c)[208]["mark"] == "own" and refs(c)[208]["voice_group"] != "Animy", "explicit auto of the episode")
        now = refs(c)[208]
        root.locator(".lever-reference .lever-reset").click()
        eventually(c, lambda: refs(c)[208]["source_id"] == channel and refs(c)[208]["mark"] == "by_level", "reset after auto")
        return {"auto": {"mark": now["mark"], "group": now["voice_group"], "source": now["source_id"]}, "after_reset": refs(c)[208]["mark"]}

    c.scenario("серия 208: выбор варианта, затем «авто» (не наследует сериал), затем «как уровнем выше»", page, episode_auto)

    def season_reference() -> dict:
        root = grid(page, "season")
        variant(page, root, "Anton Shanteau", "kodik", site).locator(".variant-reference").click()
        eventually(c, lambda: refs(c)[209]["voice_group"] == "Anton Shanteau", "the reference of the season")
        now = refs(c)
        assert all(now[e]["voice_group"] == "Anton Shanteau" for e in now if e != 210), {e: now[e]["voice_group"] for e in now}
        assert now[210]["voice_group"] == "Anton Shanteau" and now[210]["mark"] != "own"
        root.locator(".lever-reference .lever-level", has_text=text_of("output.level.season")).wait_for(timeout=20000)
        text = text_of("output.level.season")
        root.locator(".lever-reference .lever-reset").click()
        eventually(c, lambda: refs(c)[209]["voice_group"] == "AniStar", "the season handed back to the series")
        return {"after_pick": "Anton Shanteau во всех сериях сезона", "badge": text}

    c.scenario("референс сезона: Anton Shanteau из Kodik для всех серий; уровень «из сезона»; сброс возвращает сериал", page, season_reference)

    # --- the participation of a variant ---------------------------------------------------------------------------------

    def exclude_series_level() -> dict:
        root = grid(page, "series")
        box = variant(page, root, "AniStar", "vk", channel).locator("input[type=checkbox]")
        box.uncheck()
        eventually(c, lambda: refs(c)[212]["source_id"] != channel, "the excluded channel is no reference")
        now = refs(c)
        assert all(now[e]["source_id"] != channel for e in channel_episodes), {e: now[e]["source_id"] for e in channel_episodes}
        variant(page, root, "AniStar", "vk", channel).locator(".lever-level", has_text=text_of("output.level.series")).wait_for(timeout=20000)
        return {"ep212": {"mark": now[212]["mark"], "player": now[212]["player"]}}

    c.scenario("вариант «AniStar — канал VK» исключён на сериал: он больше не референс, у серий 208/212/217 другой плеер", page, exclude_series_level)

    def dubs_without_excluded() -> dict:
        # Another voice-over is the reference of the season now, so AniStar is a voice-over to be had: from the variants left.
        root = grid(page, "season")
        variant(page, root, "Animy", "cvh", site).locator(".variant-reference").click()
        chain = eventually(c, lambda: chain_of(c, 212, "AniStar"), "the plan of the voice-over AniStar of the episode 212")
        assert "vk" not in chain and set(chain) == {"cvh", "kodik"}, chain
        return {"chain 212 AniStar": chain}

    c.scenario("озвучка AniStar серии 212 планируется только из CVH и Kodik: исключённый вариант в цепочку не входит", page, dubs_without_excluded)

    def allow_episode() -> dict:
        root = grid(page, "episode", 212)
        box = variant(page, root, "AniStar", "vk", channel).locator("input[type=checkbox]")
        box.check()
        chain = eventually(c, lambda: (lambda ch: ch if ch and "vk" in ch else None)(chain_of(c, 212, "AniStar")), "the channel is back for the episode 212")
        assert "vk" not in (chain_of(c, 208, "AniStar") or []), "the episode 208 keeps the exclusion"
        variant(page, root, "AniStar", "vk", channel).locator(".lever-level", has_text=text_of("output.level.episode")).wait_for(timeout=20000)
        badge = text_of("output.level.episode")
        return {"chain 212": chain, "chain 208": chain_of(c, 208, "AniStar"), "badge": badge}

    c.scenario("на серию 212 вариант включён обратно: в её цепочке канал VK снова есть, у серии 208 — нет; уровень «из серии»", page, allow_episode)

    def reset_allow() -> dict:
        root = grid(page, "episode", 212)
        variant(page, root, "AniStar", "vk", channel).locator(".lever-reset").click()
        eventually(c, lambda: "vk" not in (chain_of(c, 212, "AniStar") or ["vk"]), "the episode 212 inherits the exclusion again")
        top = grid(page, "series")
        variant(page, top, "AniStar", "vk", channel).locator(".lever-reset").click()
        eventually(c, lambda: "vk" in (chain_of(c, 208, "AniStar") or []), "the series lets the channel in again")
        return {"chain 208": chain_of(c, 208, "AniStar")}

    c.scenario("сброс у серии 212 возвращает исключение сериала; сброс у сериала возвращает вариант всем", page, reset_allow)

    # --- taking a voice-over and preferring a source ----------------------------------------------------------------------

    def take_off_series() -> dict:
        root = grid(page, "series")
        column(page, root, "AnimeVost").locator(".voice-head input[type=checkbox]").uncheck()
        eventually(c, lambda: chain_of(c, 212, "AnimeVost") is None, "AnimeVost is not planned")
        assert chain_of(c, 208, "AnimeVost") is None
        return {"AnimeVost planned": False}

    c.scenario("«Брать озвучку» у AnimeVost выключено на сериал: её нет в плане ни у одной серии", page, take_off_series)

    def take_on_episode() -> dict:
        root = grid(page, "episode", 212)
        column(page, root, "AnimeVost").locator(".voice-head input[type=checkbox]").check()
        chain = eventually(c, lambda: chain_of(c, 212, "AnimeVost"), "AnimeVost is planned for the episode 212")
        assert chain_of(c, 208, "AnimeVost") is None
        return {"chain 212 AnimeVost": chain, "208": None}

    c.scenario("у серии 212 озвучка AnimeVost включена: она в плане этой серии и только её", page, take_on_episode)

    def prefer_season() -> dict:
        root = grid(page, "season")
        variant(page, root, "Anton Shanteau", "kodik", site).locator(".variant-prefer").click()
        # Anton Shanteau is the reference's voice-over, so the order is read from another episode: give the reference to Animy first
        chain = eventually(c, lambda: (lambda ch: ch if ch and ch[0] == "kodik" else None)(chain_of(c, 212, "Anton Shanteau")), "Kodik is first")
        return {"chain 212 Anton Shanteau": chain}

    c.scenario("предпочтительный источник Anton Shanteau на сезон — Kodik: он первый в цепочке", page, prefer_season)

    def prefer_episode() -> dict:
        root = grid(page, "episode", 212)
        variant(page, root, "Anton Shanteau", "cvh", site).locator(".variant-prefer").click()
        chain = eventually(c, lambda: (lambda ch: ch if ch and ch[0] == "cvh" else None)(chain_of(c, 212, "Anton Shanteau")), "CVH is first")
        other = chain_of(c, 209, "Anton Shanteau")
        column(page, root, "Anton Shanteau").locator(".voice-head .lever-value", has_text="CVH").wait_for(timeout=20000)
        head = column(page, root, "Anton Shanteau").locator(".voice-head .lever-value").inner_text()
        return {"chain 212": chain, "chain 209": other, "head": head}

    c.scenario("на серию 212 предпочтителен CVH: у неё он первый, у остальных серий — Kodik сезона", page, prefer_episode)

    # --- switching off, the filter, the names, the narrow screen ----------------------------------------------------------

    def switch_episode() -> dict:
        card = page.locator("article.output-episode[data-episode='215']")
        card.locator(".episode-switch").click()
        card.locator(".episode-marks li", has_text=text_of("output.mark.off")).wait_for(timeout=20000)
        assert refs(c)[215]["mark"] == "off"
        card.locator(".episode-switch").click()
        card.locator(".episode-marks li", has_text=text_of("output.mark.by_level")).wait_for(timeout=20000)
        return {"off_and_on": 215}

    c.scenario("серия 215 отключена и включена кнопкой; отметка «серия отключена» и возврат референса", page, switch_episode)

    def switch_season() -> dict:
        level = level_root(page, "season")
        level.locator(":scope > header .level-switch").click()
        eventually(c, lambda: all(each["mark"] == "off" for each in refs(c).values()), "the season is off")
        level.locator(":scope > header .level-switch").click()
        eventually(c, lambda: all(each["mark"] != "off" for each in refs(c).values()), "the season is on")
        return {"season": SEASON}

    c.scenario("сезон отключён целиком и включён обратно", page, switch_season)

    def deviations() -> dict:
        box = page.locator(".output-deviations input")
        box.check()
        shown = page.locator(".output-episode").count()
        box.uncheck()
        assert shown == 1, shown
        return {"shown": shown, "of": 10}

    c.scenario("фильтр «только отклонения» показывает серию со своими настройками (212), остальные скрывает", page, deviations)

    def narrow() -> dict:
        page.set_viewport_size({"width": 360, "height": 900})
        scroll = page.evaluate("() => document.documentElement.scrollWidth - window.innerWidth")
        page.set_viewport_size({"width": 1280, "height": 1000})
        assert scroll <= 1, scroll
        return {"overflow_px": scroll}

    c.scenario("при ширине 360 px окно «Выход» с открытыми рычагами не выходит за экран", page, narrow)


DUBS_OF_EPISODE = f"article.output-episode[data-season='{SEASON}'][data-episode='{KEEP}'] .episode-dubs li"
ALIGNED_STATES = ("align_queued", "running", "done")
ENDED_STATES = ("done", "error")


def dub_states(page: Page) -> list[str]:
    """The state of every voice-over of the episode as the window shows it (the class `dub-<state>` of its line)."""
    return page.eval_on_selector_all(DUBS_OF_EPISODE, "els => els.map(e => [...e.classList].find(c => c.startsWith('dub-')).slice(4))")


def sync(c: Campaign, page: Page) -> None:
    """Ж-1 and Ж-9 through the window: the channel is named by the rule of the profile made by hand; the groups, the reference and the
    one episode are set by the buttons; the alignment server is typed in the settings; the button starts the pass."""
    assert c.hand, "sync needs the profiles made by hand (--profiles hand): the channel is named by their rule"
    c.series_id = next(each["id"] for each in c.api("/api/series") if each["name"] == SERIES)
    channel = source_ids(c)["channel"]
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)

    def named_by_the_rule() -> dict:
        open_output(page, c.stand.base)
        page.locator(".st-tab[title='Свойства']").click()
        card = page.locator(".source-card").filter(has=page.locator(".vk-mode-group"))
        typed = card.locator(".source-voice-name input").input_value()
        page.locator(".st-tab[title='Выход']").click()
        page.wait_for_selector(".output-episode .episode-marks li", timeout=60000)
        rows = [r for r in layout_rows(c) if r["source_id"] == channel and not r["excluded"]]
        assert rows and {r["voice_name"] for r in rows} == {"AniStar"} and {r["player"] for r in rows} == {"vk"}, rows[:2]
        assert typed == "", f"the name was typed in the card, not given by the rule: {typed!r}"
        return {"rows": len(rows), "name_in_card": typed, "voice": "AniStar", "player": "vk"}

    c.scenario("Ж-1: озвучка канала VK названа правилом профиля, собранного руками; в карточке источника имя не набрано", page, named_by_the_rule)
    c.scenario("Ж-6: пять групп озвучек заведены кнопкой в колонках «Выхода»", page, lambda: make_groups(c, page))

    def reference() -> dict:
        variant(page, grid(page, "series"), "AniStar", "vk", channel).locator(".variant-reference").click()
        now = eventually(c, lambda: (lambda r: r if r[KEEP]["source_id"] == channel and r[KEEP]["voice_group"] == "AniStar" else None)(refs(c)),
                         "the reference of the channel")
        return {"episode": KEEP, "player": now[KEEP]["player"], "height": now[KEEP]["height"], "mark": now[KEEP]["mark"]}

    c.scenario("референс сериала: AniStar из канала VK", page, reference)

    def one_episode() -> dict:
        for episode in sorted(refs(c)):
            if episode == KEEP or refs(c)[episode]["mark"] == "off":
                continue
            card = page.locator(f"article.output-episode[data-episode='{episode}']")
            card.locator(".episode-switch").click()
            card.locator(".episode-marks li", has_text=text_of("output.mark.off")).wait_for(timeout=20000)
        on = sorted(e for e, each in refs(c).items() if each["mark"] != "off")
        assert on == [KEEP], f"episodes on: {on}"
        return {"on": on}

    c.scenario(f"все серии кроме {KEEP} отключены кнопкой серии", page, one_episode)

    def conform_server() -> dict:
        before = c.api("/api/auth")["vk"]["has_password"]
        page.goto(c.stand.base + "/", wait_until="domcontentloaded")
        page.wait_for_selector(".series-card", timeout=30000)
        page.get_by_role("button", name="Настройки").click(timeout=30000)
        page.get_by_label(text_of("conform.settings.url")).fill(conform_address())
        page.get_by_label(text_of("conform.settings.local")).fill(LOCAL)
        page.get_by_label(text_of("conform.settings.daemon")).fill(REMOTE)
        page.get_by_role("button", name="Сохранить").click()
        page.locator(".conform-status .pill-success").wait_for(timeout=60000)
        saved = c.api("/api/conform/settings")
        assert saved["work_dir"] == LOCAL and saved["work_dir_remote"] == REMOTE and saved["url"], saved
        after = c.api("/api/auth")["vk"]["has_password"]
        assert before and after, f"the saving of the tab lost the password of VK: {before} -> {after}"
        page.get_by_role("button", name="Закрыть").click()
        return {"daemon": "up", "work_dir": saved["work_dir"], "vk_password_kept": after}

    c.scenario("сервер выравнивания набран в настройках, сохранён кнопкой; демон отвечает; пароль VK цел", page, conform_server)

    def press() -> dict:
        open_output(page, c.stand.base)
        page.get_by_role("button", name=text_of("output.sync")).click(timeout=20000)
        page.wait_for_function(f"() => document.querySelector(\"{DUBS_OF_EPISODE}\")", timeout=c.left_ms())
        return {"dubs": dub_states(page)}

    c.scenario("кнопка «Синхронизировать»: озвучки серии появились в окне", page, press)

    def aligned() -> dict:
        page.wait_for_function(
            "(s) => [...document.querySelectorAll(s.dubs)].some(e => s.states.some(n => e.classList.contains('dub-' + n)))",
            arg={"dubs": DUBS_OF_EPISODE, "states": list(ALIGNED_STATES)}, timeout=c.left_ms())
        return {"dubs": dub_states(page)}

    c.scenario("загрузка дошла до выравнивания: в окне озвучка в очереди, в работе или выполнена", page, aligned)


def again(c: Campaign, page: Page) -> None:
    """The voice-overs of the episode that ended in error are taken again as a user takes them: «брать озвучку» is switched off for
    the episode and on, then the button of the pass is pressed. Whatever else was placed stays as it is."""
    c.series_id = next(each["id"] for each in c.api("/api/series") if each["name"] == SERIES)
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)
    labels = [row["voice_label"] for row in c.db(
        "SELECT voice_label FROM voice_dubs WHERE series_id=? AND season=? AND episode=? AND status IN ('rejected', 'failed')",
        (c.series_id, SEASON, KEEP))]
    assert labels, "no voice-over of the episode ended in error"

    def off_and_on() -> dict:
        open_output(page, c.stand.base)
        root = grid(page, "episode", KEEP)
        for label in labels:
            box = column(page, root, label).locator(".voice-head input[type=checkbox]")
            line = page.locator(DUBS_OF_EPISODE, has=page.locator("span", has_text=label))
            box.uncheck()
            page.wait_for_function("(s) => ![...document.querySelectorAll(s.dubs)].some(e => e.innerText.includes(s.label))",
                                   arg={"dubs": DUBS_OF_EPISODE, "label": label}, timeout=c.left_ms())
            box.check()
            line.first.wait_for(timeout=c.left_ms())
        return {"taken_again": labels}

    c.scenario("озвучки с ошибкой выключены у серии и включены снова: строки ушли из плана и вернулись", page, off_and_on)

    def press() -> dict:
        page.get_by_role("button", name=text_of("output.sync")).click(timeout=20000)
        page.wait_for_function(
            "(s) => [...document.querySelectorAll(s.dubs)].some(e => s.labels.some(l => e.innerText.includes(l)) && !e.classList.contains('dub-planned') && !e.classList.contains('dub-error'))",
            arg={"dubs": DUBS_OF_EPISODE, "labels": labels}, timeout=c.left_ms())
        return {"dubs": dub_states(page)}

    c.scenario("кнопка «Синхронизировать»: взятые заново озвучки пошли в работу", page, press)


def finish(c: Campaign, page: Page, live: Live) -> None:
    """The end of Ж-9: a new stand on the same database picks the work up; the window shows every voice-over ended; the files
    lie beside the reference and the pairs of the service are judged. Not ended in the time of the run: the run says so and is repeated."""
    c.series_id = next(each["id"] for each in c.api("/api/series") if each["name"] == SERIES)
    page.goto(c.stand.base + "/", wait_until="domcontentloaded")
    first_run(page)
    open_output(page)

    def ended() -> dict:
        try:
            page.wait_for_function(
                "(s) => { const l = [...document.querySelectorAll(s.dubs)];"
                " return l.length > 0 && l.every(e => s.states.some(n => e.classList.contains('dub-' + n))) }",
                arg={"dubs": DUBS_OF_EPISODE, "states": list(ENDED_STATES)}, timeout=c.left_ms())
        except Exception:  # noqa: BLE001 - time out is the answer "still going", anything else is the result of the scenario
            c.pending = dub_states(page)
            raise
        states = dub_states(page)
        assert all(state == "done" for state in states), states
        return {"dubs": states}

    if not c.scenario("все озвучки серии закончили в окне: выполнены", page, ended):
        return
    c.scenario("файлы рядом с референсом, пары сервиса выравнивания приняты", page, lambda: {"verdict": _passed(two_phase(live))})


def _passed(verdict: str) -> str:
    assert verdict.startswith("PASS"), verdict
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("reset", "setup", "levers", "sync", "again", "finish"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--profiles", choices=("api", "hand"), default="api",
                        help="hand: the profiles made in the constructor by ui_profiles.py (the channel is named by their rule)")
    parser.add_argument("--on-nas", action="store_true", help="the library of the series is the folder the alignment service sees")
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
            campaign.hand = args.profiles == "hand"
            if args.on_nas:
                campaign.library = Path(LIBRARY)
            try:
                if args.phase == "reset":
                    for each in campaign.api("/api/series"):
                        campaign.http.delete(f"/api/series/{each['id']}").raise_for_status()
                    view = campaign.api("/api/voices/settings")
                    saved = campaign.http.put("/api/voices/settings", json={"priority": view["priority"], "voice_groups": {}}).json()
                    assert saved.get("saved") is True, saved
                    campaign.results.append({"scenario": "сериал и группы озвучек сняты", "ok": True, "detail": {}, "shot": None, "seconds": 0})
                elif args.phase == "setup":
                    setup(campaign, page)
                elif args.phase == "sync":
                    sync(campaign, page)
                elif args.phase == "again":
                    again(campaign, page)
                elif args.phase == "finish":
                    live = Live(stand, campaign.deadline)
                    live.series_name, live.work = SERIES, args.work
                    finish(campaign, page, live)
                else:
                    levers(campaign, page, context)  # noqa: F821
            finally:
                results = campaign.results
                browser.close()
        failed = [each["scenario"] for each in results if not each["ok"]]
        verdict = (f"PASS сценариев {len(results)}" if results and not failed
                   else f"FAIL сценариев {len(results)}, не прошло {len(failed)}: {failed[:4]}")
        if failed and campaign.pending:
            verdict = f"ЕЩЁ ИДЁТ озвучки {campaign.pending}: прогон повторяется на той же базе"
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        verdict = f"FAIL прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"M-33__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"phase": args.phase, "verdict": verdict, "results": results}, ensure_ascii=False) + "\n")
    print(f"ИЗМЕРЕНИЕ M-33: {verdict}")
    return 0 if verdict.startswith("PASS") else 2 if verdict.startswith("ЕЩЁ") else 1


if __name__ == "__main__":
    sys.exit(main())
