"""Helpers of tests that start and stop real programs."""
import asyncio
import os


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class Spy:
    """Remembers the processes started through asyncio."""

    def __init__(self):
        self.started = []
        self._real = asyncio.create_subprocess_exec

    async def __call__(self, *args, **kwargs):
        process = await self._real(*args, **kwargs)
        self.started.append(process)
        return process


def stalled_downloader(tmp_path, name: str = "yt-dlp") -> str:
    script = tmp_path / name
    script.write_text("#!/bin/sh\nexec sleep 60\n")
    script.chmod(0o755)
    return str(script)
