"""Формулы идентификаторов — КОНСТРЕЙНТ ДАННЫХ (Р-10).

Существующие записи прод-БД обязаны совпадать с вычислением: проверено
на фикстуре 190/190 торрентов и 351/351 медиа-элементов
(tests/test_scan_ids.py). Менять формулы нельзя — это вызвало бы
ложное «всё новое» и массовую перекачку (родственно находке 13).
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone


def torrent_id(link: str, date_time: str | None) -> str:
    """md5(link + date_time)[:16]; date_time — строка в историческом
    формате СВОЕГО трекера (modules/sources/dates.py)."""
    return hashlib.md5(f"{link}{date_time or ''}".encode()).hexdigest()[:16]


def media_unique_id(url: str, pub_date: datetime | str, series_id: int) -> str:
    """md5(url + 'YYYY-MM-DD HH:MM:SS' + series_id)[:16]; дата — UTC."""
    if isinstance(pub_date, str):
        pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    date_str = pub_date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return hashlib.md5(f"{url}{date_str}{series_id}".encode()).hexdigest()[:16]
