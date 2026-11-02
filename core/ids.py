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


def _utc_date_str(pub_date: datetime | str) -> str:
    if isinstance(pub_date, str):
        pub_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    return pub_date.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def media_unique_id(url: str, pub_date: datetime | str, series_id: int) -> str:
    """md5(url + 'YYYY-MM-DD HH:MM:SS' + series_id)[:16]; дата — UTC."""
    date_str = _utc_date_str(pub_date)
    return hashlib.md5(f"{url}{date_str}{series_id}".encode()).hexdigest()[:16]


def upgrade_unique_id(url: str, pub_date: datetime | str, series_id: int,
                      resolution: int) -> str:
    """Id of a higher-resolution file served from an already downloaded URL.

    The plain formula would overwrite the downloaded row's resolution and the
    better file would never be fetched; the resolution suffix keeps both rows."""
    date_str = _utc_date_str(pub_date)
    return hashlib.md5(
        f"{url}{date_str}{series_id}@{resolution}p".encode()).hexdigest()[:16]


# The date inside the identity of a row of a layout is a constant: the date a site gives may change, the row stays the same.
_ROW_ID_DATE = "1970-01-01 00:00:00"


def _row_address(source_id: int, row_key: str) -> str:
    return f"row:{source_id}/{row_key}"


def row_unique_id(series_id: int, source_id: int, row_key: str) -> str:
    """Id of the plan row made from a row of the layout of a series: the same formula as `media_unique_id`, with the
    key of the row of the source in place of the address and a fixed date. The old formulas are unchanged."""
    return media_unique_id(_row_address(source_id, row_key), _ROW_ID_DATE, series_id)


def version_row_unique_id(series_id: int, source_id: int, row_key: str, address: str) -> str:
    """Id of the plan row for another video behind a row of the layout whose earlier video is already fetched: the
    address of the video is a part of the identity, so each video of the row has its own file, and a row never carries
    the file of a video it no longer shows."""
    return media_unique_id(f"{_row_address(source_id, row_key)}@{address}", _ROW_ID_DATE, series_id)


def upgrade_row_unique_id(series_id: int, source_id: int, row_key: str, resolution: int, address: str) -> str:
    """Id of the row of a better version of a candidate whose earlier version is already fetched (see
    `upgrade_unique_id`), for a row of the layout. The address of the video is a part of it: two videos behind one row
    at one resolution are two rows, never one that holds the file of the other."""
    return upgrade_unique_id(f"{_row_address(source_id, row_key)}@{address}", _ROW_ID_DATE, series_id, resolution)
