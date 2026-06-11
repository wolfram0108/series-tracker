"""Нормализация дат трекеров.

ВАЖНО (находка 13): итоговые строки — это МАРКЕРЫ сравнения с
историческими значениями в прод-БД (`torrents.date_time`), формат
каждого трекера сохраняется байт-в-байт:
  kinozal/rutracker/anilibria_tv: 'DD.MM.YYYY HH:MM:SS'
  anilibria: ISO 8601 c 'Z'      ('2026-03-17T20:13:16Z')
  astar:     'DD.MM.YYYY'
Смена формата маркера == ложное «все раздачи обновились» при
переключении (массовая перекачка).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

MOSCOW_TZ = timezone(timedelta(hours=3))

_MONTHS_FULL = {
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04",
    "мая": "05", "июня": "06", "июля": "07", "августа": "08",
    "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
}
_MONTHS_SHORT = {
    "янв": "01", "фев": "02", "мар": "03", "апр": "04", "мая": "05",
    "июн": "06", "июл": "07", "авг": "08", "сен": "09", "окт": "10",
    "ноя": "11", "дек": "12",
    "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05",
    "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10",
    "nov": "11", "dec": "12",
}


class DateParseError(ValueError):
    pass


def kinozal_date(raw: str, *, now: datetime | None = None) -> str:
    """'сегодня в 10:15' / 'вчера в 23:01' / '5 мая 2026 в 10:15'
    -> 'DD.MM.YYYY HH:MM:00' (московское время — так показывает сайт)."""
    raw = raw.strip().lower()
    current = now or datetime.now(MOSCOW_TZ)
    try:
        if "сегодня" in raw or "вчера" in raw:
            time_part = raw.split("в ")[1].strip()
            day = current - timedelta(days=1) if "вчера" in raw else current
            return day.strftime("%d.%m.%Y") + f" {time_part}:00"
        date_part, time_part = (s.strip() for s in raw.split(" в "))
        day, month_name, year = date_part.split()
        month = _MONTHS_FULL.get(month_name)
        if not month:
            raise DateParseError(f"неизвестный месяц: {month_name!r}")
        return f"{day.zfill(2)}.{month}.{year} {time_part}:00"
    except (IndexError, ValueError) as exc:
        raise DateParseError(f"дата Kinozal не разобрана: {raw!r}") from exc


def rutracker_date(raw: str) -> str:
    """'04-Ноя-25 10:18' -> '04.11.2025 10:18:00'."""
    match = re.match(
        r"(\d{1,2})-([А-Яа-яA-Za-zёЁ]{3})-(\d{2})\s+(\d{1,2}:\d{2})",
        raw.strip())
    if not match:
        raise DateParseError(f"дата RuTracker не разобрана: {raw!r}")
    day, month_name, year2, hhmm = match.groups()
    month = _MONTHS_SHORT.get(month_name.lower())
    if not month:
        raise DateParseError(f"неизвестный месяц: {month_name!r}")
    year = ("20" if int(year2) < 50 else "19") + year2
    return f"{day.zfill(2)}.{month}.{year} {hhmm}:00"


def anilibria_date(iso: str) -> str:
    """ISO API ('...+00:00') -> исторический ISO с 'Z'."""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def anilibria_tv_date(iso: str) -> str:
    """ISO из data-datetime -> 'DD.MM.YYYY HH:MM:SS'."""
    try:
        return datetime.fromisoformat(iso).strftime("%d.%m.%Y %H:%M:%S")
    except (ValueError, TypeError) as exc:
        raise DateParseError(f"дата Anilibria.TV не разобрана: {iso!r}") from exc


def astar_date(raw: str) -> str:
    """'05-03-2026' -> '05.03.2026'."""
    try:
        return datetime.strptime(raw.strip(), "%d-%m-%Y").strftime("%d.%m.%Y")
    except ValueError as exc:
        raise DateParseError(f"дата Astar не разобрана: {raw!r}") from exc
