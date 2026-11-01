"""Settings of the voices module: one reading of every key, with each problem named instead of swallowed."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping

from core.voice_names import NO_GROUPS, Groups, usable_groups

# Source priority, most preferred first; empirical, by bitrate. The first one wins a resolution tie.
DEFAULT_PRIORITY = ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")

PREFIX = "voices."
PRIORITY_KEY = "voices.source_priority"
VOICE_GROUPS_KEY = "voices.groups"
PLAYER_GROUPS_KEY = "voices.player_groups"
MAX_TEMP_KEY = "voices.max_temp_episodes"


@dataclass(frozen=True)
class VoiceSettings:
    priority: tuple[str, ...] = tuple(DEFAULT_PRIORITY)
    voices: Groups = NO_GROUPS
    players: Groups = NO_GROUPS
    max_temp_episodes: int = 0
    problems: tuple[str, ...] = ()
    # The groups as spelled by the user ({group: [names that put a voice-over or a player into it]}) and the
    # keys whose stored value was refused.
    voice_groups: Mapping[str, list[str]] = field(default_factory=dict)
    player_groups: Mapping[str, list[str]] = field(default_factory=dict)
    rejected: tuple[str, ...] = ()


def _json(raw: str | None, key: str, problems: list[str], rejected: list[str]) -> object:
    if raw is None or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        problems.append(f"{key}: значение не является JSON, настройка не применена")
        rejected.append(key)
        return None


def _groups(raw: str | None, key: str, problems: list[str], rejected: list[str]) -> dict[str, list[str]] | None:
    value = _json(raw, key, problems, rejected)
    if value is None:
        return None
    if not isinstance(value, dict):
        problems.append(f"{key}: ожидается объект {{группа: [названия]}}, настройка не применена")
        rejected.append(key)
        return None
    usable = usable_groups(value)
    kept = len(usable) + sum(len(triggers) for triggers in usable.values())
    given = len(value) + sum(len(triggers) for triggers in value.values() if isinstance(triggers, list))
    if kept != given:
        problems.append(f"{key}: отброшено записей: {given - kept} (нужны названия и списки непустых строк)")
    return usable


def parse(values: Mapping[str, str | None]) -> VoiceSettings:
    """Typed settings from the raw values; a wrong value falls back to the default and is named."""
    problems: list[str] = []
    rejected: list[str] = []
    priority = _json(values.get(PRIORITY_KEY), PRIORITY_KEY, problems, rejected)
    if priority is not None and not (isinstance(priority, list) and priority
                                     and all(isinstance(item, str) for item in priority)):
        problems.append(f"{PRIORITY_KEY}: ожидается непустой список строк, применён порядок по умолчанию")
        rejected.append(PRIORITY_KEY)
        priority = None
    cap_raw = (values.get(MAX_TEMP_KEY) or "").strip()
    cap = 0
    if cap_raw:
        try:
            cap = int(cap_raw)
        except ValueError:
            cap = -1
        if cap < 0:
            problems.append(f"{MAX_TEMP_KEY}: ожидается целое число не меньше 0, лимита нет")
            rejected.append(MAX_TEMP_KEY)
            cap = 0
    voices = _groups(values.get(VOICE_GROUPS_KEY), VOICE_GROUPS_KEY, problems, rejected) or {}
    players = _groups(values.get(PLAYER_GROUPS_KEY), PLAYER_GROUPS_KEY, problems, rejected) or {}
    return VoiceSettings(
        priority=tuple(priority) if priority else tuple(DEFAULT_PRIORITY),
        voices=Groups.of(voices), players=Groups.of(players),
        max_temp_episodes=cap, problems=tuple(problems),
        voice_groups=voices, player_groups=players, rejected=tuple(rejected))
