"""What the settings screen of the voices shows and what a save may write: the groups of voice-overs and the order of sources.

Pure functions over the typed settings: the module only carries their results across the bus. A group is a name
with the list of names (triggers) that put a voice-over into it. A save is checked here as a whole and refused
with coded problems; a refused value is never written and never quietly replaced by a default.
"""
from __future__ import annotations

import json
from typing import Mapping

from core.contracts import Reason, SettingProblem, VoiceSettingsDraft, VoiceSettingsView
from core.reasons import parse as parse_reason, reason
from core.voice_names import fold_name

from .settings import DEFAULT_PRIORITY, PRIORITY_KEY, VOICE_GROUPS_KEY, VoiceSettings

# The field of the screen behind each stored key, for naming the stored value that was refused.
_FIELD_OF_KEY = {PRIORITY_KEY: "priority", VOICE_GROUPS_KEY: "voice_groups"}


def _problem(field: str, stored: str) -> SettingProblem:
    return SettingProblem(field=field, detail=Reason.model_validate(parse_reason(stored)))


def _cleaned(groups: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """Names trimmed, a trigger written twice in one group kept once, a row left blank (no name, no triggers) dropped."""
    cleaned = {}
    for name, triggers in groups.items():
        kept: dict[str, str] = {}
        for trigger in triggers:
            if fold_name(trigger):
                kept.setdefault(fold_name(trigger), trigger.strip())
        if name.strip() or kept:
            cleaned[name.strip()] = list(kept.values())
    return cleaned


def _group_problems(groups: Mapping[str, list[str]]) -> list[SettingProblem]:
    field = "voice_groups"
    problems = []
    names: dict[str, str] = {}
    cleaned = _cleaned(groups)
    for name in cleaned:
        if not fold_name(name):
            problems.append(_problem(field, reason("group_name_empty")))
        elif fold_name(name) in names:
            problems.append(_problem(field, reason("group_duplicate", name=name, other=names[fold_name(name)])))
        else:
            names[fold_name(name)] = name
    owner: dict[str, str] = {}
    for name, triggers in cleaned.items():
        for trigger in triggers:
            key = fold_name(trigger)
            if key == fold_name(name):
                problems.append(_problem(field, reason("trigger_is_group", trigger=trigger, group=name)))
            elif key in names:
                problems.append(_problem(field, reason("trigger_is_other_group", trigger=trigger, group=name,
                                                       other=names[key])))
            elif owner.setdefault(key, name) != name:
                problems.append(_problem(field, reason("trigger_in_two_groups", trigger=trigger, first=owner[key],
                                                       second=name)))
    return problems


def player_names(player_groups: Mapping[str, list[str]]) -> list[str]:
    """Players the order of sources may name: the built-in kinds and every name the stored player groups mention."""
    named: dict[str, str] = {}
    for name in [*DEFAULT_PRIORITY, *(each for group, triggers in player_groups.items() for each in (group, *triggers))]:
        if fold_name(name):
            named.setdefault(fold_name(name), name)
    return list(named.values())


def check(draft: VoiceSettingsDraft, player_groups: Mapping[str, list[str]]) -> list[SettingProblem]:
    """Every reason this form cannot be saved; empty when it can."""
    problems = []
    names = [name.strip() for name in draft.priority if name.strip()]
    if not names:
        problems.append(_problem("priority", reason("priority_empty")))
    seen: set[str] = set()
    known = {fold_name(name) for name in player_names(player_groups)}
    for name in names:
        if fold_name(name) in seen:
            problems.append(_problem("priority", reason("priority_duplicate", player=name)))
        elif fold_name(name) not in known:
            problems.append(_problem("priority", reason("priority_unknown_player", player=name)))
        seen.add(fold_name(name))
    return problems + _group_problems(draft.voice_groups)


def clean(draft: VoiceSettingsDraft) -> VoiceSettingsDraft:
    """The draft as it is stored: trimmed, without blank rows or repeated names."""
    priority = list(dict.fromkeys(name.strip() for name in draft.priority if name.strip()))
    return VoiceSettingsDraft(priority=priority, voice_groups=_cleaned(draft.voice_groups))


def stored(draft: VoiceSettingsDraft) -> dict[str, str]:
    """The values to write, keyed as the settings table holds them."""
    def dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)
    return {PRIORITY_KEY: dump(draft.priority), VOICE_GROUPS_KEY: dump(draft.voice_groups)}


def view(settings: VoiceSettings) -> VoiceSettingsView:
    """What applies now, with the order to return to."""
    return VoiceSettingsView(
        priority=list(settings.priority),
        voice_groups={name: list(triggers) for name, triggers in settings.voice_groups.items()},
        default_priority=list(DEFAULT_PRIORITY),
        rejected=[_FIELD_OF_KEY[key] for key in settings.rejected if key in _FIELD_OF_KEY])
