"""Settings of the voices module: one reading of every key, with each problem named instead of swallowed."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

from .identity import Equivalence, usable_aliases
from .layout import DEFAULT_PRIORITY

PREFIX = "voices."
PRIORITY_KEY = "voices.source_priority"
VOICE_ALIASES_KEY = "voices.aliases"
PLAYER_ALIASES_KEY = "voices.player_aliases"
TEMP_ROOT_KEY = "voices.temp_root"
MAX_TEMP_KEY = "voices.max_temp_episodes"
DEFAULT_TEMP_ROOT = "/nas/torrents/series-tracker"


@dataclass(frozen=True)
class VoiceSettings:
    priority: tuple[str, ...] = tuple(DEFAULT_PRIORITY)
    rules: Equivalence = Equivalence()
    temp_root: str = DEFAULT_TEMP_ROOT
    max_temp_episodes: int = 0
    problems: tuple[str, ...] = ()


def _json(raw: str | None, key: str, problems: list[str]) -> object:
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        problems.append(f"{key}: значение не является JSON, настройка не применена")
        return None


def _aliases(raw: str | None, key: str, problems: list[str]) -> Mapping[str, str] | None:
    value = _json(raw, key, problems)
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append(f"{key}: ожидается объект {{псевдоним: имя}}, настройка не применена")
        return None
    usable = usable_aliases(value)
    if len(usable) != len(value):
        problems.append(f"{key}: отброшено записей: {len(value) - len(usable)} (нужны непустые строки)")
    return usable


def parse(values: Mapping[str, str | None]) -> VoiceSettings:
    """Typed settings from the raw values; a wrong value falls back to the default and is named."""
    problems: list[str] = []
    priority = _json(values.get(PRIORITY_KEY), PRIORITY_KEY, problems)
    if priority is not None and not (isinstance(priority, list) and priority
                                     and all(isinstance(item, str) for item in priority)):
        problems.append(f"{PRIORITY_KEY}: ожидается непустой список строк, применён порядок по умолчанию")
        priority = None
    root = (values.get(TEMP_ROOT_KEY) or "").strip()
    if root and not os.path.isabs(root):
        problems.append(f"{TEMP_ROOT_KEY}: ожидается абсолютный путь, применён {DEFAULT_TEMP_ROOT}")
        root = ""
    cap_raw = (values.get(MAX_TEMP_KEY) or "").strip()
    cap = 0
    if cap_raw:
        try:
            cap = int(cap_raw)
        except ValueError:
            cap = -1
        if cap < 0:
            problems.append(f"{MAX_TEMP_KEY}: ожидается целое число не меньше 0, лимита нет")
            cap = 0
    return VoiceSettings(
        priority=tuple(priority) if priority else tuple(DEFAULT_PRIORITY),
        rules=Equivalence.of(_aliases(values.get(VOICE_ALIASES_KEY), VOICE_ALIASES_KEY, problems),
                             _aliases(values.get(PLAYER_ALIASES_KEY), PLAYER_ALIASES_KEY, problems)),
        temp_root=root or DEFAULT_TEMP_ROOT, max_temp_episodes=cap, problems=tuple(problems))
