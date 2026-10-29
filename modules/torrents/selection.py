"""Выбор состава раздачи — чистые функции (как pipeline.py).

Единая точка правды «качать / не качать». Правило — белый список:
качается только файл, который система распознала как серию сериала и
который пользователь не исключил. Всё остальное (не-видео, файлы без
резолва сезона — их не создаёт renaming, файлы без номера серии) в qBit
получает приоритет 0 ещё до старта закачки.

Память исключений живёт в сериале (сезоны + номера серий), а не в
строках файлов: релиз заменяется, файлы пересоздаются — выбор обязан
пережить замену. Здесь она только применяется.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.ignored import covered, episodes_of, parse_memory  # noqa: F401 - the shared rule, re-exported

# qBittorrent file priority: 0 — не скачивать, 1 — обычный.
PRIORITY_SKIP = 0
PRIORITY_NORMAL = 1

REASON_USER = "user"
REASON_UNRECOGNIZED = "unrecognized"


@dataclass
class Selection:
    """Дельта приоритетов для одной раздачи."""
    to_disable: list[int] = field(default_factory=list)
    to_enable: list[int] = field(default_factory=list)
    unmatched: list[str] = field(default_factory=list)
    wanted_empty: bool = False


def is_wanted(extracted: dict | None, ignored_seasons: set[int],
              ignored_episodes: dict[int, set[int]]
              ) -> tuple[bool, str | None]:
    """Нужен ли файл. Возвращает (нужен, причина исключения)."""
    meta = extracted or {}
    if meta.get("is_extra"):
        return True, None
    season = meta.get("season")
    if season is not None and int(season) in ignored_seasons:
        return False, REASON_USER
    episodes = episodes_of(meta)
    if not episodes:
        return False, REASON_UNRECOGNIZED
    if season is None:
        return True, None
    if covered(season, episodes, ignored_episodes):
        return False, REASON_USER
    return True, None


def _index_of(qb_file: dict, position: int) -> int:
    value = qb_file.get("index")
    return int(value) if value is not None else position


def resolve(db_rows: list[dict], qb_files: list[dict]) -> Selection:
    """Строки БД + файлы qBit -> какие индексы включить/выключить.

    Сопоставление по qb_file_index, имя — фолбэк для строк, записанных
    до появления индекса (имя мутирует при переименовании, индекс нет).
    """
    by_index: dict[int, dict] = {}
    by_name: dict[str, dict] = {}
    for row in db_rows:
        idx = row.get("qb_file_index")
        if idx is not None:
            by_index[int(idx)] = row
        for name in (row.get("renamed_path"), row.get("original_path")):
            if name:
                by_name[name] = row

    selection = Selection()
    matched: set[int] = set()
    wanted = 0
    for position, qb_file in enumerate(qb_files):
        index = _index_of(qb_file, position)
        row = by_index.get(index) or by_name.get(qb_file.get("name") or "")
        if row is None:
            # Файла нет в реестре: не-видео либо правила не дали сезон —
            # такое не качаем (белый список).
            want = False
        else:
            matched.add(id(row))
            want = not row.get("is_excluded")
        if want:
            wanted += 1
        current = qb_file.get("priority")
        if want and current == PRIORITY_SKIP:
            selection.to_enable.append(index)
        elif not want and current != PRIORITY_SKIP:
            selection.to_disable.append(index)

    for row in db_rows:
        if id(row) not in matched:
            selection.unmatched.append(
                row.get("renamed_path") or row.get("original_path") or "")
    selection.wanted_empty = bool(qb_files) and wanted == 0
    return selection
