"""The admission of the lines of a video application: the first, the central and the last episode of each line of a card
downloaded whole by the project's own path (the stream of the player, the downloader of the service) and the file read
back — picture, frame, sound, length; the speed of each download is recorded, the measure the downloader is tuned by. A line one of whose three files did not come is not admitted, and its other
episodes still waiting are not asked. Up to `AT_ONCE` files go side by side. A download silent for `SILENCE_S`, or not
whole within `FILE_LIMIT_S`, is a file that did not come.

One JSON line per episode, appended to `data/raw/app_lines_admission__<stamp>__<app>-<card>.jsonl` (out of git); the
files are deleted once read. The registry of admitted lines is made of these records (tools/analyzers/app_lines_registry.py).

    systemd-run --scope -q -p MemoryMax=1800M -p MemorySwapMax=0 \
        .venv/bin/python tools/collectors/app_lines_admission.py <app> <card> [line,line,...] [first,central,last]

The run is on the machine of the service: under the limit of memory of its own scope, and marked for the kernel as the
first to go when memory runs out (its downloads inherit the mark), so files of 2160p side by side never cost the service.

The episodes can be admitted one at a time (the last argument): the registry takes the last record of each episode.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from core.http_limits import bounded_client  # noqa: E402
from modules.downloads.module import FRAGMENTS_AT_ONCE  # noqa: E402
from modules.downloads import ytdlp  # noqa: E402
from modules.downloads.ytdlp import download  # noqa: E402
from modules.sources.apps import card_address, profiles_of  # noqa: E402
from modules.sources.module import APP_PROTOCOLS  # noqa: E402
from modules.sources.players.app_lines import AppPlayer  # noqa: E402
from modules.sources.players.contract import StreamUnavailable  # noqa: E402
from modules.sources.players.registry import PlayerRegistry  # noqa: E402
from modules.sources.players.app_lines import AppLines  # noqa: E402

sys.path.insert(0, os.path.join(ROOT, "tools", "collectors"))
from app_lines_inventory import environment, ffprobe  # noqa: E402

# Files downloaded side by side: the time of the admission is the line's slowest file, not the sum of all of them.
AT_ONCE = 8
# A download silent for this long at the admission is a line that does not give its file: the service itself waits three
# times longer before it gives a download up (modules/downloads/ytdlp.py, STALL_TIMEOUT_S) and goes to the next source.
SILENCE_S = 60.0
ytdlp.STALL_TIMEOUT_S = SILENCE_S
# A file of the admission comes whole within this long or is no file of it: the lines admitted so far gave an episode of
# 2160p in 25–90 s (52–271 Mbit/s). A line that needs longer is admitted by a run of its own, agreed with the owner.
FILE_LIMIT_S = 140.0


async def main(app: str, card: str, only: list[str], which_only: list[str]) -> None:
    (profile,) = [each for each in profiles_of(environment()) if each.name == app]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = os.path.join(ROOT, "data", "raw", f"app_lines_admission__{stamp}__{app}-{card}.jsonl")
    work = os.path.join(ROOT, "data", "admission")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    os.makedirs(work, exist_ok=True)
    async with bounded_client(timeout=20.0, follow_redirects=True) as http:
        player = AppPlayer(APP_PROTOCOLS[profile.protocol](profile, lambda: http))
        pacer = PlayerRegistry([AppLines([player])]).pacer
        found_card = await player.client.card(card)
        url = card_address(app, card)
        failed: set[str] = set()
        gate = asyncio.Semaphore(AT_ONCE)

        async def admit(code: str, which: str, place: int, label: str, token: str, raw) -> None:
            async with gate:
                if code in failed:
                    return
                record = {"at": datetime.now(timezone.utc).isoformat(), "app": app, "card": card, "line": code,
                          "which": which, "place": place, "label": label}
                began = time.monotonic()
                try:
                    stream = await player.stream(player.link_of(url, code, token), http, None, pacer)
                except StreamUnavailable as exc:
                    record.update(ok=False, step="stream", reason=exc.reason)
                else:
                    record["progressive"], record["playlist"] = stream["progressive"], stream["playlist"]
                    path = os.path.join(work, f"{app}-{card}-{code}-{which}.mp4")

                    async def progress(_event: dict) -> None:
                        return None

                    record["stream_seconds"] = round(time.monotonic() - began, 1)
                    fetching = time.monotonic()
                    try:
                        done, failure = await asyncio.wait_for(
                            download(stream["stream_url"], path, progress, headers=stream["headers"],
                                     threads=FRAGMENTS_AT_ONCE, progressive=stream["progressive"],
                                     playlist=stream["playlist"]), FILE_LIMIT_S)
                    except TimeoutError:
                        done, failure = False, f"файл не получен целиком за {FILE_LIMIT_S:.0f} с"
                    record["download_seconds"] = round(time.monotonic() - fetching, 1)
                    if not done:
                        record.update(ok=False, step="download", reason=failure[:300])
                    else:
                        record["size"] = os.path.getsize(path)
                        record["mbit_s"] = round(record["size"] * 8 / max(record["download_seconds"], 0.1) / 1e6, 1)
                        read = record["file"] = await ffprobe(path)
                        record["ok"] = bool(read.get("height") and read.get("audio") and read.get("duration"))
                        if not record["ok"]:
                            record.update(step="file", reason=read.get("error") or "no picture, sound or length")
                        os.remove(path)
                if not record["ok"]:
                    failed.add(code)
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                raw.flush()
                file = record.get("file") or {}
                print(f"{code:<8} {which:<8} {label[:18]:<18} ok={record['ok']} "
                      f"{file.get('width')}x{file.get('height')} audio={file.get('audio')} dur={file.get('duration')} "
                      f"size={record.get('size')} {record.get('download_seconds')} s {record.get('mbit_s')} Mbit/s "
                      f"{record.get('reason', '')[:80]}",
                      flush=True)

        began = time.monotonic()
        with open(raw_path, "a", encoding="utf-8") as raw:
            lines: dict[str, list[asyncio.Task]] = {}
            for line in found_card.lines:
                code, items = line.code, line.items
                if only and code not in only or not items:
                    continue
                for which, item in (("first", items[0]), ("central", items[len(items) // 2]), ("last", items[-1])):
                    if which_only and which not in which_only:
                        continue
                    lines.setdefault(code, []).append(asyncio.create_task(
                        admit(code, which, item.place, item.label, item.token, raw)))
            jobs = [task for tasks in lines.values() for task in tasks]

            async def watch(code: str) -> None:
                # A line one of whose files did not come is decided: its downloads still going are stopped.
                for done in asyncio.as_completed(lines[code]):
                    try:
                        await done
                    except asyncio.CancelledError:
                        pass
                    if code in failed:
                        for task in lines[code]:
                            task.cancel()
                        return

            await asyncio.gather(*(watch(code) for code in lines))
            await asyncio.gather(*jobs, return_exceptions=True)
        # What a stopped download left is no file of the admission.
        shutil.rmtree(work, ignore_errors=True)
        print(f"{len(jobs)} episodes in {time.monotonic() - began:.0f} s")
    print("raw:", raw_path)


# The kernel takes this run and its downloads, not the service, when the machine runs out of memory (as the tests do,
# tests/conftest.py).
FIRST_TO_GO = 1000


if __name__ == "__main__":
    with open("/proc/self/oom_score_adj", "w", encoding="ascii") as score:
        score.write(str(FIRST_TO_GO))
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3].split(",") if len(sys.argv) > 3 else [],
                     sys.argv[4].split(",") if len(sys.argv) > 4 else []))
    print("ADMISSION_DONE")
