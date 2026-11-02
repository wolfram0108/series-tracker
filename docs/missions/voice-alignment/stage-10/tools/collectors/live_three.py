"""M-24 and M-25 on three sources of one series, the last episodes only (the series «Расколотая битвой синева небес 5»).

    live_three.py read  --work <dir> [--safeguard 590]   the tables of the three sources are read to their end and kept
    live_three.py build --work <dir> [--safeguard 170]   the series, the profiles, the groups, the reference, one episode
    live_three.py two   --work <dir> [--safeguard 170]   a new stand on the same database waits for the service and checks the files

The tables are read once (the table of a page keeps for an hour), so the later phases do not read the sites again while
the hour lasts. The work folder keeps the database of the stand between the phases. The last line printed in any outcome is
`ИЗМЕРЕНИЕ M-24: ...` (read and build) or `ИЗМЕРЕНИЕ M-25: ...` (two). Run it only through `progon_runner.py`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_sync import Live, LIVE_STATES, conform_address, states  # noqa: E402
from stand import RAW, Stand  # noqa: E402

REAL_DB = "/home/user/series-tracker/app.db"
SERIES = "Расколотая битвой синева небес 5"
SERIES_EN = "Raskolotaia Bitva Sineva Nebes 5"
LOCAL = "/nas/torrents/track-muxer/stage10-live3"
REMOTE = "/mnt/meganas/media/torrents/track-muxer/stage10-live3"
LIBRARY = f"{LOCAL}/library/{SERIES_EN}"
CUT = "1-207"  # the range filter: the episodes up to 207 are out, 208-217 stay
FIRST, LAST = 208, 217
KEEP = 212  # the channel has this episode in 2160p, and all five voice-overs have a row for it
TEXT_FIRST = [{"type": "text", "value": "Серия"}, {"type": "whitespace"}, {"type": "number"}]
NUMBER_FIRST = [{"type": "number"}, {"type": "whitespace"}, {"type": "text", "value": "серия"}]
OUT_TEXT_FIRST = [*TEXT_FIRST, {"type": "range", "value": CUT}]
OUT_NUMBER_FIRST = [{"type": "number"}, {"type": "range", "value": CUT}, {"type": "whitespace"}, {"type": "text", "value": "серия"}]
# The name of a group stands for itself (case, spaces and signs aside), so only the other names are triggers.
GROUPS = {
    "AniStar": ["AniStar Многоголосый"],
    "Animy": ["Ruchime", "AniMy / RuChiMe"],
    "Anton Shanteau": [],
    "Студия Константина Исаева": [],
    "AnimeVost": [],
}
WANTED_VOICES = {"AniStar", "Animy", "Anton Shanteau", "Студия Константина Исаева", "AnimeVost"}


def _rule(name: str, conditions: list[list[dict]], actions: list[dict]) -> dict:
    """A rule as the editor sends it: the actions and the blocks of the conditions are JSON texts."""
    return {"name": name, "action_pattern": json.dumps(actions), "continue_after_match": False,
            "conditions": [{"condition_type": "contains", "pattern": json.dumps(blocks),
                            "logical_operator": "OR" if index < len(conditions) - 1 else "AND"}
                           for index, blocks in enumerate(conditions)]}


def _extract(blocks: list[dict]) -> dict:
    return {"action_type": "extract_single", "action_pattern": json.dumps(blocks)}


EXCLUDE = [{"action_type": "exclude", "action_pattern": "[]"}]
PROFILES = {
    "ruchime": [_rule("Исключить до 207", [OUT_TEXT_FIRST, OUT_NUMBER_FIRST], EXCLUDE),
                _rule("Серия N", [TEXT_FIRST], [_extract(TEXT_FIRST)]),
                _rule("N серия", [NUMBER_FIRST], [_extract(NUMBER_FIRST)])],
    "dunhuatv": [_rule("Исключить до 207", [OUT_NUMBER_FIRST], EXCLUDE),
                 _rule("N серия", [NUMBER_FIRST], [_extract(NUMBER_FIRST)])],
    "vk": [_rule("Исключить до 207", [OUT_NUMBER_FIRST], EXCLUDE),
           _rule("N серия, озвучка AniStar", [NUMBER_FIRST], [_extract(NUMBER_FIRST),
                                                              {"action_type": "assign_voiceover", "action_pattern": "AniStar"}])],
}
NAMES = ["ruchime", "dunhuatv", "vk"]
SOURCES = [
    ("https://1.ruchime.org/release/raskolotaia-bitvoi-sineva-nebes-5", {}),
    ("https://dunhuatv.ru/24-raskolotaja-bitvoj-sineva-nebes-5-smotret-onlajn.html", {}),
    ("https://vkvideo.ru/@donghuaworld|Расколотая битвой синева небес", {"vk_search_mode": "search"}),
]


def read_phase(work: str, safeguard: float) -> str:
    verdict, report = "FAIL прибор", []
    with Stand(credentials_from=REAL_DB, work=work) as stand:
        seconds = safeguard - 40
        client = httpx.Client(base_url=stand.base, timeout=60)
        ended: dict[str, str] = {}

        def match(name: str, data: dict) -> bool:
            if name in ("sources_table_ready", "sources_table_failed"):
                ended[data["url"]] = name
            return len(ended) >= len(SOURCES)

        found, finished = stand.listen({"sources_table_ready", "sources_table_failed"}, match, seconds)
        began = time.monotonic()
        canonical = {}
        for url, options in SOURCES:
            answer = client.post("/api/sources/preview", json={"url": url, "options": options, "refresh": True}).json()
            canonical[url] = answer["url"]
            print(f"  начато чтение {answer['url']} state={answer['state']}", flush=True)
        finished.wait(seconds)
        elapsed = round(time.monotonic() - began, 1)
        for index, (url, options) in enumerate(SOURCES):
            final = client.post("/api/sources/preview", json={"url": url, "options": options, "refresh": False}).json()
            lines = final.get("lines") or []
            Path(work, f"lines_{index}.json").write_text(json.dumps(lines, ensure_ascii=False), encoding="utf-8")
            players = (final.get("summary") or {}).get("players") or []
            report.append({"url": url, "state": final.get("state"), "reason": final.get("reason"), "lines": len(lines),
                           "ended_by": ended.get(canonical[url]),
                           "players": [{"player": p["player"], "rows": p["rows"], "heights": p["heights"][-3:],
                                        "voices": [v["name"] for v in p.get("voices", [])]} for p in players]})
            print("  ", json.dumps(report[-1], ensure_ascii=False)[:900], flush=True)
        ready = all(item["state"] == "ready" and item["lines"] for item in report)
        verdict = (f"PASS таблицы источников прочитаны за {elapsed} с: {[item['lines'] for item in report]} строк"
                   if ready else f"FAIL чтение за {elapsed} с: {[(i['state'], i['lines']) for i in report]}")
    return verdict


def drop_unnamed(live: Live) -> list[str]:
    """The voice-overs nobody named a group for (a row with no name, a voice of other episodes) are not taken: the lever
    `voice.take` of their group is off for the whole series, as a user turns them off."""
    layout = live.call("GET", "/api/series/1/composition")
    labels = sorted({row["voice_label"] for row in layout["rows"] if not row.get("excluded")} - WANTED_VOICES)
    for label in labels:
        live.call("PUT", "/api/series/1/output/settings", json={"lever": "voice.take", "target": f"group:{label}", "value": False})
    return labels


def create_series(live: Live) -> None:
    view = live.call("GET", "/api/voices/settings")
    answer = live.call("PUT", "/api/voices/settings", json={"priority": view["priority"], "voice_groups": GROUPS})
    assert answer.get("saved") is True, f"voice groups: {answer}"
    live.step("группы озвучек заведены", groups=list(GROUPS))

    ids: dict[str, int] = {}
    for name, rules in PROFILES.items():
        made = live.call("POST", "/api/parser-profiles", json={"name": f"{name} 5"})
        ids[name] = made["id"]
        for rule in rules:
            live.call("POST", f"/api/parser-profiles/{ids[name]}/rules", json=rule)
    live.step("профили созданы, правила записаны", profiles=ids)

    for (url, options), name in zip(SOURCES, NAMES):
        tested = live.call("POST", "/api/parser-profiles/test", json={
            "url": url, "options": options, "profile_id": ids[name], "refresh": False})
        lines = tested["lines"]
        excluded = [each for each in lines if (each.get("result") or {}).get("excluded")]
        episodes = sorted({each["result"]["episode"] for each in lines
                           if each.get("result") and not each["result"].get("excluded") and each["result"].get("episode") is not None})
        live.step(f"диапазон {CUT} у источника {name}", lines=len(lines), excluded=len(excluded),
                  kept_episodes=episodes[:3] + ["…"] + episodes[-3:], kept_count=len(episodes))
        assert excluded, f"{name}: the range filter excluded nothing"
        assert episodes and min(episodes) >= FIRST and max(episodes) <= LAST, f"{name}: episodes {episodes}"

    seen: set[int] = set()
    watching = live.stand.listen({"reference_changed"}, lambda n, d: (seen.add(int(d["episode"])) or len(seen) >= LAST - FIRST + 1),
                                 min(90.0, live.left() - 60))
    live.call("POST", "/api/series", json={
        "name": SERIES, "name_en": SERIES_EN, "site": "1.ruchime.org", "save_path": LIBRARY, "season": "s05",
        "sources": [{"url": url, "source_type": "web_video", "parser_profile_id": ids[name],
                     "vk_search_mode": options.get("vk_search_mode", "search"), "vibix_season": None, "players_off": []}
                    for (url, options), name in zip(SOURCES, NAMES)]})
    watching[1].wait(100)
    live.step("сериал создан из трёх источников; референсы пришли событиями", episodes=sorted(seen))
    assert min(seen) >= FIRST and max(seen) <= LAST, f"episodes {sorted(seen)}"


def aim(live: Live) -> str:
    """Everything that depends on the episode taken: the groups, the one episode on, the reference of the channel, the voice-overs
    nobody named off; then the pass. Every step is the same when it is done again, so the phase may be run on the same database."""
    view = live.call("GET", "/api/voices/settings")
    if view["voice_groups"] != GROUPS:
        # New groups name voice-overs in the layout only after it is counted again: its event is waited for.
        rev = live.call("GET", "/api/series/1/composition")["rev"]
        recount = live.stand.listen({"composition_changed"}, lambda name, data: data.get("rev", 0) > rev, 40)
        answer = live.call("PUT", "/api/voices/settings", json={"priority": view["priority"], "voice_groups": GROUPS})
        assert answer.get("saved") is True, f"voice groups: {answer}"
        assert recount[1].wait(40), "the layout was not counted again after the groups"
        live.step("группы озвучек изменены, раскладка пересчитана", groups=list(GROUPS))
    layout = live.call("GET", "/api/series/1/composition")
    labels = sorted({row["voice_label"] for row in layout["rows"] if row.get("episode_start") == KEEP and not row.get("excluded")})
    live.step(f"озвучки серии {KEEP} в раскладке (имя группы или неизвестная)", labels=labels)
    assert WANTED_VOICES <= set(labels), f"voices {labels}"

    for episode in range(FIRST, LAST + 1):
        live.call("PUT", "/api/series/1/output/switch", json={"season": 5, "episode": episode, "off": episode != KEEP})
    sources = live.call("GET", "/api/series/1/sources")
    channel_id = next(each["id"] for each in sources if "vkvideo.ru" in each["url"])
    live.call("PUT", "/api/series/1/output/settings", json={
        "lever": "reference.choice", "target": None, "value": {"group": "AniStar", "source_id": channel_id, "player": "vk"}})
    output = live.call("GET", "/api/series/1/output")
    wanted = [each for each in output if each["mark"] != "off"]
    assert [each["episode"] for each in wanted] == [KEEP], f"wanted {[each['episode'] for each in wanted]}"
    reference = wanted[0]
    live.step("референс из канала VK выбран рычагом", mark=reference["mark"], group=reference["voice_group"],
              player=reference["player"], height=reference["height"], source_is_channel=reference["source_id"] == channel_id)
    assert reference["source_id"] == channel_id and reference["voice_group"] == "AniStar", f"reference {reference}"
    live.step("безымянные озвучки отключены рычагом «брать озвучку»", dropped=drop_unnamed(live))

    live.call("POST", "/api/series/1/scan", timeout=live.left() - 30)
    live.step("проход по сериалу выполнен (загрузка поставлена)")
    live.watch({"voice_dub_update", "voice_dubs_snapshot", "conform_queue_update"},
               lambda name, data: name == "voice_dub_update" and data.get("episode") == KEEP and data.get("state") in LIVE_STATES,
               live.left() - 10)
    dubs = mine(live)
    voices = {dub["voice_label"] for dub in dubs}
    live.step("озвучки", voices=sorted(voices), states=states(dubs))
    ok = voices == WANTED_VOICES - {"AniStar"} and any(dub["state"] in LIVE_STATES for dub in dubs)
    return (f"PASS три источника, диапазон {CUT}, референс AniStar из канала, озвучек {len(dubs)}: {states(dubs)}" if ok
            else f"FAIL озвучки {sorted(voices)}: {states(dubs)}")


def build_phase(live: Live) -> str:
    address = conform_address()
    saved = live.call("PUT", "/api/conform/settings", json={"url": address, "work_dir": LOCAL, "work_dir_remote": REMOTE})
    assert saved.get("saved") is True, f"conform settings: {saved.get('problems')}"
    if live.call("GET", "/api/series"):
        live.step("сериал уже есть в базе стенда: серия и озвучки нацеливаются заново")
    else:
        create_series(live)
    return aim(live)


def pairs_of_jobs(work: str) -> list[dict]:
    """What the service says of each pair of the jobs of this series: read-only asks, the ids from the database of the stand."""
    import sqlite3
    ids = sorted({row[0] for row in sqlite3.connect(f"file:{work}/app.db?mode=ro", uri=True).execute(
        "SELECT job_id FROM voice_dubs WHERE episode=? AND status != 'removed' AND job_id IS NOT NULL", (KEEP,))})
    address = conform_address()
    pairs = []
    for job_id in ids:
        job = httpx.get(f"{address}/conform/jobs/{job_id}", timeout=30).json()
        for result in job.get("results", []):
            pairs.append({"job": job_id, "elapsed_s": round(job.get("elapsed_s") or 0), **{
                key: result.get(key) for key in ("dub", "ok", "suspect", "critical", "error", "audio_resid_ms",
                                                 "assigned_pct", "dropped_intro_s") if key in result}})
    return pairs


def mine(live: Live) -> list[dict]:
    """The voice-overs of the episode in work: the rows of episodes switched off and the removed ones are history."""
    return [dub for dub in live.dubs() if dub.get("status") != "removed" and dub["episode"] == KEEP]


def two_phase(live: Live) -> str:
    ended = {"done", "error"}
    live.step("новый стенд на той же базе: работа подхвачена из базы", dubs=states(mine(live)))
    while live.left() > 30:
        dubs = mine(live)
        if dubs and all(dub["state"] in ended for dub in dubs):
            break
        live.watch({"voice_dub_update", "voice_dubs_snapshot", "conform_queue_update"},
                   lambda name, data: name == "voice_dub_update" and data.get("episode") == KEEP and data.get("state") in ended,
                   min(60.0, live.left() - 30))
    dubs = mine(live)
    count = states(dubs)
    live.step("озвучки после ожидания", states=count)
    if not dubs or any(dub["state"] not in ended for dub in dubs):
        return f"FAIL ещё идёт: {count}"
    library = Path(LIBRARY)
    files = sorted(str(path.relative_to(library)) for path in library.rglob("*") if path.is_file()) if library.exists() else []
    aligned = [name for name in files if name.endswith((".flac", ".mka", ".ac3", ".aac"))]
    temp = sorted(str(path.relative_to(LOCAL)) for path in Path(LOCAL).glob("downloads/**/*") if path.is_file())
    live.step("файлы рядом с референсом", files=files[:12], aligned=len(aligned), temporary_left=temp[:5])
    pairs = pairs_of_jobs(live.work)
    live.step("пары сервиса выравнивания", pairs=pairs)
    suspect = [pair["dub"] for pair in pairs if pair.get("suspect")]
    critical = [pair["dub"] for pair in pairs if pair.get("critical")]
    failed = [pair["dub"] for pair in pairs if not pair.get("ok")]
    done = count.get("done", 0)
    # The verdict of a pair is the service's `ok`; `suspect` and `critical` tell what it found and put right.
    ok = done == len(dubs) and len(aligned) == len(dubs) and not temp and not failed
    detail = (f"озвучек {done}, дорожек рядом с референсом {len(aligned)}, во временной папке {len(temp)}, пары: не ok {len(failed)}; "
              f"сообщения сервиса: suspect {len(suspect)}, critical {len(critical)}")
    return f"PASS {detail}" if ok else f"FAIL {detail}"


def run_live(phase, work: str, safeguard: float) -> str:
    verdict = "FAIL прибор"
    with Stand(credentials_from=REAL_DB, work=work) as stand:
        live = Live(stand, time.monotonic() + safeguard - 15)
        live.work = work
        try:
            verdict = phase(live)
        except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
            verdict = f"FAIL {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
            traceback.print_exc()
    return verdict


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("read", "build", "two"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--safeguard", type=float, default=170)
    parser.add_argument("--skip", default="", help="names of the sources left out (ruchime, dunhuatv, vk), comma separated")
    args = parser.parse_args()
    skipped = {name for name in args.skip.split(",") if name}
    SOURCES[:] = [source for source, name in zip(SOURCES, NAMES) if name not in skipped]
    NAMES[:] = [name for name in NAMES if name not in skipped]
    verdict = "FAIL прибор"
    try:
        if args.phase == "read":
            verdict = read_phase(args.work, args.safeguard)
        else:
            verdict = run_live(build_phase if args.phase == "build" else two_phase, args.work, args.safeguard)
    except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
        verdict = f"FAIL {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    ident = "M-25" if args.phase == "two" else "M-24"
    with open(RAW / f"{ident}__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"scenario": f"{len(SOURCES)}-sources", "phase": args.phase, "verdict": verdict}, ensure_ascii=False) + "\n")
    print(f"ИЗМЕРЕНИЕ {ident}: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
