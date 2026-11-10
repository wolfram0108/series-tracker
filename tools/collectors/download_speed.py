"""The speed of the downloader of the service on one episode of a line of a video application, at a given parallelism:
the stream resolved by the player, the download started by the project's downloader, the bytes on disk counted for a
window of seconds, then the download stopped and its files removed. The rate is taken after the first seconds of the
start, so the parse and the opening of the connections do not lower it.

One JSON line per measure, appended to `data/raw/download_speed__<stamp>__<app>-<card>.jsonl` (out of git).

    .venv/bin/python tools/collectors/download_speed.py <app> <card> <line> <first|central|last> <threads,...> [seconds]
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
sys.path.insert(0, os.path.join(ROOT, "tools", "collectors"))

from app_lines_inventory import environment  # noqa: E402
from core.http_limits import bounded_client  # noqa: E402
from modules.downloads.ytdlp import download  # noqa: E402
from modules.sources.apps import card_address, profiles_of  # noqa: E402
from modules.sources.apps.app99 import App99Client  # noqa: E402
from modules.sources.players.app_lines import AppPlayer  # noqa: E402
from modules.sources.players.registry import PlayerRegistry  # noqa: E402
from modules.sources.sites.apps.site import lines_of  # noqa: E402

# The seconds of the start not counted: the worker starts, the playlist is read, the first connections open.
RAMP_S = 3.0


def size_of(folder: str) -> int:
    return sum(os.path.getsize(os.path.join(root, name)) for root, _dirs, names in os.walk(folder) for name in names
               if os.path.exists(os.path.join(root, name)))


async def measure(player, http, pacer, url, code, token, threads: int, seconds: float, work: str) -> dict:
    stream = await player.stream(player.link_of(url, code, token), http, None, pacer)
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)

    async def progress(_event: dict) -> None:
        return None

    task = asyncio.create_task(download(stream["stream_url"], os.path.join(work, "episode.mp4"), progress,
                                        headers=stream["headers"], threads=threads, progressive=stream["progressive"],
                                        playlist=stream["playlist"]))
    began = time.monotonic()
    await asyncio.sleep(RAMP_S)
    first_bytes, first_at = size_of(work), time.monotonic()
    done = None
    try:
        done = await asyncio.wait_for(asyncio.shield(task), seconds)
    except TimeoutError:
        pass
    last_bytes, last_at = size_of(work), time.monotonic()
    if not task.done():
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001 - the measure stops the download, whatever it says
            pass
    shutil.rmtree(work, ignore_errors=True)
    window = max(last_at - first_at, 0.1)
    return {"progressive": stream["progressive"], "playlist": stream["playlist"], "threads": threads,
            "window_s": round(window, 1), "bytes": last_bytes - first_bytes,
            "mbit_s": round((last_bytes - first_bytes) * 8 / window / 1e6, 1),
            "finished": done, "total_s": round(time.monotonic() - began, 1)}


async def main(app: str, card: str, code: str, which: str, threads: list[int], seconds: float) -> None:
    (profile,) = [each for each in profiles_of(environment()) if each.name == app]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    raw_path = os.path.join(ROOT, "data", "raw", f"download_speed__{stamp}__{app}-{card}.jsonl")
    os.makedirs(os.path.dirname(raw_path), exist_ok=True)
    async with bounded_client(timeout=20.0, follow_redirects=True) as http:
        player = AppPlayer(App99Client(profile, lambda: http))
        pacer = PlayerRegistry([player]).pacer
        items = dict(lines_of(await player.client.detail(card)))[code]
        place, label, token = {"first": items[0], "central": items[len(items) // 2], "last": items[-1]}[which]
        with open(raw_path, "a", encoding="utf-8") as raw:
            for each in threads:
                got = await measure(player, http, pacer, card_address(app, card), code, token, each, seconds,
                                    os.path.join(ROOT, "data", "speed"))
                record = {"at": datetime.now(timezone.utc).isoformat(), "app": app, "card": card, "line": code,
                          "which": which, "label": label, **got}
                raw.write(json.dumps(record, ensure_ascii=False) + "\n")
                print(f"{code} {which} {label[:16]} threads={each}: {got['mbit_s']} Mbit/s over {got['window_s']} s "
                      f"({'file' if got['progressive'] else 'playlist'}{', finished' if got['finished'] else ''})",
                      flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], [int(each) for each in sys.argv[5].split(",")],
                     float(sys.argv[6]) if len(sys.argv) > 6 else 30.0))
    print("SPEED_DONE")
