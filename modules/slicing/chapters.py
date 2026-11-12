"""Главы компиляций: получение через yt-dlp и фильтрация мусора.

Эвристики фильтра — порт старого ChapterFilter 1:1 (это контракт UI:
та же разметка опенингов/эндингов при тех же названиях). Получение
глав — асинхронный yt-dlp с таймаутом (180 с, как в оригинале).
"""
from __future__ import annotations

import asyncio
import json
import re

from core.proc import program, run_capture
from core.reasons import reason

YTDLP_TIMEOUT = 180.0

GARBAGE_PATTERNS = [
    # опенинги и эндинги
    r"op\b", r"opening", r"опенинг", r"опенинги", r"opening\s*\d*",
    r"ed\b", r"ending", r"эндинг", r"эндинги", r"ending\s*\d*",
    # промо-материалы
    r"promo", r"pv", r"preview", r"трейлер", r"промо", r"анонс",
    # другой мусор
    r"credits?", r"титры", r"интро", r"outro", r"перерыв",
    r"recap", r"повтор", r"preview", r"предпросмотр",
    # нумерация опенингов/эндингов
    r"op\s*\d+", r"ed\s*\d+", r"opening\s*\d+", r"ending\s*\d+",
]

MIN_DURATION_SECONDS = 30

_OPENING_KEYWORDS = ["op", "opening", "оп", "опенинг", "тема", "intro"]
_ENDING_KEYWORDS = ["ed", "ending", "эн", "эндинг", "титры", "конец",
                    "outro", "credits"]


def format_seconds(seconds) -> str:
    if not isinstance(seconds, (int, float)):
        return "00:00:00"
    seconds = int(seconds)
    return (f"{seconds // 3600:02}:{(seconds % 3600) // 60:02}:"
            f"{seconds % 60:02}")


def time_to_seconds(time_str: str) -> int | None:
    try:
        parts = [int(p) for p in str(time_str).split(":")]
    except (ValueError, AttributeError):
        return None
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return None


def is_garbage_chapter(chapter: dict, index: int, total: int,
                       next_time: int | None = None
                       ) -> tuple[bool, str | None]:
    # A chapter yt-dlp gave no title (None; the window words it) is judged by its length alone.
    untitled = chapter.get("title") is None
    title = "" if untitled else (chapter.get("title") or "").lower().strip()
    for pattern in () if untitled else GARBAGE_PATTERNS:
        if re.search(pattern, title, re.IGNORECASE):
            return True, reason("chapter_matches_pattern", pattern=pattern)
    if not untitled and index == 0 and any(k in title for k in _OPENING_KEYWORDS):
        return True, reason("chapter_first_like_opening")
    if not untitled and index == total - 1 and any(k in title for k in _ENDING_KEYWORDS):
        return True, reason("chapter_last_like_ending")
    # Длительность главы = интервал до СЛЕДУЮЩЕЙ главы, а не её время
    # начала (находка 35/54): иначе глава в 00:00:00 всегда «0 сек».
    # Последнюю главу (next_time неизвестно) по длительности не судим.
    start = time_to_seconds(chapter.get("time", ""))
    if next_time is not None and start is not None:
        duration = next_time - start
        if 0 <= duration < MIN_DURATION_SECONDS:
            return True, reason("chapter_too_short", seconds=duration)
    if (not untitled and index in (0, total - 1) and len(title) < 10
            and not any(c.isdigit() for c in title)):
        return True, reason("chapter_short_title_at_edge")
    return False, None


def _next_time(chapters: list[dict], index: int) -> int | None:
    if index + 1 < len(chapters):
        return time_to_seconds(chapters[index + 1].get("time", ""))
    return None


def filter_chapters(chapters: list[dict]) -> list[dict]:
    """Только хорошие главы (с метками is_garbage=False)."""
    result = []
    for index, chapter in enumerate(chapters):
        garbage, garbage_reason = is_garbage_chapter(
            chapter, index, len(chapters), _next_time(chapters, index))
        if not garbage:
            result.append({**chapter, "is_garbage": False,
                           "garbage_reason": garbage_reason})
    return result


def garbage_chapters(chapters: list[dict]) -> list[dict]:
    result = []
    for index, chapter in enumerate(chapters):
        garbage, garbage_reason = is_garbage_chapter(
            chapter, index, len(chapters), _next_time(chapters, index))
        if garbage:
            result.append({**chapter, "is_garbage": True,
                           "garbage_reason": garbage_reason,
                           "original_index": index})
    return result


def mark_manually(chapters: list[dict],
                  garbage_indices: list[int]) -> list[dict]:
    return [{**ch, "is_garbage": i in garbage_indices,
             "garbage_reason": (reason("chapter_marked_by_hand") if i in garbage_indices
                                else None),
             "original_index": i}
            for i, ch in enumerate(chapters)]


async def fetch_chapters(video_url: str) -> list[dict]:
    """yt-dlp --print %(chapters)j -> [{'time': 'HH:MM:SS', 'title': str}].
    Пустой список — глав нет; исключение — реальная ошибка."""
    executable = program("yt-dlp")
    if not executable:
        raise RuntimeError(reason("chapters_ytdlp_missing"))
    try:
        returncode, stdout, stderr = await run_capture(
            [executable, "--print", "%(chapters)j", "--no-warnings",
             "--no-progress", "--no-cache-dir", video_url], timeout=YTDLP_TIMEOUT)
    except asyncio.TimeoutError:
        raise RuntimeError(reason("chapters_ytdlp_timeout",
                                  seconds=round(YTDLP_TIMEOUT))) from None
    if returncode != 0:
        raise RuntimeError(reason("chapters_ytdlp_failed",
                                  detail=stderr.decode("utf-8", "replace").strip()))
    output = stdout.decode("utf-8", "replace").strip()
    if not output:
        return []
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return []
    if not data:
        return []
    return [{"time": format_seconds(ch.get("start_time")),
             "title": ch.get("title")} for ch in data]
