"""Duration of a media file or stream through ffprobe, with the bounded life of core.proc."""
from __future__ import annotations

import shutil

from .proc import run_capture

DEFAULT_TIMEOUT_S = 30.0


async def duration_of(target: str, *, input_args: list[str] | None = None,
                      timeout: float = DEFAULT_TIMEOUT_S) -> float:
    """Seconds of a file or stream; 0 when ffprobe is missing or cannot read the target.

    `input_args` go before the target (protocol whitelist, headers of a stream)."""
    executable = shutil.which("ffprobe")
    if not executable:
        return 0.0
    cmd = [executable, "-v", "error", *(input_args or []), "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", target]
    try:
        code, out, _ = await run_capture(cmd, timeout=timeout)
        return float(out.decode().strip()) if code == 0 and out else 0.0
    except (OSError, ValueError):
        return 0.0


async def video_gaps(path: str, *, longer_than: float, timeout: float = 120.0) -> list[tuple[float, float]]:
    """(second where it begins, seconds it lasts) of every pause between two frames of the first video stream that is
    longer than `longer_than`: a piece the download lost leaves such a pause, while the audio and the length of the file
    stay whole. Empty when there is none, when the file has no video, or when ffprobe cannot read it."""
    executable = shutil.which("ffprobe")
    if not executable:
        return []
    cmd = [executable, "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", path]
    try:
        code, out, _ = await run_capture(cmd, timeout=timeout)
    except (OSError, TimeoutError):
        return []
    if code != 0:
        return []
    times = sorted(float(word) for word in out.decode().split() if _is_number(word))
    return [(before, after - before) for before, after in zip(times, times[1:]) if after - before > longer_than]


def _is_number(word: str) -> bool:
    try:
        float(word)
    except ValueError:
        return False
    return True

