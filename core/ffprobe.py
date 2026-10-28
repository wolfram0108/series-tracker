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
