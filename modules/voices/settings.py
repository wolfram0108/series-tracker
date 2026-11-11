"""Settings of the voices module: one reading of every key, with each problem named instead of swallowed."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Mapping

from core.contracts.voices import LANGUAGES
from core.voice_names import NO_GROUPS, Groups, usable_groups

# Source priority, most preferred first; empirical, by bitrate. The first one wins a resolution tie.
DEFAULT_PRIORITY = ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")

PREFIX = "voices."
PRIORITY_KEY = "voices.source_priority"
VOICE_GROUPS_KEY = "voices.groups"
# The players of the order switched off for every source: their rows stay in the tables and go to no layout.
PLAYERS_OFF_KEY = "voices.players_off"
PLAYER_GROUPS_KEY = "voices.player_groups"
MAX_TEMP_KEY = "voices.max_temp_episodes"
# Stage 12: the height the file of a compilation is downloaded at for its cuts (0 or unset: the best it has). A check of
# the cuts sets it low (720) so that the service encodes fast; the product leaves it unset.
CUT_HEIGHT_KEY = "voices.cut_max_height"
# The language of the voice-over of each group ({group: ISO 639-2}), chosen in the editor of the group.
LANGUAGES_KEY = "voices.languages"


@dataclass(frozen=True)
class VoiceSettings:
    priority: tuple[str, ...] = tuple(DEFAULT_PRIORITY)
    voices: Groups = NO_GROUPS
    players: Groups = NO_GROUPS
    max_temp_episodes: int = 0
    cut_max_height: int = 0
    problems: tuple[str, ...] = ()
    # The groups as spelled by the user ({group: [names that put a voice-over or a player into it]}) and the
    # keys whose stored value was refused.
    voice_groups: Mapping[str, list[str]] = field(default_factory=dict)
    player_groups: Mapping[str, list[str]] = field(default_factory=dict)
    rejected: tuple[str, ...] = ()
    voice_languages: Mapping[str, str] = field(default_factory=dict)
    players_off: tuple[str, ...] = ()


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


def _count(raw: str | None, key: str, problems: list[str], rejected: list[str]) -> int:
    """A whole number not below 0; 0 (no limit) for nothing or for a wrong value, which is named."""
    text = (raw or "").strip()
    if not text:
        return 0
    try:
        value = int(text)
    except ValueError:
        value = -1
    if value < 0:
        problems.append(f"{key}: ожидается целое число не меньше 0, лимита нет")
        rejected.append(key)
        return 0
    return value


def _languages(raw: str | None, key: str, problems: list[str], rejected: list[str]) -> dict[str, str]:
    value = _json(raw, key, problems, rejected)
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(name, str) and code in LANGUAGES for name, code in value.items()):
        problems.append(f"{key}: ожидается объект {{группа: код языка из {', '.join(LANGUAGES)}}}, настройка не применена")
        rejected.append(key)
        return {}
    return dict(value)


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
    cap = _count(values.get(MAX_TEMP_KEY), MAX_TEMP_KEY, problems, rejected)
    cut_height = _count(values.get(CUT_HEIGHT_KEY), CUT_HEIGHT_KEY, problems, rejected)
    voices = _groups(values.get(VOICE_GROUPS_KEY), VOICE_GROUPS_KEY, problems, rejected) or {}
    players = _groups(values.get(PLAYER_GROUPS_KEY), PLAYER_GROUPS_KEY, problems, rejected) or {}
    languages = _languages(values.get(LANGUAGES_KEY), LANGUAGES_KEY, problems, rejected)
    off = _json(values.get(PLAYERS_OFF_KEY), PLAYERS_OFF_KEY, problems, rejected)
    if off is not None and not (isinstance(off, list) and all(isinstance(item, str) for item in off)):
        problems.append(f"{PLAYERS_OFF_KEY}: ожидается список строк, отключённых плееров нет")
        rejected.append(PLAYERS_OFF_KEY)
        off = None
    return VoiceSettings(
        priority=tuple(priority) if priority else tuple(DEFAULT_PRIORITY),
        voices=Groups.of(voices), players=Groups.of(players),
        max_temp_episodes=cap, cut_max_height=cut_height, problems=tuple(problems),
        voice_groups=voices, player_groups=players, rejected=tuple(rejected),
        voice_languages={name: code for name, code in languages.items() if name in voices},
        players_off=tuple(off or ()))
