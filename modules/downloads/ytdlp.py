"""Загрузка VK-видео в две фазы, каждая с мониторингом ИЗ ВЫВОДА
инструмента (мониторинг по размеру файла на диске запрещён — требование
пользователя).

Фаза 1 (download): yt-dlp тянет hls-поток в N параллельных фрагментов
    (-N) — единственная комбинация, дающая реальный прирост на VK
    (замер на стенде: hls+-N ≈ ×27 к скорости; -N без hls не помогает).
    Постобработку yt-dlp отключаем (--fixup never): remux делаем сами,
    чтобы владеть его метриками.
Фаза 2 (remux): наш ffmpeg -c copy с -progress pipe:1 перепаковывает
    промежуточный MPEG-TS в mp4. Прогресс и ETA считаются из метрик
    ffmpeg (out_time / длительность, speed) — постобработка обязана
    показывать прогресс и оставшееся время (ТЗ пользователя).

Разбор строк — чистые функции (тестируются без процессов); запуск —
асинхронные subprocess с убийством при отмене (находка 45).
"""
from __future__ import annotations

import asyncio
import glob
import os
import re
import shutil
from typing import Awaitable, Callable

ProgressCb = Callable[[dict], Awaitable[None]]

# hls + лучший muxed-поток; fallback на обычный best, если hls нет
# (тогда без ускорения, но загрузка работает).
HLS_FORMAT = "b[protocol*=m3u8]/b"

# Формат hlsnative+-N отличается от обычного: после '~' идут пробелы,
# ETA бывает 'Unknown', в хвосте '(frag X/Y)' (находка 47).
PROGRESS_RE = re.compile(
    r"\[download\]\s+(?P<percent>[\d.]+)%\s+of\s+~?\s*(?P<size>[\d.]+\w+)"
    r"\s+at\s+(?P<speed>[\d.]+\w+/s)\s+ETA\s+(?P<eta>[\d:]+|Unknown)")

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
    """Строка yt-dlp '[download] X% of ~Y at Z/s ETA W' -> метрики."""
    m = PROGRESS_RE.search(line)
    if not m:
        return None
    d = m.groupdict()
    return {
        "phase": "download",
        "progress": int(float(d["percent"])),
        "total_size_mb": round(size_to_bytes(d["size"]) / (1024 ** 2), 2),
        "dlspeed": size_to_bytes(d["speed"]),
        "eta": eta_to_seconds(d["eta"]),
    }


def _hms_to_seconds(text: str) -> float:
    """'00:00:39.98' -> 39.98."""
    try:
        h, m, s = text.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except (ValueError, AttributeError):
        return 0.0


def ffmpeg_progress(fields: dict, duration: float) -> dict:
    """Блок метрик ffmpeg -progress (out_time, speed) + длительность ->
    прогресс remux в % и ETA. Чистая функция (тестируется отдельно)."""
    out_time = _hms_to_seconds(fields.get("out_time", "") or "")
    speed_raw = (fields.get("speed", "") or "").replace("x", "").strip()
    try:
        speed = float(speed_raw)
    except ValueError:
        speed = 0.0
    percent = int(min(100, out_time / duration * 100)) if duration > 0 else 0
    remaining = max(0.0, duration - out_time)
    eta = int(remaining / speed) if speed > 0 else 0
    return {"phase": "remux", "progress": percent, "eta": eta,
            "speed": round(speed, 1)}


async def _probe_duration(path: str) -> float:
    """Длительность файла через ffprobe (метрика инструмента, не размер)."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    proc = await asyncio.create_subprocess_exec(
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
        return 0.0


async def download(video_url: str, full_output_path: str,
                   on_progress: ProgressCb, *, threads: int = 6,
                   ) -> tuple[bool, str]:
    """Двухфазная загрузка. (успех, текст ошибки). Существующий файл —
    успех (идемпотентность)."""
    os.makedirs(os.path.dirname(full_output_path), exist_ok=True)
    if os.path.exists(full_output_path):
        return True, "файл уже существует"

    raw = await _phase_download(video_url, full_output_path, on_progress,
                                threads)
    if not isinstance(raw, str):
        return raw  # (False, error)
    try:
        return await _phase_remux(raw, full_output_path, on_progress)
    finally:
        for leftover in glob.glob(full_output_path + ".download.*"):
            try:
                os.remove(leftover)
            except OSError:
                pass


async def _phase_download(video_url: str, full_output_path: str,
                          on_progress: ProgressCb, threads: int):
    """yt-dlp hls + -N, без постобработки. Возвращает путь промежуточного
    файла (str) или (False, error)."""
    executable = shutil.which("yt-dlp")
    if not executable:
        return False, "yt-dlp не найден в PATH"
    out_template = full_output_path + ".download.%(ext)s"

    proc = await asyncio.create_subprocess_exec(
        executable, "--progress", "--newline", "--fixup", "never",
        "-f", HLS_FORMAT, "-N", str(threads),
        "-o", out_template, video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
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
        if proc.returncode is None:  # отмена/сбой — убить процесс (находка 45)
            proc.kill()
            await proc.wait()

    if proc.returncode != 0:
        stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
        error = stderr.strip().splitlines()[-1] if stderr.strip() else \
            f"yt-dlp завершился с кодом {proc.returncode}"
        if "Video unavailable" in stderr or "Private video" in stderr:
            error = "видео недоступно или приватно"
        return False, error

    raw_files = glob.glob(full_output_path + ".download.*")
    if not raw_files:
        return False, "yt-dlp не оставил файл после загрузки"
    return raw_files[0]


async def _phase_remux(raw: str, full_output_path: str,
                       on_progress: ProgressCb) -> tuple[bool, str]:
    """Наш ffmpeg -c copy с -progress pipe:1: remux в mp4, прогресс и ETA
    из метрик ffmpeg (out_time/длительность, speed)."""
    executable = shutil.which("ffmpeg")
    if not executable:
        return False, "ffmpeg не найден в PATH"
    duration = await _probe_duration(raw)
    await on_progress({"phase": "remux", "progress": 0, "eta": 0,
                       "speed": 0.0})

    proc = await asyncio.create_subprocess_exec(
        executable, "-y", "-i", raw, "-c", "copy",
        "-movflags", "+faststart", "-progress", "pipe:1", "-nostats",
        full_output_path,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    fields: dict[str, str] = {}
    try:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if "=" not in text:
                continue
            key, value = text.split("=", 1)
            fields[key] = value
            if key == "progress":  # конец блока метрик -> эмитим прогресс
                await on_progress(ffmpeg_progress(fields, duration))
                fields = {}
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()

    if proc.returncode == 0:
        await on_progress({"phase": "remux", "progress": 100, "eta": 0,
                           "speed": 0.0})
        return True, ""
    stderr = (await proc.stderr.read()).decode("utf-8", errors="replace")
    tail = stderr.strip().splitlines()[-1] if stderr.strip() else ""
    return False, f"ffmpeg remux: {tail or proc.returncode}"
