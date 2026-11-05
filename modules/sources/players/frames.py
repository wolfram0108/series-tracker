"""The real frame height of a stream, read by ffprobe: for the players whose labels of quality do not state it.

The labels of a player are words of the host (CDN VideoHub calls a 2160 frame "2k", animakima calls a 720 frame "1080p"),
so a height that matters is measured on the stream itself, with the headers the host signs the stream for.
"""
from __future__ import annotations

import asyncio
import shutil
from typing import Mapping

from core.proc import run_capture

MEASURE_TIMEOUT_S = 40.0


async def frame_height(url: str, headers: Mapping[str, str]) -> int | None:
    """The height of the first video stream; None when ffprobe is missing or the stream cannot be read."""
    executable = shutil.which("ffprobe")
    if not executable:
        return None
    agent = headers.get("User-Agent")
    blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if key != "User-Agent")
    cmd = [executable, "-v", "error", *(["-user_agent", agent] if agent else []), *(["-headers", blob] if blob else []),
           "-select_streams", "v:0", "-show_entries", "stream=height", "-of", "csv=p=0", url]
    try:
        _, out, _ = await run_capture(cmd, timeout=MEASURE_TIMEOUT_S)
    except (OSError, asyncio.TimeoutError):
        return None
    try:
        return int(out.decode().strip().splitlines()[0])
    except (ValueError, IndexError):
        return None
