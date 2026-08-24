"""Сезон-инвариант — КОНСТРЕЙНТ ДАННЫХ, единая точка правды.

Карточка сериала (`series.season`, «односезонник») бьёт сезон, извлечённый
из заголовка/имени файла правилами. Раньше эта проверка была продублирована
поштучно под каждого потребителя (modules/rules/formatter.py) и одна точка
использования (rules.apply) её не имела вовсе — сезон терялся молча.
Единый source of truth, как core/ids.py."""
from __future__ import annotations

import re


def resolve(series_season: str | int | None,
           extracted_season: int | None) -> int | None:
    """series_season задан и содержит цифру -> она побеждает; иначе
    extracted_season как есть."""
    if series_season:
        m = re.search(r"\d+", str(series_season))
        if m:
            return int(m.group())
    return extracted_season
