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
import contextlib
import json
import os
import re
import time
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from core import storage
from core.ffprobe import duration_of, readable_length
from core.proc import LOST, REAP_TIMEOUT_S, exited, program, run_capture, spawn, stop
from core.reasons import reason
from core.retry import UNTIMED, Budget, retrying

from . import hls_native, ranged
from . import failure
from .failure import Failed, Final, Unasked, kind_of

ProgressCb = Callable[[dict], Awaitable[None]]

# Decoded manifests live on disk while their segments stay remote (Vibix).
PROTOCOL_WHITELIST = ["-protocol_whitelist", "file,http,https,tcp,tls"]

# CDNs of the web players stall on open and drop a stream halfway: ffmpeg reconnects instead of failing.
RECONNECT_ARGS = ["-reconnect", "1", "-reconnect_streamed", "1",
                  "-reconnect_on_network_error", "1", "-reconnect_delay_max", "30"]
# A refused segment is asked for again by the HLS demuxer (by default it skips it); one still refused after these looks ends the
# stream there and ffmpeg exits 0 with a file that stops short. The count is that of the looks the CDN gets elsewhere here.
SEGMENT_RETRIES = 3
# A file shorter than the stream announces by more than this lacks a piece (the service refuses a voice-over short of one);
# a frame or an audio packet less is the end of a whole file.
HOLE_S = 1.0
# ffmpeg prints this line at every refusal of a segment, also of one the next look took: it explains an incomplete file and
# does not make one, which is judged by what the file holds.
_REFUSED_SEGMENT = re.compile(rb"Failed to open segment (\d+) of playlist \d+")
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


def capped(selector: str, max_height: int | None) -> str:
    """The selector under a cap of height, as a player picks a file (`sources.players.vk_files.pick_file`): the best of
    each alternative not above the cap, else the lowest of them; the selector as it is without a cap."""
    if not max_height:
        return selector
    alternatives = selector.split("/")
    assert all(each.startswith("b") for each in alternatives), selector
    return "/".join([f"{each}[height<={max_height}]" for each in alternatives] + ["w" + each[1:] for each in alternatives])
_WARNING_LINE = re.compile(
    r"^\s*(?:WARNING:|It is strongly recommended|Run \"yt-dlp --update\"|"
    r"To suppress this warning)", re.IGNORECASE)


# A child a signal ended was interrupted (the service stops: systemd signals every process of it at once, before the module
# cancels its work); it says nothing of the source, and the download goes on when it is asked again.
INTERRUPTED = "interrupted_by_signal"


def _interrupted(returncode: int | None, tail: str) -> bool:
    """Whether a signal ended the child: a negative code, or ffmpeg that caught one and says so."""
    return (returncode is not None and returncode < 0) or "received signal" in tail.lower()


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


async def _drain_stderr(stream, tail: list[bytes], refused: list[int] | None = None) -> None:
    """Read stderr in the background so a full pipe never blocks the process (a broken VK HLS floods ffmpeg with thousands of
    'Packet corrupt' lines). Only the tail is kept for the error text, in `tail` as the lines arrive; the numbers of refused
    segments go to `refused` before the tail pushes them out."""
    while True:
        line = await stream.readline()
        if not line:
            break
        if refused is not None and (match := _REFUSED_SEGMENT.search(line)):
            refused.append(int(match.group(1)))
        tail.append(line)
        if len(tail) > 50:
            del tail[0]


async def _stderr_tail(drain: asyncio.Future, tail: list[bytes]) -> str:
    """The tail of stderr of a program that has ended. A child it left behind may keep the pipe open: it is not waited for."""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(drain, REAP_TIMEOUT_S)
    return b"".join(tail).decode("utf-8", errors="replace")


class _Silent(Exception):
    """A running program printed nothing for too long, or ran past its total time."""


async def _next_line(proc, program: str, started: float, total: float | None = None) -> bytes:
    """The next line of a program's output; the program is declared silent after `failure.SILENCE_S`."""
    try:
        line = await asyncio.wait_for(proc.stdout.readline(), failure.SILENCE_S)
    except asyncio.TimeoutError:
        raise _Silent(reason("program_silent", program=program, seconds=f"{failure.SILENCE_S:.0f}")) from None
    if total is not None and time.monotonic() - started > total:
        raise _Silent(reason("program_too_long", program=program, seconds=f"{total:.0f}"))
    return line


async def _probe_duration(video_url: str) -> float:
    """Длительность видео из yt-dlp (метаданные источника — НАДЁЖНО).
    ffprobe промежуточного MPEG-TS даёт заниженную длительность (находка
    52: 7343с вместо реальных 102787с), из-за чего процент remux был бы
    100% сразу. yt-dlp --print даёт точное значение из метаданных VK."""
    executable = program("yt-dlp")
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
    """Seconds of a file on disk as far as its data goes, not as its header states; 0 when it cannot be read."""
    return await readable_length(path, timeout=PROBE_TIMEOUT_S)


async def _download_direct_ffmpeg(stream_url: str, headers: dict,
                                  full_output_path: str,
                                  on_progress: ProgressCb, announced: float | None = None, *,
                                  audio_url: str | None = None) -> tuple[bool, str]:
    executable = program("ffmpeg")
    if not executable:
        return False, Unasked(reason("program_missing", program="ffmpeg"))
    duration = announced if announced is not None else await _probe_duration_ffprobe(stream_url, headers)
    audio_duration = await _probe_duration_ffprobe(audio_url, headers) if audio_url else 0.0
    tmp_out = full_output_path + ".download.mp4"
    await on_progress({"phase": "download", "progress": 0, "eta": 0, "speed": 0.0})

    cmd = [executable, "-y"]
    cmd.extend(PROTOCOL_WHITELIST)
    cmd.extend(_reconnect_args(stream_url))
    cmd.extend(_segment_args(stream_url))
    cmd.extend(_headers_args(stream_url, headers))
    cmd.extend(["-i", stream_url])
    if audio_url:
        cmd.extend(PROTOCOL_WHITELIST)
        cmd.extend(_reconnect_args(audio_url))
        cmd.extend(_headers_args(audio_url, headers))
        cmd.extend(["-i", audio_url, "-map", "0:V:0", "-map", "1:a:0"])
    cmd.extend(["-c", "copy", "-progress", "pipe:1", "-nostats", tmp_out])

    proc = await spawn(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    refused: list[int] = []
    lines: list[bytes] = []
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr, lines, refused))
    code: int | None = None
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
            code = await exited(proc)
    finally:
        if code is None:
            code = await stop(proc)
    tail = await _stderr_tail(stderr_task, lines)

    if stopped:
        await storage.discard(tmp_out)
        return False, stopped
    # An end whose status was lost tells nothing (core/proc.LOST, the code a signal also ends ffmpeg with): unless its
    # words say a signal stopped it, the file is what is judged, by its length.
    if code == 0 or (code == LOST and not _interrupted(code, tail)):
        got = await _local_duration(tmp_out)
        if audio_url:
            # The longer track must not conceal a cut in the other one after remuxing rewrites their headers.
            lengths = await _paired_lengths(tmp_out)
            for kind, expected in (("video", duration), ("audio", audio_duration)):
                length = lengths.get(kind, 0.0)
                if length <= 0 or short_of(length, expected):
                    await storage.discard(tmp_out)
                    return False, reason("cut_short", got=f"{length:.0f}", expected=f"{expected:.0f}")
        if got <= 0 or short_of(got, duration):
            await storage.discard(tmp_out)
            if refused:
                return False, reason("cut_short_refused", got=f"{got:.0f}", expected=f"{duration:.0f}",
                                     refusals=len(set(refused)), first=refused[0])
            return False, reason("cut_short", got=f"{got:.0f}", expected=f"{duration:.0f}")
        await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))
        await on_progress({"phase": "download", "progress": 100, "eta": 0, "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    if _interrupted(code, tail):
        return False, Unasked(reason(INTERRUPTED, program="ffmpeg direct download", cause=str(last or code)))
    return False, reason("program_failed", program="ffmpeg direct download", cause=str(last or code))


async def _paired_lengths(path: str) -> dict[str, float]:
    """Check both copied tracks before publishing a paired DASH download."""
    executable = program("ffprobe")
    if not executable:
        return {}
    try:
        code, out, _ = await run_capture([executable, "-v", "error", "-show_entries",
                                          "stream=codec_type,duration", "-of", "json", path],
                                         timeout=PROBE_TIMEOUT_S)
        return {track["codec_type"]: float(track.get("duration") or 0)
                for track in json.loads(out).get("streams", [])} if code == 0 else {}
    except (OSError, TimeoutError, ValueError, KeyError):
        return {}


def _unavailable_error(error: str) -> bool:
    # A refusal we named ourselves is typed Final; the words are the English of yt-dlp's own errors.
    if isinstance(error, Final):
        return True
    lowered = (error or "").lower()
    return "video unavailable" in lowered or "private video" in lowered


async def download(video_url: str, full_output_path: str,
                   on_progress: ProgressCb, *,
                   headers: dict | None = None,
                   threads: int = 6,
                   progressive: bool = False,
                   max_height: int | None = None,
                   playlist: bool = False,
                   resumable: bool = False,
                   fetcher=None,
                   audio_url: str | None = None) -> tuple[bool, str]:
    """Two-phase video download. Supports parts of a ready file, direct ffmpeg copy and yt-dlp.

    A download that says it is done is taken only at the length its source announced: a stream copy short of it fails (the
    service refused each voice-over short of segments and took each whole one).

    `audio_url` supplies a separate complete audio file for a DASH video track; both are copied into one MP4.
    `progressive` marks one ready file address: it is asked for in parts at once, and by one stream when the server
    cannot serve parts. `max_height` caps a page the downloader reads itself (a resolved stream is one file already).
    The name is owned from the first step to the last: the programs, their leftovers and the publication.

    `playlist` marks an HLS playlist a player resolved: it is downloaded by its segments (modules/downloads/hls_native.py).
    `fetcher`: the player takes the segments itself (`async (raw path, on_progress) -> (ok, words, seconds, longest)`);
    the stream is then copied and checked as any playlist.
    `resumable`: the caller asks for this file again after a failure of the way to it — the segments a broken transfer
    took stay next to the name (`download_artifacts`) and the next ask continues them; the caller removes them when it
    gives the file up.

    The words of a failure tell what it says of the source (modules/downloads/failure.py)."""
    if not os.path.isabs(full_output_path):
        return False, reason("save_path_relative")
    try:
        with storage.hold(full_output_path):
            if playlist:
                return await _download_playlist(video_url, full_output_path, on_progress, headers or {}, threads,
                                                resumable, fetcher)
            announced = (await _probe_duration_ffprobe(video_url, headers or {}) if audio_url
                         else await _announced_length(video_url, headers, progressive))
            await on_progress({"phase": "link", "source_seconds": announced or None})
            done = await _download_owned(video_url, full_output_path, on_progress, headers, threads, progressive,
                                         announced=announced, max_height=max_height, audio_url=audio_url)
            return await _as_long_as_announced(done, full_output_path, announced, on_progress)
    except storage.StorageBusy:  # an operation on this name has not ended: a file written now could be lost
        return False, Unasked(reason("path_busy"))


def short_of(got: float, announced: float) -> bool:
    """Whether a file is shorter than the length its source announced, beyond what a cut of a frame explains."""
    return announced > 0 and got < announced - HOLE_S


def _is_stream(video_url: str, headers: dict | None, progressive: bool) -> bool:
    """A link a player resolved (it carries its headers), a ready file, or a bare mp4: read by ffmpeg, not by yt-dlp."""
    return bool(progressive or headers or (video_url.startswith("http") and ".mp4" in video_url
                                           and "vk.com" not in video_url and "vkvideo.ru" not in video_url))


def multithreaded(video_url: str, headers: dict | None, progressive: bool, playlist: bool) -> bool:
    """Whether the download of a link takes its file by many connections at once (modules/downloads/limits.py): the
    segments of a playlist, the parts of a ready file, a page yt-dlp reads; a stream ffmpeg reads takes one."""
    return playlist or progressive or not _is_stream(video_url, headers, progressive)


async def _announced_length(video_url: str, headers: dict | None, progressive: bool) -> float:
    """The length the source states, asked the way its file is downloaded; 0 when it states none."""
    if _is_stream(video_url, headers, progressive):
        return await _probe_duration_ffprobe(video_url, headers or {})
    return await _probe_duration(video_url)


async def _download_playlist(video_url: str, full_output_path: str, on_progress: ProgressCb, headers: dict,
                             threads: int, resumable: bool = False, fetcher=None) -> tuple[bool, str]:
    """The playlist read by our client and saved, its segments by the native downloader of yt-dlp, the stream copied into
    the container by ffmpeg; the file is taken at the length the playlist states, short of it by up to its longest
    segment. The parts of a transfer that broke stay for a `resumable` download, and are continued by its next ask under
    the same listing of segments."""
    await storage.call(os.makedirs, os.path.dirname(full_output_path), exist_ok=True)
    if await storage.call(os.path.exists, full_output_path):
        return True, reason("file_already_there")
    await on_progress({"method": "hls"})
    done, seconds, tolerance = (False, ""), 0.0, HOLE_S
    if fetcher is not None:
        return await _playlist_by_player(fetcher, full_output_path, on_progress)
    try:
        try:
            snap = await hls_native.snapshot(video_url, headers, full_output_path)
        except hls_native.PlaylistError as exc:
            done = (False, exc.failure)
            return done
        seconds, tolerance = snap.seconds, max(HOLE_S, snap.tolerance)
        await on_progress({"phase": "link", "source_seconds": seconds or None})
        raw = full_output_path + ".download.ts"
        if await storage.call(hls_native.continued, raw, snap.listing, owns=full_output_path):
            hls_native.logger.info("%s: загрузка продолжается с полученных сегментов",
                                   os.path.basename(full_output_path))
        done = await hls_native.fetch(snap, headers, raw, threads, on_progress)
        if done[0]:
            done = await _phase_remux(raw, full_output_path, on_progress, seconds)
    finally:
        # The playlist, the stream and the leftovers of the downloader go, whatever the end — but for the parts of a
        # transfer whose way broke, when the caller asks again: an exception (a stop) leaves `done` a failure of no words.
        if not (resumable and not done[0] and done[1] and kind_of(done[1]) is Failed.BROKEN):
            await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
    return await _as_long_as_announced(done, full_output_path, seconds, on_progress, tolerance=tolerance)


def player_target(full_output_path: str) -> str:
    """The file a player writes the segments of a download into: a name of its own next to the file, outside the name the
    download holds (the player writes from its own module, which the hold of the download does not cover)."""
    return os.path.join(os.path.dirname(full_output_path), "." + os.path.basename(full_output_path) + ".player.ts")


async def _playlist_by_player(fetcher, full_output_path: str, on_progress: ProgressCb) -> tuple[bool, str]:
    """The segments taken by the player, moved into the raw stream of the download, copied into the container by ffmpeg
    and checked at the length of the playlist as any playlist; a transfer is never continued (the session of the player
    that took it is gone)."""
    raw, taken = full_output_path + ".download.ts", player_target(full_output_path)
    done, seconds, tolerance = (False, ""), 0.0, HOLE_S
    try:
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
        await storage.discard(taken)
        ok, words, seconds, longest = await fetcher(taken, on_progress)
        tolerance = max(HOLE_S, longest)
        done = (ok, words)
        if ok:
            await storage.call(os.replace, taken, raw, owns=(taken, raw))
            done = await _phase_remux(raw, full_output_path, on_progress, seconds)
    finally:
        await storage.discard(taken)
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
    return await _as_long_as_announced(done, full_output_path, seconds, on_progress, tolerance=tolerance)


async def _as_long_as_announced(done: tuple[bool, str], path: str, announced: float,
                                on_progress: ProgressCb | None = None, *, tolerance: float = HOLE_S) -> tuple[bool, str]:
    """A download that says it is done is taken only at the length its source announced: a program that stopped early
    (a killed run continued, a playlist cut short) still says it is done. A file shorter than that is not kept; a length
    the source does not state proves nothing, so the file is kept and the download is not finished."""
    if not done[0]:
        return done
    if on_progress is not None:
        await on_progress({"phase": "check"})
    got = await _local_duration(path)
    if on_progress is not None:
        await on_progress({"phase": "check", "got_seconds": got})
    if got <= 0:
        await storage.discard(path)
        return False, reason("cut_unreadable")
    if announced <= 0:
        return False, reason("length_unstated")
    if announced > 0 and got < announced - tolerance:
        await storage.discard(path)
        return False, reason("cut_short", got=f"{got:.0f}", expected=f"{announced:.0f}")
    return done


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
        return False, reason("file_unreadable")
    await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))
    await on_progress({"phase": "download", "progress": 100, "eta": 0, "speed": 0.0})
    return True, ""


async def _download_owned(video_url: str, full_output_path: str, on_progress: ProgressCb,
                          headers: dict | None, threads: int, progressive: bool = False, *,
                          announced: float | None = None, max_height: int | None = None,
                          audio_url: str | None = None) -> tuple[bool, str]:
    await storage.call(os.makedirs, os.path.dirname(full_output_path), exist_ok=True)
    if await storage.call(os.path.exists, full_output_path):
        return True, reason("file_already_there")

    if audio_url:
        await on_progress({"method": "ffmpeg"})
        return await _download_direct_ffmpeg(video_url, headers or {}, full_output_path, on_progress, announced,
                                             audio_url=audio_url)

    if progressive:
        await on_progress({"method": "ranged"})
        done = await _download_ranged_file(video_url, headers or {}, full_output_path, on_progress, threads)
        if done is not None:
            return done
        await on_progress({"method": "ffmpeg"})
        return await _download_direct_ffmpeg(video_url, headers or {}, full_output_path, on_progress, announced)

    # A resolved stream carries the headers its player asked for; a bare mp4 link is read as it is.
    if _is_stream(video_url, headers, progressive):
        await on_progress({"method": "ffmpeg"})
        return await _download_direct_ffmpeg(video_url, headers or {}, full_output_path, on_progress, announced)

    # длительность для процента remux — из yt-dlp (метаданные источника),
    # НЕ из промежуточного TS (находка 52)
    duration = announced if announced is not None else await _probe_duration(video_url)
    await on_progress({"method": "ytdlp_hls"})
    raw = await _phase_download(video_url, full_output_path, on_progress,
                                threads, capped(HLS_FORMAT, max_height))
    if not isinstance(raw, str) and not _unavailable_error(raw[1]):
        # VK HLS fragments are fragile; progressive/DASH retry is reliable.
        await storage.call(_cleanup_artifacts, full_output_path, owns=full_output_path)
        await on_progress({"method": "ytdlp_dash"})
        raw = await _phase_download(video_url, full_output_path, on_progress,
                                    threads, capped(DASH_FALLBACK_FORMAT, max_height))
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
    executable = program("yt-dlp")
    if not executable:
        return False, Unasked(reason("program_missing", program="yt-dlp"))
    out_template = full_output_path + ".download.%(ext)s"

    proc = await spawn(
        executable, "--progress", "--newline", "--fixup", "never",
        "-f", format_selector, "-N", str(threads),
        "-o", out_template, video_url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    lines: list[bytes] = []
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr, lines))
    stopped = ""
    code: int | None = None
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
            code = await exited(proc)
    finally:
        if code is None:  # отмена/сбой — убить процесс (находка 45)
            code = await stop(proc)
    tail = await _stderr_tail(stderr_task, lines)

    if stopped:
        return False, stopped
    if code != 0 and _interrupted(code, tail):
        return False, Unasked(reason(INTERRUPTED, program="yt-dlp",
                                     cause=reason("program_exit_code", program="yt-dlp", code=code)))
    # A lost status says nothing: the file yt-dlp left is judged by what follows (the remux and the check of its length).
    if code not in (0, LOST):
        error = _ytdlp_error(
            tail, reason("program_exit_code", program="yt-dlp", code=code))
        if "Video unavailable" in tail or "Private video" in tail:
            error = Final(reason("video_unavailable"))
        return False, error

    raw = await storage.call(_download_artifact, full_output_path)
    if raw is None:
        return False, reason("program_no_file", program="yt-dlp")
    return raw


async def _phase_remux(raw: str, full_output_path: str,
                       on_progress: ProgressCb,
                       duration: float) -> tuple[bool, str]:
    """Наш ffmpeg -c copy с -progress pipe:1: remux в mp4, прогресс и ETA
    из метрик ffmpeg (out_time/длительность, speed). duration — из
    yt-dlp (надёжно), не из промежуточного TS (находка 52)."""
    executable = program("ffmpeg")
    if not executable:
        return False, Unasked(reason("program_missing", program="ffmpeg"))
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
    lines: list[bytes] = []
    stderr_task = asyncio.ensure_future(_drain_stderr(proc.stderr, lines))
    fields: dict[str, str] = {}
    stopped = ""
    code: int | None = None
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
        if not stopped:
            code = await exited(proc)
    finally:
        if code is None:
            code = await stop(proc)
    tail = await _stderr_tail(stderr_task, lines)

    if stopped:
        await storage.discard(tmp_out)
        return False, stopped
    if code == 0 or (code == LOST and not _interrupted(code, tail) and duration > 0
                     and not short_of(await _local_duration(tmp_out), duration)):
        await storage.call(os.replace, tmp_out, full_output_path, owns=(tmp_out, full_output_path))  # atomic publication
        await on_progress({"phase": "remux", "progress": 100, "eta": 0,
                           "speed": 0.0})
        return True, ""
    last = tail.strip().splitlines()[-1] if tail.strip() else ""
    if _interrupted(code, tail):
        return False, Unasked(reason(INTERRUPTED, program="ffmpeg remux", cause=str(last or code)))
    return False, reason("program_failed", program="ffmpeg remux", cause=str(last or code))
