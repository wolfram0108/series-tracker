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
from typing import Awaitable, Callable

import httpx

ProgressCb = Callable[[dict], Awaitable[None]]

# Decoded manifests live on disk while their segments stay remote (Vibix).
PROTOCOL_WHITELIST = ["-protocol_whitelist", "file,http,https,tcp,tls"]

# hls + лучший muxed-поток; fallback на обычный best, если hls нет
# (тогда без ускорения, но загрузка работает).
HLS_FORMAT = "b[protocol*=m3u8]/b"

# Отказ от m3u8: на некоторых VK-роликах CDN отдаёт 404 на хвостовые
# HLS-фрагменты (манифест шире, чем реально долитые сегменты) — весь
# файл выходит пустым. DASH/progressive не фрагментированы так же
# (одно соединение на поток) — не подвержены этой дыре, но без -N
# параллелизма (медленнее).
DASH_FALLBACK_FORMAT = "b[protocol!*=m3u8]"
_EMPTY_FILE_ERROR = "The downloaded file is empty"

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


async def _drain_stderr(stream) -> list[bytes]:
    """Фоновое вычитывание stderr, чтобы процесс не блокировался на
    переполнении буфера (deadlock — находка 48: битый VK-hls сыпет
    тысячи 'Packet corrupt' в stderr ffmpeg). Копим только хвост для
    текста ошибки."""
    tail: list[bytes] = []
    while True:
        line = await stream.readline()
        if not line:
            break
        tail.append(line)
        if len(tail) > 50:
            del tail[0]
    return tail


async def _probe_duration(video_url: str) -> float:
    """Длительность видео из yt-dlp (метаданные источника — НАДЁЖНО).
    ffprobe промежуточного MPEG-TS даёт заниженную длительность (находка
    52: 7343с вместо реальных 102787с), из-за чего процент remux был бы
    100% сразу. yt-dlp --print даёт точное значение из метаданных VK."""
    executable = shutil.which("yt-dlp")
    if not executable:
        return 0.0
    proc = await asyncio.create_subprocess_exec(
        executable, "--no-download", "--print", "%(duration)s", video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except (ValueError, AttributeError):
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


def is_allvideo_url(url: str) -> bool:
    # Match embed player pages, not direct stream URLs.
    return "/embed/" in url and any(d in url for d in ("fsst.online", "incvideo1.online"))


def _extract_best_stream_from_playerjs(file_val: str) -> str | None:
    streams: list[tuple[int, str]] = []
    for chunk in file_val.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        m = re.search(r'\[(?:(\d+)p?|(4k))\]', chunk, re.IGNORECASE)
        if m:
            tag_num, tag_4k = m.group(1), m.group(2)
            quality = 2160 if tag_4k else int(tag_num)
            stream_url = re.sub(r'\[[^\]]+\]', '', chunk).strip()
            if stream_url:
                streams.append((quality, stream_url))
        else:
            cleaned = re.sub(r'\[[^\]]+\]', '', chunk).strip()
            if cleaned:
                streams.append((0, cleaned))
    if streams:
        streams.sort(key=lambda s: s[0], reverse=True)
        return streams[0][1]
    return file_val.split(',')[-1].split(']')[-1].strip() or None


async def _resolve_allvideo_stream(embed_url: str) -> tuple[str, str] | None:
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True,
                                     headers={"Referer": "https://1.ruchime.org/"}) as client:
            r = await client.get(embed_url)
            m = re.search(r'file:\s*["\']([^"\']+)["\']', r.text)
            if not m:
                return None
            mp4_url = _extract_best_stream_from_playerjs(m.group(1))
            if not mp4_url:
                return None
            return mp4_url, "https://incvideo1.online/"
    except Exception:
        return None


def _headers_args(stream_url: str, referer: str) -> list[str]:
    """A local decoded manifest rejects HTTP options like -headers."""
    if referer and not stream_url.startswith("file:"):
        return ["-headers", f"Referer: {referer}\r\n"]
    return []


async def _probe_duration_ffprobe(stream_url: str, referer: str = "") -> float:
    executable = shutil.which("ffprobe")
    if not executable:
        return 0.0
    cmd = [executable, "-v", "error"]
    cmd.extend(PROTOCOL_WHITELIST)
    cmd.extend(_headers_args(stream_url, referer))
    cmd.extend(["-show_entries", "format=duration", "-of",
                "default=noprint_wrappers=1:nokey=1", stream_url])
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        if proc.returncode == 0 and out:
            return float(out.decode().strip())
    except Exception:
        pass
    return 0.0


async def _download_direct_ffmpeg(stream_url: str, referer: str,
                                  full_output_path: str,
                                  on_progress: ProgressCb) -> tuple[bool, str]:
    executable = shutil.which("ffmpeg")
    if not executable:
        return False, "ffmpeg не найден в PATH"
    duration = await _probe_duration_ffprobe(stream_url, referer)
    tmp_out = full_output_path + ".download.mp4"
    await on_progress({"phase": "download", "progress": 0, "eta": 0, "speed": 0.0})

    cmd = [executable, "-y"]
    cmd.extend(PROTOCOL_WHITELIST)
    cmd.extend(_headers_args(stream_url, referer))
    cmd.extend(["-i", stream_url, "-c", "copy", "-progress", "pipe:1", "-nostats", tmp_out])

    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
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
            k, v = text.split("=", 1)
            fields[k] = v
            if k == "progress":
                await on_progress(ffmpeg_progress(fields, duration))
                fields = {}
        await proc.wait()
    finally:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if proc.returncode == 0:
        os.replace(tmp_out, full_output_path)
        await on_progress({"phase": "download", "progress": 100, "eta": 0, "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    return False, f"ffmpeg direct download: {last or proc.returncode}"


async def download(video_url: str, full_output_path: str,
                   on_progress: ProgressCb, *,
                   headers: dict | None = None,
                   threads: int = 6) -> tuple[bool, str]:
    """Two-phase video download. Supports direct ffmpeg copy and yt-dlp."""
    if not os.path.isabs(full_output_path):
        return False, "путь сохранения должен быть абсолютным"
    os.makedirs(os.path.dirname(full_output_path), exist_ok=True)
    if os.path.exists(full_output_path):
        return True, "файл уже существует"

    # Direct stream download if headers (e.g. Referer) are provided or URL is already an mp4 stream
    if headers or (video_url.startswith("http") and ".mp4" in video_url and "vk.com" not in video_url and "vkvideo.ru" not in video_url):
        referer = (headers or {}).get("Referer", "https://incvideo1.online/")
        return await _download_direct_ffmpeg(video_url, referer, full_output_path, on_progress)

    if is_allvideo_url(video_url):
        resolved = await _resolve_allvideo_stream(video_url)
        if not resolved:
            return False, "не удалось получить ссылку на поток AllVideo"
        stream_url, referer = resolved
        return await _download_direct_ffmpeg(stream_url, referer, full_output_path, on_progress)

    # длительность для процента remux — из yt-dlp (метаданные источника),
    # НЕ из промежуточного TS (находка 52)
    duration = await _probe_duration(video_url)
    raw = await _phase_download(video_url, full_output_path, on_progress,
                                threads, HLS_FORMAT)
    if not isinstance(raw, str) and _EMPTY_FILE_ERROR in raw[1]:
        # хвост неудачной HLS-попытки (недокачанные .part/фрагменты) собьёт
        # DASH-попытку с тем же выходным путём — чистим перед повтором.
        for leftover in download_artifacts(full_output_path):
            try:
                os.remove(leftover)
            except OSError:
                pass
        raw = await _phase_download(video_url, full_output_path, on_progress,
                                    threads, DASH_FALLBACK_FORMAT)
    if not isinstance(raw, str):
        return raw  # (False, error)
    try:
        return await _phase_remux(raw, full_output_path, on_progress,
                                  duration)
    finally:
        # подчищаем промежуточные и недоделанный remux-файл (находка 50:
        # частичный финал нельзя принимать за готовый)
        for leftover in download_artifacts(full_output_path):
            try:
                os.remove(leftover)
            except OSError:
                pass


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

    proc = await asyncio.create_subprocess_exec(
        executable, "--progress", "--newline", "--fixup", "never",
        "-f", format_selector, "-N", str(threads),
        "-o", out_template, video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
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
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if proc.returncode != 0:
        error = tail.strip().splitlines()[-1] if tail.strip() else \
            f"yt-dlp завершился с кодом {proc.returncode}"
        if "Video unavailable" in tail or "Private video" in tail:
            error = "видео недоступно или приватно"
        return False, error

    raw = _download_artifact(full_output_path)
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
    proc = await asyncio.create_subprocess_exec(
        executable, "-y", "-i", raw, "-c", "copy",
        "-progress", "pipe:1", "-nostats", tmp_out,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    # stderr дренируем параллельно: битый VK-hls сыпет тысячи
    # 'Packet corrupt' в stderr, без вычитки буфер переполняется и
    # ffmpeg намертво виснет на записи в него (deadlock — находка 48).
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr))
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
    tail = b"".join(await stderr_task).decode("utf-8", errors="replace")

    if proc.returncode == 0:
        os.replace(tmp_out, full_output_path)  # атомарная публикация
        await on_progress({"phase": "remux", "progress": 100, "eta": 0,
                           "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    return False, f"ffmpeg remux: {last or proc.returncode}"
