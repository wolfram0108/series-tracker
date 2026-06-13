"""Запуск yt-dlp и разбор его прогресса.

Разбор строк — чистые функции (тестируются без процесса); сам запуск —
асинхронный subprocess: чтение stdout не занимает поток, зависание
процесса не замораживает модуль (урок находки 7).
"""
from __future__ import annotations

import asyncio
import os
import re
import shutil
from typing import Awaitable, Callable

PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<percent>[\d.]+)%\s+of\s+~?(?P<size>[\d.]+\w+)"
    r"\s+at\s+(?P<speed>[\d.]+\w+/s)\s+ETA\s+(?P<eta>[\d:]+)")

_UNITS = {"kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3}


def size_to_bytes(text: str) -> int:
    text = text.lower().replace("/s", "").strip()
    for unit, mult in _UNITS.items():
        if unit in text:
            return int(float(text.replace(unit, "").strip()) * mult)
    return 0


def eta_to_seconds(text: str) -> int:
    parts = [int(p) for p in text.split(":") if p.isdigit()]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return 0


def parse_progress_line(line: str) -> dict | None:
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    d = m.groupdict()
    return {
        "progress": int(float(d["percent"])),
        "total_size_mb": round(size_to_bytes(d["size"]) / (1024 ** 2), 2),
        "dlspeed": size_to_bytes(d["speed"]),
        "eta": eta_to_seconds(d["eta"]),
    }


async def download(video_url: str, full_output_path: str,
                   on_progress: Callable[[dict], Awaitable[None]],
                   ) -> tuple[bool, str]:
    """Скачивание одного видео. Возвращает (успех, текст ошибки).
    Существующий файл — успех (идемпотентность, как в оригинале)."""
    os.makedirs(os.path.dirname(full_output_path), exist_ok=True)
    if os.path.exists(full_output_path):
        return True, "файл уже существует"

    executable = shutil.which("yt-dlp")
    if not executable:
        return False, "yt-dlp не найден в PATH"

    proc = await asyncio.create_subprocess_exec(
        executable, "--progress", "--newline",
        "--merge-output-format", "mp4",
        "-o", full_output_path, video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    # При отмене задачи (downloads.cancel) корутину прерывает
    # CancelledError на await ниже — процесс yt-dlp надо убить, иначе он
    # продолжит качать осиротевшим (находка 45). finally гарантирует это
    # при любом выходе.
    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            data = parse_progress_line(line.decode("utf-8", errors="replace"))
            if data:
                await on_progress(data)
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    if proc.returncode == 0:
        await on_progress({"progress": 100, "dlspeed": 0, "eta": 0})
        return True, ""
    stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
    error = stderr.strip().splitlines()[-1] if stderr.strip() else \
        f"yt-dlp завершился с кодом {proc.returncode}"
    if "Video unavailable" in stderr or "Private video" in stderr:
        error = "видео недоступно или приватно"
    return False, error
