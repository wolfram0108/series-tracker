"""Сезон-инвариант — КОНСТРЕЙНТ ДАННЫХ, единая точка правды.

Сезон, который правила извлекли из заголовка/имени файла, — факт о самом
файле, и он сильнее сезона карточки сериала (`series.season`). Карточка
задаёт сезон ДЕФОЛТОМ: она отвечает на вопрос «какой сезон, если из имени
его не видно» (типичный случай VK, где сезон в заголовке не пишут).

Обратный порядок молча терял распознанное: спешелы (сезон 0) и файлы
другого сезона схлопывались в сезон карточки, и раздача выглядела
односезонной. Единый source of truth, как core/ids.py."""
from __future__ import annotations

import re


def resolve(series_season: str | int | None,
           extracted_season: int | None) -> int | None:
    """Извлечённый сезон побеждает; сезон карточки — дефолт для None."""
    if extracted_season is not None:
        return int(extracted_season)
    if series_season:
        m = re.search(r"\d+", str(series_season))
        if m:
            return int(m.group())
    return extracted_season
