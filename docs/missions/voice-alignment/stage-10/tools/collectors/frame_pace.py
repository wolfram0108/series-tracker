"""The pace of the frames of finished video files: how long the longest pause between two frames is against the usual one.

    frame_pace.py --library <folder with series folders> [--limit 60] [--jobs 2]

The check for holes in a downloaded video (`core/ffprobe.video_gaps`) calls a pause a hole when it is longer than
`HOLE_S` seconds and longer than `PACE_FACTOR` usual intervals between frames. Both numbers have to stand above the
pauses of healthy files, and this tool measures those pauses: one line for each file, the same packets the check reads.
It takes every k-th video file of the library (sorted by path) so that the sample is the same on every run and spreads over
all the series. Writes raw/measurements/PACE__<date>.jsonl, a file that is never overwritten; the last line printed is
`ИЗМЕРЕНИЕ PACE: PASS <n>` or `ИЗМЕРЕНИЕ PACE: FAIL <reason>`.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[6]
sys.path.insert(0, str(ROOT))

from core.proc import run_capture  # noqa: E402

RAW = Path(__file__).resolve().parents[2] / "raw" / "measurements"
EXTENSIONS = (".mp4", ".mkv")


def sample(library: Path, limit: int) -> list[Path]:
    files = sorted(path for path in library.rglob("*") if path.suffix.lower() in EXTENSIONS and path.is_file())
    step = max(1, len(files) // limit)
    return files[::step][:limit]


async def pace_of(path: Path) -> dict:
    began = time.monotonic()
    code, out, _ = await run_capture([shutil.which("ffprobe") or "ffprobe", "-v", "error", "-select_streams", "v:0",
                                      "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)], timeout=150.0)
    record = {"file": str(path), "bytes": path.stat().st_size, "code": code, "seconds": round(time.monotonic() - began, 2)}
    times = sorted(float(word) for word in out.decode().split() if word.replace(".", "", 1).replace("-", "", 1).isdigit())
    if code != 0 or len(times) < 3:
        return record | {"frames": len(times)}
    intervals = [after - before for before, after in zip(times, times[1:])]
    median = statistics.median(intervals)
    longest = max(intervals)
    return record | {"frames": len(times), "length_s": round(times[-1], 2), "median_interval_s": round(median, 5),
                     "longest_pause_s": round(longest, 4), "longest_at_s": round(times[intervals.index(longest)], 2),
                     "pauses_over_1s": sum(1 for each in intervals if each > 1.0),
                     "pauses_over_8_intervals": sum(1 for each in intervals if each > 8 * median)}


async def collect(library: Path, limit: int, jobs: int, out: Path) -> int:
    files = sample(library, limit)
    gate = asyncio.Semaphore(jobs)

    async def one(path: Path) -> dict:
        async with gate:
            return await pace_of(path)

    records = await asyncio.gather(*(one(path) for path in files))
    with out.open("x", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps({"collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **record},
                                    ensure_ascii=False) + "\n")
    return len(records)


def main() -> int:
    parser = argparse.ArgumentParser(prog="frame_pace")
    parser.add_argument("--library", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    out = args.out or RAW / f"PACE__{datetime.now(timezone.utc):%Y%m%d}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = asyncio.run(collect(args.library, args.limit, args.jobs, out))
    except Exception as exc:  # noqa: BLE001 - whatever stopped the instrument is the verdict about the instrument
        print(f"ИЗМЕРЕНИЕ PACE: FAIL {type(exc).__name__}: {exc}")
        return 2
    print(f"ИЗМЕРЕНИЕ PACE: PASS {count} {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
