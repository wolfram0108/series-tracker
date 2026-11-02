"""M-24 and M-25 on live sites and the live alignment service: one series, one episode, in two phases.

    live_sync.py one --work <dir> [--safeguard 170]   the series is made, all episodes but one are switched off, the pass is
                                                      asked for; the run ends when a voice-over has gone to the service
    live_sync.py two --work <dir> [--safeguard 170]   a new stand on the same database waits for the service and checks what
                                                      lies next to the reference (run again while it says `ещё идёт`)

The work folder keeps the database of the stand between the phases; the second phase is also the live part of M-26 (the
job of the service is taken up again from the database by a new process). The folder the service reads and writes is
`/nas/torrents/track-muxer/stage10-live` (as the service names it: `/mnt/meganas/media/...`). The address of the service is
`ST_CONFORM_URL` of the `.env` of the project; it is not printed. Nothing of the service is deleted here.

The last line printed in any outcome is `ИЗМЕРЕНИЕ M-24: PASS|FAIL ...` (phase one) or `ИЗМЕРЕНИЕ M-25: PASS|FAIL ...` (phase
two); a phase two that finds the work not ended says `FAIL ещё идёт` and is run again on the same folder. Run it only through
`progon_runner.py`.
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from stand import RAW, ROOT, Stand  # noqa: E402
from verdicts import judge_alignment  # noqa: E402

URL = "https://dunhuatv.ru/500-stat-pobeditelem.html"
NAME, NAME_EN = "Стать победителем", "Stat Pobeditelem"
LOCAL = "/nas/torrents/track-muxer/stage10-live"
REMOTE = "/mnt/meganas/media/torrents/track-muxer/stage10-live"
LIBRARY = f"{LOCAL}/library/{NAME_EN}"
EPISODES = 15
KEEP = 1
LIVE_STATES = {"align_queued", "running", "done"}
PROFILES = [{"name": "общий", "rules": [{
    "name": "серия", "conditions": [[{"type": "text", "value": "серия"}]],
    "actions": [{"action_type": "extract_single", "action_pattern": json.dumps(
        [{"type": "number"}, {"type": "whitespace"}, {"type": "text", "value": "серия"}])}]}]}]


def conform_address() -> str:
    for line in (ROOT.parents[2] / ".env").read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() == "ST_CONFORM_URL":
            return value.strip().strip('"').strip("'")
    raise RuntimeError("ST_CONFORM_URL is not in the .env of the project")


def pairs_of_jobs(work: str, episode: int) -> list[dict]:
    """What the service says of each pair of the jobs of the episode: read-only asks, the jobs named by the database of the stand."""
    with sqlite3.connect(f"file:{work}/app.db?mode=ro", uri=True) as conn:
        ids = sorted({row[0] for row in conn.execute(
            "SELECT job_id FROM voice_dubs WHERE episode=? AND job_id IS NOT NULL", (episode,))})
    address = conform_address()
    pairs = []
    for job_id in ids:
        job = httpx.get(f"{address}/conform/jobs/{job_id}", timeout=30).json()
        for result in job.get("results", []):
            pairs.append({"job": job_id, "elapsed_s": round(job.get("elapsed_s") or 0), **{
                key: result.get(key) for key in ("dub", "ok", "suspect", "critical", "error", "audio_resid_ms",
                                                 "assigned_pct", "dropped_intro_s") if key in result}})
    return pairs


def outputs_of(work: str, episode: int) -> dict[str, str]:
    """{label of the voice-over: the path of its track} for the voice-overs of the episode that are placed, as the database holds them."""
    with sqlite3.connect(f"file:{work}/app.db?mode=ro", uri=True) as conn:
        return dict(conn.execute("SELECT voice_label, output_path FROM voice_dubs WHERE episode=? AND status='placed' "
                                 "AND output_path IS NOT NULL", (episode,)).fetchall())


def reference_beside(library: Path, outputs: dict[str, str]) -> Path | None:
    """The video the tracks lie beside: the one file of the library whose name begins the name of every track."""
    names = [Path(path).name for path in outputs.values()]
    found = [path for path in library.rglob("*.mp4") if names and all(name.startswith(path.stem) for name in names)] if library.exists() else []
    return found[0] if len(found) == 1 else None


class Live:
    def __init__(self, stand: Stand, deadline: float) -> None:
        self.stand, self.deadline = stand, deadline
        self.client = httpx.Client(base_url=stand.base, timeout=60)
        self.steps: list[dict] = []
        self.began = time.monotonic()
        self.events: dict[str, dict] = {}
        self.series_name: str | None = None

    def series_path(self) -> str:
        """The series is found on the stand, not assumed to be number 1: a database that held other series numbers new ones on."""
        found = [each["id"] for each in self.call("GET", "/api/series") if self.series_name in (None, each.get("name"))]
        assert len(found) == 1, f"series {self.series_name!r} in the stand: {found}"
        return f"/api/series/{found[0]}"

    def left(self) -> float:
        return max(1.0, self.deadline - time.monotonic())

    def step(self, name: str, **facts) -> None:
        self.steps.append({"step": name, "t": round(time.monotonic() - self.began, 1), **facts})
        print(f"  {name} {facts or ''}", flush=True)

    def call(self, method: str, path: str, **kwargs):
        answer = self.client.request(method, path, **kwargs)
        if answer.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {answer.status_code} {answer.text[:200]}")
        return answer.json() if answer.content else None

    def dubs(self) -> list[dict]:
        return self.call("GET", f"{self.series_path()}/voices").get("items", [])

    def watch(self, names: set[str], match, seconds: float):
        """Events of the stream until `match` says so or the seconds are out; the last of each name is kept."""
        found, done = self.stand.listen(names, match, seconds)
        done.wait(seconds + 2)
        for name, data in found:
            self.events[name] = data
        return found


def states(dubs: list[dict]) -> dict[str, int]:
    count: dict[str, int] = {}
    for dub in dubs:
        count[dub["state"]] = count.get(dub["state"], 0) + 1
    return count


def phase_one(live: Live) -> str:
    address = conform_address()
    saved = live.call("PUT", "/api/conform/settings", json={"url": address, "work_dir": LOCAL, "work_dir_remote": REMOTE})
    assert saved.get("saved") is True, f"conform settings: {saved.get('problems')}"
    live.step("настройки сервиса выравнивания приняты", work_dir=LOCAL)
    if live.call("GET", "/api/series"):
        live.step("сериал уже есть в базе стенда: ждём работу дальше")
        live.watch({"voice_dub_update", "voice_dubs_snapshot", "conform_queue_update"},
                   lambda name, data: name == "voice_dub_update" and data.get("state") in LIVE_STATES, live.left() - 10)
        dubs = live.dubs()
        ok = any(dub["state"] in LIVE_STATES for dub in dubs)
        return f"{'PASS' if ok else 'FAIL'} озвучек {len(dubs)}: {states(dubs)}"
    profile = live.call("GET", "/api/parser-profiles")[0]["id"]
    seen: set[int] = set()

    def reference_of_all(name: str, data: dict) -> bool:
        if name == "reference_changed":
            seen.add(int(data["episode"]))
        return len(seen) >= EPISODES

    watching = live.stand.listen({"reference_changed"}, reference_of_all, min(100.0, live.left() - 60))
    created = live.call("POST", "/api/series", json={
        "name": NAME, "name_en": NAME_EN, "site": "dunhuatv.ru", "save_path": LIBRARY, "season": "s01",
        "sources": [{"url": URL, "source_type": "web_video", "parser_profile_id": profile, "vk_search_mode": "search",
                     "vibix_season": None, "players_off": []}]})
    assert created.get("series_id") or created.get("id"), f"series: {created}"
    watching[1].wait(110)
    live.step("сериал создан; референсы пришли событиями", episodes=len(seen))
    assert len(seen) >= EPISODES, f"references for {len(seen)} episodes"
    for episode in range(KEEP + 1, EPISODES + 1):
        live.call("PUT", f"{live.series_path()}/output/switch", json={"season": 1, "episode": episode, "off": True})
    output = live.call("GET", f"{live.series_path()}/output")
    wanted = [each for each in output if each["mark"] != "off"]
    assert [each["episode"] for each in wanted] == [KEEP], f"wanted: {[each['episode'] for each in wanted]}"
    reference = wanted[0]
    live.step("одна серия в работе, остальные отключены", reference=f"{reference['voice_group']} {reference['player']} {reference['height']}p")

    live.call("POST", f"{live.series_path()}/scan", timeout=live.left() - 30)
    live.step("проход по сериалу выполнен (загрузка поставлена)")
    live.watch({"voice_dub_update", "voice_dubs_snapshot", "conform_queue_update"},
               lambda name, data: name == "voice_dub_update" and data.get("state") in LIVE_STATES, live.left() - 10)
    dubs = live.dubs()
    items = live.call("GET", f"{live.series_path()}/media-items")
    downloaded = [item for item in items if item.get("status") == "completed" and not item.get("is_ignored_by_user")]
    live.step("озвучки", states=states(dubs), downloaded_items=len(downloaded))
    ok = bool(downloaded) and any(dub["state"] in LIVE_STATES for dub in dubs)
    return f"PASS референс скачан ({len(downloaded)}), озвучек {len(dubs)}: {states(dubs)}" if ok else (
        f"FAIL референс скачан: {len(downloaded)}, озвучки: {states(dubs)}")


def phase_two(live: Live) -> str:
    ended = {"done", "error"}
    # What the service did while no stand was up is in the first snapshot; then events until every voice-over has ended.
    live.step("новый стенд на той же базе: работа подхвачена из базы", dubs=states(live.dubs()))
    while live.left() > 30:
        dubs = live.dubs()
        if dubs and all(dub["state"] in ended for dub in dubs):
            break
        live.watch({"voice_dub_update", "voice_dubs_snapshot", "conform_queue_update"},
                   lambda name, data: name == "voice_dub_update" and data.get("state") in ended, min(60.0, live.left() - 30))
    dubs = live.dubs()
    count = states(dubs)
    live.step("озвучки после ожидания", states=count)
    if not dubs or any(dub["state"] not in ended for dub in dubs):
        return f"FAIL ещё идёт: {count}"
    library = Path(LIBRARY)
    files = sorted(str(path.relative_to(library)) for path in library.rglob("*") if path.is_file()) if library.exists() else []
    temp = sorted(str(path.relative_to(LOCAL)) for path in Path(LOCAL).glob("downloads/**/*") if path.is_file())
    outputs = outputs_of(live.work, KEEP)
    pairs = pairs_of_jobs(live.work, KEEP)
    live.step("файлы рядом с референсом", files=files[:12], outputs=outputs, temporary_left=temp[:5])
    live.step("пары сервиса выравнивания", pairs=pairs)
    problems = judge_alignment(dubs, outputs, reference_beside(library, outputs), pairs, temp)
    return (f"PASS озвучек {len(dubs)}, результатов сервиса {len(pairs)}, дорожки по путям базы рядом с референсом, временная папка пуста"
            if not problems else f"FAIL {'; '.join(problems)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("one", "two"))
    parser.add_argument("--work", required=True)
    parser.add_argument("--safeguard", type=float, default=170)
    args = parser.parse_args()
    ident = "M-24" if args.phase == "one" else "M-25"
    verdict, steps = "FAIL прибор", []
    try:
        with Stand(profiles=PROFILES, work=args.work) as stand:
            live = Live(stand, time.monotonic() + args.safeguard - 15)
            live.work = args.work
            try:
                verdict = (phase_one if args.phase == "one" else phase_two)(live)
            except Exception as exc:  # noqa: BLE001 - the last line must come out in any outcome
                verdict = f"FAIL {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}"
                traceback.print_exc()
            steps = live.steps
    except Exception as exc:  # noqa: BLE001
        verdict = f"FAIL прибор: {type(exc).__name__}: {exc}"
        traceback.print_exc()
    RAW.mkdir(parents=True, exist_ok=True)
    with open(RAW / f"{ident}__{datetime.now(timezone.utc):%Y%m%d}.jsonl", "a", encoding="utf-8") as out:
        out.write(json.dumps({"phase": args.phase, "verdict": verdict, "steps": steps}, ensure_ascii=False) + "\n")
    print(f"ИЗМЕРЕНИЕ {ident}: {verdict}")
    return 0 if verdict.startswith("PASS") else 1


if __name__ == "__main__":
    sys.exit(main())
