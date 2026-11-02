"""K-3: the length of every CVH video of a title, one line for each, as the player gives it.

    cvh_durations.py --page <address of the page of a title on dunhuatv.ru> [--studio <part of a name>]... [--pace 0.15]

Reads the playlist of the title with the application's own reader, then the answer of the player for each video of the
chosen studios (all when none is named): `duration` is in the same answer that gives the rungs of the ladder. Writes
raw/measurements/K-3__cvh_durations__<date>.jsonl, a file that is never overwritten; the last line printed is the trigger
`ИЗМЕРЕНИЕ K-3: PASS <n>` (n lines written) or `ИЗМЕРЕНИЕ K-3: FAIL <reason>`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from modules.sources.players import cdnvideohub as cvh  # noqa: E402

RAW = Path(__file__).resolve().parents[2] / "raw" / "measurements"


async def collect(page: str, studios: list[str], pace: float, out: Path) -> int:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as http:
        html = (await http.get(page, headers=cvh.REQUEST_HEADERS)).text
        title, publisher = cvh.widget_of(html)
        if title is None:
            raise RuntimeError("на странице нет плеера CVH")
        answer = await cvh.playlist_request(http, title, publisher)
        items = cvh.read_playlist(answer)
        chosen = [item for item in items if not studios or any(name.lower() in item.studio.lower() for name in studios)]
        written = 0
        with out.open("x", encoding="utf-8") as handle:
            for item in chosen:
                reply = await http.get(f"{cvh.API}/player/sv/video/{item.vk_id}", headers=cvh.REQUEST_HEADERS)
                duration = None
                if reply.status_code == 200 and reply.text.strip():
                    try:
                        duration = json.loads(reply.text).get("duration")
                    except ValueError:
                        duration = None
                handle.write(json.dumps({
                    "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "page": page,
                    "studio": item.studio, "voice_type": item.voice_type, "season": item.season,
                    "episode": item.episode, "vk_id": item.vk_id, "status": reply.status_code, "duration_s": duration,
                }, ensure_ascii=False) + "\n")
                written += 1
                await asyncio.sleep(pace)  # a rate limit of the host, not a wait for an answer
        return written


def main() -> int:
    parser = argparse.ArgumentParser(prog="cvh_durations")
    parser.add_argument("--page", required=True)
    parser.add_argument("--studio", action="append", default=[])
    parser.add_argument("--pace", type=float, default=0.15)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or RAW / f"K-3__cvh_durations__{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        written = asyncio.run(collect(args.page, args.studio, args.pace, out))
    except Exception as exc:  # noqa: BLE001 - whatever stopped the instrument is the verdict about the instrument
        print(f"ИЗМЕРЕНИЕ K-3: FAIL {type(exc).__name__}: {exc}")
        return 2
    print(f"ИЗМЕРЕНИЕ K-3: PASS {written} {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
