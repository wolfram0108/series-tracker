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
import os
import re
import shutil
import time
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from core import storage
from core.ffprobe import duration_of, video_gaps
from core.proc import run_capture, spawn
from core.retry import UNTIMED, Budget, retrying

from . import ranged

ProgressCb = Callable[[dict], Awaitable[None]]

# Decoded manifests live on disk while their segments stay remote (Vibix).
PROTOCOL_WHITELIST = ["-protocol_whitelist", "file,http,https,tcp,tls"]

# CDNs of the web players stall on open and drop a stream halfway: ffmpeg reconnects instead of failing.
RECONNECT_ARGS = ["-reconnect", "1", "-reconnect_streamed", "1",
                  "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30"]
# A segment the CDN refuses once is asked for again by the HLS demuxer instead of being skipped (it skips by default); the same
# count of looks as the CDN gets elsewhere in this module. What is still lost is caught by the line of a skipped segment.
SEGMENT_RETRIES = 3
# ffmpeg exits 0 on a broken-off HLS stream, so a download counts only above this share of the
# duration the stream announces.
MIN_COMPLETE_FRACTION = 0.85
# A pause between two frames longer than this is a piece of the video the download lost (ffmpeg skips a segment it cannot
# open and still exits 0; the audio and the length of the file stay whole), not the pace of the picture.
HOLE_S = 1.0
# ffmpeg skips an HLS segment it cannot open and exits 0: the file is shorter by the segment, which a share of the duration does not
# see, and the voice-over missing a piece is refused by the service as a foreign video. This line is the one sign of the loss.
_SKIPPED_SEGMENT = re.compile(rb"Failed to open segment (\d+) of playlist \d+")
STALL_TIMEOUT_S = 180.0
DOWNLOAD_TIMEOUT_S = 3600.0
PROBE_ATTEMPTS = 3
PROBE_RETRY_S = 2.0
PROBE_TIMEOUT_S = 60.0

# hls + лучший muxed-поток; fallback на обычный best, если hls нет
# (тогда без ускорения, но загрузка работает).
HLS_FORMAT = "b[protocol*=m3u8]/b"

# Отказ от m3u8: на некоторых VK-роликах CDN отдаёт 404 на хвостовые
# HLS-фрагменты (манифест шире, чем реально долитые сегменты) — весь
# файл выходит пустым. DASH/progressive не фрагментированы так же
# (одно соединение на поток) — не подвержены этой дыре, но без -N
# параллелизма (медленнее).
DASH_FALLBACK_FORMAT = "b[protocol!*=m3u8]"
_WARNING_LINE = re.compile(
    r"^\s*(?:WARNING:|It is strongly recommended|Run \"yt-dlp --update\"|"
    r"To suppress this warning)", re.IGNORECASE)


def _ytdlp_error(tail: str, default: str) -> str:
    """The real failure is the ERROR line, not yt-dlp's version warning."""
    lines = [line.strip() for line in tail.splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            return line[len("ERROR:"):].strip() or line
    for line in reversed(lines):
        if not _WARNING_LINE.match(line):
            return line
    return default


def _cleanup_artifacts(full_output_path: str) -> None:
    for leftover in download_artifacts(full_output_path):
        storage.remove_file(leftover)

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


def ffmpeg_progress(fields: dict, duration: float,
                    phase: str = "remux") -> dict:
    """Блок метрик ffmpeg -progress (out_time, speed) + длительность ->
    прогресс фазы в % и ETA. Чистая функция (тестируется отдельно)."""
    out_time = _hms_to_seconds(fields.get("out_time", "") or "")
    speed_raw = (fields.get("speed", "") or "").replace("x", "").strip()
    try:
        speed = float(speed_raw)
    except ValueError:
        speed = 0.0
    percent = int(min(100, out_time / duration * 100)) if duration > 0 else 0
    remaining = max(0.0, duration - out_time)
    eta = int(remaining / speed) if speed > 0 else 0
    return {"phase": phase, "progress": percent, "eta": eta,
            "speed": round(speed, 1)}


async def _drain_stderr(stream, lost: list[int] | None = None) -> list[bytes]:
    """Read stderr in the background so a full pipe never blocks the process (a broken VK HLS floods ffmpeg with thousands of
    'Packet corrupt' lines). Only the tail is kept for the error text; the numbers of skipped segments go to `lost` as the lines
    arrive, before the tail pushes them out."""
    tail: list[bytes] = []
    while True:
        line = await stream.readline()
        if not line:
            break
        if lost is not None and (skipped := _SKIPPED_SEGMENT.search(line)):
            lost.append(int(skipped.group(1)))
        tail.append(line)
        if len(tail) > 50:
            del tail[0]
    return tail


class _Silent(Exception):
    """A running program printed nothing for too long, or ran past its total time."""


async def _next_line(proc, program: str, started: float, total: float | None = None) -> bytes:
    """The next line of a program's output; the program is declared silent after STALL_TIMEOUT_S."""
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), STALL_TIMEOUT_S)
    except asyncio.TimeoutError:
        raise _Silent(f"{program}: нет прогресса {STALL_TIMEOUT_S:.0f} с (timeout)") from None
    if total is not None and time.monotonic() - started > total:
        raise _Silent(f"{program}: загрузка дольше {total:.0f} с (timeout)")
    return line


async def _probe_duration(video_url: str) -> float:
    """Длительность видео из yt-dlp (метаданные источника — НАДЁЖНО).
    ffprobe промежуточного MPEG-TS даёт заниженную длительность (находка
    52: 7343с вместо реальных 102787с), из-за чего процент remux был бы
    100% сразу. yt-dlp --print даёт точное значение из метаданных VK."""
    executable = shutil.which("yt-dlp")
    if not executable:
        return 0.0
    try:
        _, out, _ = await run_capture(
            [executable, "--no-download", "--print", "%(duration)s", video_url], timeout=PROBE_TIMEOUT_S)
        return float(out.decode().strip())
    except (asyncio.TimeoutError, OSError, ValueError):
        return 0.0


def _download_artifact(full_output_path: str) -> str | None:
    """Готовый промежуточный файл фазы download (`<path>.download.<ext>`),
    либо None, если его нет.

    Имя пути — это ДАННЫЕ и может содержать спецсимволы glob (`[ ] * ?`):
    каталоги медиатеки именуются с `[tmdbid-NNNN]`. Поэтому ищем файл
    строковым сопоставлением через listdir, а НЕ glob — glob трактовал
    `[tmdbid-…]` как класс символов и не находил реально скачанный файл
    (ложное «yt-dlp не оставил файл»)."""
    directory = os.path.dirname(full_output_path)
    prefix = os.path.basename(full_output_path) + ".download."
    try:
        for name in sorted(os.listdir(directory)):
            if name.startswith(prefix):
                return os.path.join(directory, name)
    except OSError:
        pass
    return None


def download_artifacts(full_output_path: str) -> list[str]:
    """Все временные артефакты загрузки/ремукса для данного финального пути:
    промежуточный `.download.*`, tmp `.remux.mp4`, `.part`/`.ytdl` и фрагменты
    yt-dlp (`<stem>.fNNN.*`). Сам финальный файл НЕ включается.

    Сопоставление строками, не glob (причина — см. _download_artifact: имя
    пути содержит спецсимволы glob из `[tmdbid-…]`)."""
    directory = os.path.dirname(full_output_path)
    base = os.path.basename(full_output_path)
    stem = os.path.splitext(base)[0]
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    out: list[str] = []
    for name in names:
        if (name.startswith(base + ".download.")   # .download.<ext>(.part/.ytdl/.fNNN)
                or name == base + ".remux.mp4"     # незавершённый tmp remux
                or name == base + ".part"
                or (name.startswith(stem + ".") and name.endswith(".part"))
                or (name.startswith(stem) and name.endswith(".ytdl"))
                or name.startswith(stem + ".f")):  # фрагменты yt-dlp <stem>.fNNN.*
            out.append(os.path.join(directory, name))
    return out


def _reconnect_args(stream_url: str) -> list[str]:
    """Network options do not apply to a local decoded manifest (same as the header options)."""
    return [] if stream_url.startswith("file:") else list(RECONNECT_ARGS)


def _segment_args(stream_url: str) -> list[str]:
    """The option belongs to the HLS demuxer: any other input would refuse it."""
    return ["-seg_max_retry", str(SEGMENT_RETRIES)] if urlsplit(stream_url).path.endswith(".m3u8") else []


def _headers_args(stream_url: str, headers: dict) -> list[str]:
    """A local decoded manifest rejects HTTP options like -headers; the agent has its own option."""
    if not headers or stream_url.startswith("file:"):
        return []
    args: list[str] = []
    agent = next((v for k, v in headers.items() if k.lower() == "user-agent"), None)
    if agent:
        args += ["-user_agent", agent]
    blob = "".join(f"{k}: {v}\r\n" for k, v in headers.items() if k.lower() != "user-agent")
    if blob:
        args += ["-headers", blob]
    return args


async def _probe_duration_ffprobe(stream_url: str, headers: dict | None = None) -> float:
    """Duration a stream announces; a flaky CDN gets a few looks before the duration is given up."""
    args = [*PROTOCOL_WHITELIST, *_headers_args(stream_url, headers or {})]
    budget = Budget(attempts=PROBE_ATTEMPTS, pauses=(PROBE_RETRY_S,), limit_s=UNTIMED)
    duration, _attempts, _exhausted = await retrying(
        lambda: duration_of(stream_url, input_args=args, timeout=PROBE_TIMEOUT_S), final=lambda seconds: seconds > 0,
        budget=budget)
    return max(duration, 0.0)


async def _local_duration(path: str) -> float:
    """Duration of a file on disk; 0 when it cannot be read."""
    return await duration_of(path, timeout=PROBE_TIMEOUT_S)


async def _download_direct_ffmpeg(stream_url: str, headers: dict,
                                  full_output_path: str,
                                  on_progress: ProgressCb) -> tuple[bool, str]:
    executable = shutil.which("ffmpeg")
    if not executable:
        return False, "ffmpeg не найден в PATH"
    duration = await _probe_duration_ffprobe(stream_url, headers)
    tmp_out = full_output_path + ".download.mp4"
    await on_progress({"phase": "download", "progress": 0, "eta": 0, "speed": 0.0})

    cmd = [executable, "-y"]
    cmd.extend(PROTOCOL_WHITELIST)
    cmd.extend(_reconnect_args(stream_url))
    cmd.extend(_segment_args(stream_url))
    cmd.extend(_headers_args(stream_url, headers))
    cmd.extend(["-i", stream_url, "-c", "copy", "-progress", "pipe:1", "-nostats", tmp_out])

    proc = await spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    skipped: list[int] = []
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr, skipped))
    fields: dict[str, str] = {}
    last_size = 0
    last_at = time.monotonic()
    started = last_at
    stopped = ""
    try:
        assert proc.stdout is not None
        while True:
            try:
                line = await _next_line(proc, "ffmpeg", started, DOWNLOAD_TIMEOUT_S)
            except _Silent as exc:
                stopped = str(exc)
                break
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if "=" not in text:
                continue
            k, v = text.split("=", 1)
            fields[k] = v
            if k == "progress":
                data = ffmpeg_progress(fields, duration, phase="download")
                size = int(fields.get("total_size") or 0)
                now = time.monotonic()
                if now > last_at and size >= last_size:
                    data["dlspeed"] = int((size - last_size) / (now - last_at))
                last_size, last_at = size, now
                await on_progress(data)
                fields = {}
        if not stopped:
            await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if stopped:
        await storage.discard(tmp_out)
        return False, stopped
    if proc.returncode == 0:
        if skipped:
            await storage.discard(tmp_out)
            return False, (f"обрыв: ffmpeg пропустил сегменты потока ({len(skipped)}, первый №{skipped[0]}) — "
                           "неполная загрузка")
        got = await _local_duration(tmp_out)
        if got <= 0 or (duration > 0 and got < duration * MIN_COMPLETE_FRACTION):
            await storage.discard(tmp_out)
            return False, (f"обрыв: скачано {got:.0f} с из ожидаемых ~{duration:.0f} с — "
                           "неполная загрузка")
        await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))
        await on_progress({"phase": "download", "progress": 100, "eta": 0, "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    return False, f"ffmpeg direct download: {last or proc.returncode}"


def _unavailable_error(error: str) -> bool:
    lowered = (error or "").lower()
    return ("недоступно" in lowered or "приватн" in lowered
            or "video unavailable" in lowered or "private video" in lowered)


async def download(video_url: str, full_output_path: str,
                   on_progress: ProgressCb, *,
                   headers: dict | None = None,
                   threads: int = 6,
                   progressive: bool = False,
                   whole_video: bool = False) -> tuple[bool, str]:
    """Two-phase video download. Supports parts of a ready file, direct ffmpeg copy and yt-dlp.

    `whole_video` asks for a video with no hole: the file is a file of the library, which stays as it was fetched. A voice-over
    is not asked for the pause check (its picture only guides the service), but a segment ffmpeg skipped fails every stream
    copy: the service refused each voice-over short of segments (1 % of frames matched) and took each whole one.

    `progressive` marks one ready file address: it is asked for in parts at once, and by one stream when the server
    cannot serve parts. The name is owned from the first step to the last: the programs, their leftovers and the publication."""
    if not os.path.isabs(full_output_path):
        return False, "путь сохранения должен быть абсолютным"
    try:
        with storage.hold(full_output_path):
            done = await _download_owned(video_url, full_output_path, on_progress, headers, threads, progressive)
            return await _whole_or_discarded(done, full_output_path) if whole_video else done
    except storage.StorageBusy:  # an operation on this name has not ended: a file written now could be lost
        return False, "путь занят незавершённой файловой операцией (timeout)"


async def _whole_or_discarded(done: tuple[bool, str], path: str) -> tuple[bool, str]:
    """A download that says it is done is checked for pieces it lost. A file with a hole is not kept; a look that could not
    be made proves nothing, so the file is kept and the download is not finished. Both are failures the queue tries again."""
    if not done[0]:
        return done
    seen = await video_gaps(path, longer_than=HOLE_S)
    if not seen.checked:
        return False, f"обрыв: проверка целостности видео не выполнена ({seen.reason}) — загрузка повторится"
    if not seen.holes:
        return done
    await storage.discard(path)
    lost = sum(length for _, length in seen.holes)
    return False, (f"обрыв: в видео нет кадров {lost:.0f} с (кусков {len(seen.holes)}, первый на {seen.holes[0][0]:.0f}-й "
                   "секунде) — неполная загрузка")


async def _download_ranged_file(video_url: str, headers: dict, full_output_path: str, on_progress: ProgressCb,
                                threads: int) -> tuple[bool, str] | None:
    """The file in parts, published like a direct download; None when the server cannot serve parts."""
    tmp_out = full_output_path + ".download.mp4"
    try:
        ok, error = await ranged.download_ranged(video_url, headers, tmp_out, on_progress, threads=threads)
    except ranged.RangeUnsupported:
        return None
    if not ok:
        return False, error
    if await _local_duration(tmp_out) <= 0:
        await storage.discard(tmp_out)
        return False, "обрыв: скачанный файл не читается"
    await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))
    await on_progress({"phase": "download", "progress": 100, "eta": 0, "speed": 0.0})
    return True, ""


async def _download_owned(video_url: str, full_output_path: str, on_progress: ProgressCb,
                          headers: dict | None, threads: int, progressive: bool = False) -> tuple[bool, str]:
    await storage.call(os.makedirs, os.path.dirname(full_output_path), exist_ok=True)
    if await storage.call(os.path.exists, full_output_path):
        return True, "файл уже существует"

    if progressive:
        done = await _download_ranged_file(video_url, headers or {}, full_output_path, on_progress, threads)
        return done if done is not None else await _download_direct_ffmpeg(video_url, headers or {}, full_output_path,
                                                                           on_progress)

    # A resolved stream carries the headers its player asked for; a bare mp4 link is read as it is.
    if headers or (video_url.startswith("http") and ".mp4" in video_url and "vk.com" not in video_url and "vkvideo.ru" not in video_url):
        return await _download_direct_ffmpeg(video_url, headers or {}, full_output_path, on_progress)

    # длительность для процента remux — из yt-dlp (метаданные источника),
    # НЕ из промежуточного TS (находка 52)
    duration = await _probe_duration(video_url)
    raw = await _phase_download(video_url, full_output_path, on_progress,
                                threads, HLS_FORMAT)
    if not isinstance(raw, str) and not _unavailable_error(raw[1]):
        # VK HLS fragments are fragile; progressive/DASH retry is reliable.
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
        raw = await _phase_download(video_url, full_output_path, on_progress,
                                    threads, DASH_FALLBACK_FORMAT)
    if not isinstance(raw, str):
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
        return raw  # (False, error)
    try:
        return await _phase_remux(raw, full_output_path, on_progress,
                                  duration)
    finally:
        # A partial final must never be accepted as ready (finding 50).
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)


async def _phase_download(video_url: str, full_output_path: str,
                          on_progress: ProgressCb, threads: int,
                          format_selector: str):
    """yt-dlp (формат — вызывающая сторона: HLS или DASH-фолбэк), без
    постобработки. Возвращает путь промежуточного файла (str) или
    (False, error)."""
    executable = shutil.which("yt-dlp")
    if not executable:
        return False, "yt-dlp не найден в PATH"
    out_template = full_output_path + ".download.%(ext)s"

    proc = await spawn(
        executable, "--progress", "--newline", "--fixup", "never",
        "-f", format_selector, "-N", str(threads),
        "-o", out_template, video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
    stopped = ""
    started = time.monotonic()
    try:
        assert proc.stdout is not None
        while True:
            try:
                line = await _next_line(proc, "yt-dlp", started)
            except _Silent as exc:
                stopped = str(exc)
                break
            if not line:
                break
            data = parse_progress_line(line.decode("utf-8", errors="replace"))
            if data:
                await on_progress(data)
        if not stopped:
            await proc.wait()
    finally:
        if proc.returncode is None:  # отмена/сбой — убить процесс (находка 45)
            proc.kill()
            await proc.wait()
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if stopped:
        return False, stopped
    if proc.returncode != 0:
        error = _ytdlp_error(
            tail, f"yt-dlp завершился с кодом {proc.returncode}")
        if "Video unavailable" in tail or "Private video" in tail:
            error = "видео недоступно или приватно"
        return False, error

    raw = await storage.call(_download_artifact, full_output_path)
    if raw is None:
        return False, "yt-dlp не оставил файл после загрузки"
    return raw


async def _phase_remux(raw: str, full_output_path: str,
                       on_progress: ProgressCb,
                       duration: float) -> tuple[bool, str]:
    """Наш ffmpeg -c copy с -progress pipe:1: remux в mp4, прогресс и ETA
    из метрик ffmpeg (out_time/длительность, speed). duration — из
    yt-dlp (надёжно), не из промежуточного TS (находка 52)."""
    executable = shutil.which("ffmpeg")
    if not executable:
        return False, "ffmpeg не найден в PATH"
    await on_progress({"phase": "remux", "progress": 0, "eta": 0,
                       "speed": 0.0})

    # Атомарность (находка 50): ffmpeg пишет во временный файл, в финал
    # переименовываем ТОЛЬКО при успехе. Прерванный/зависший remux не
    # оставляет частичного финального файла, который потом приняли бы за
    # готовый ('файл существует' в download()).
    tmp_out = full_output_path + ".remux.mp4"
    # БЕЗ +faststart: на больших файлах он делает второй полный проход
    # (переписать весь файл ради moov в начале) — минуты после
    # progress=100 без метрик, «вечное зависание на 100%» (находка 51).
    # Для медиатеки faststart не нужен (легаси его не использовал).
    proc = await spawn(
        executable, "-y", "-i", raw, "-c", "copy",
        "-progress", "pipe:1", "-nostats", tmp_out,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    # stderr дренируем параллельно: битый VK-hls сыпет тысячи
    # 'Packet corrupt' в stderr, без вычитки буфер переполняется и
    # ffmpeg намертво виснет на записи в него (deadlock — находка 48).
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
    fields: dict[str, str] = {}
    stopped = ""
    started = time.monotonic()
    try:
        assert proc.stdout is not None
        while True:
            try:
                line = await _next_line(proc, "ffmpeg remux", started)
            except _Silent as exc:
                stopped = str(exc)
                break
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
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if stopped:
        await storage.discard(tmp_out)
        return False, stopped
    if proc.returncode == 0:
        await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))  # atomic publication
        await on_progress({"phase": "remux", "progress": 100, "eta": 0,
                           "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    return False, f"ffmpeg remux: {last or proc.returncode}"
