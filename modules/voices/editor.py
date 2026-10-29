"""What the settings form of the voices shows, what a save may write, and how a draft of the rules groups names.

Pure functions over the typed settings: the module only carries their results across the bus. A save is
checked here as a whole and refused with coded problems; a refused value is never written and never quietly
replaced by a default.
"""
from __future__ import annotations

import json
import posixpath
from typing import Mapping

from core.contracts import (GroupsDraft, Reason, SettingDefaults, SettingProblem, VoiceGroup, VoiceGroups,
                            VoiceSettingsDraft, VoiceSettingsView)
from core.reasons import parse as parse_reason, reason

from .identity import Equivalence, folded_name, group_key, voice_groups
from .layout import DEFAULT_PRIORITY
from .settings import (DEFAULT_TEMP_ROOT, MAX_TEMP_KEY, PLAYER_ALIASES_KEY, PRIORITY_KEY, TEMP_ROOT_KEY,
                       VOICE_ALIASES_KEY, VoiceSettings)

# The key behind each field of the form, for naming the stored value that was refused.
_FIELD_OF_KEY = {PRIORITY_KEY: "priority", VOICE_ALIASES_KEY: "voice_aliases", PLAYER_ALIASES_KEY: "player_aliases",
                 TEMP_ROOT_KEY: "temp_root", MAX_TEMP_KEY: "max_temp_episodes"}


def _problem(field: str, stored: str) -> SettingProblem:
    return SettingProblem(field=field, detail=Reason.model_validate(parse_reason(stored)))


def _pairs(aliases: Mapping[str, str]) -> dict[str, str]:
    """The pairs the user filled in: spaces trimmed, rows with both names empty (a row added and left blank) dropped."""
    pairs = {}
    for alias, canonical in aliases.items():
        alias, canonical = alias.strip(), canonical.strip()
        if alias or canonical:
            pairs[alias] = canonical
    return pairs


def _pair_problems(field: str, aliases: Mapping[str, str]) -> list[SettingProblem]:
    problems = []
    seen: dict[str, str] = {}
    pairs = _pairs(aliases)
    targets = {folded_name(alias): alias for alias in pairs}
    for alias, canonical in pairs.items():
        name = alias or canonical
        if not folded_name(alias) or not folded_name(canonical):
            problems.append(_problem(field, reason("alias_incomplete", name=name)))
            continue
        key = folded_name(alias)
        if key == folded_name(canonical):
            problems.append(_problem(field, reason("alias_self", name=alias)))
        elif key in seen and folded_name(seen[key]) != folded_name(canonical):
            problems.append(_problem(field, reason("alias_conflict", name=alias, first=seen[key], second=canonical)))
        elif folded_name(canonical) in targets:
            # A pair is resolved in one step: a canonical name that is itself an alias would leave the first one apart.
            problems.append(_problem(field, reason("alias_chain", alias=alias, canonical=canonical,
                                                   target=pairs[targets[folded_name(canonical)]])))
        seen.setdefault(key, canonical)
    return problems


def known_players(player_aliases: Mapping[str, str]) -> set[str]:
    """Player kinds the order of sources may name: the built-in ones and every name the player rules mention."""
    named = {folded_name(kind) for kind in DEFAULT_PRIORITY}
    for alias, canonical in _pairs(player_aliases).items():
        named |= {folded_name(alias), folded_name(canonical)}
    return named


def check(draft: VoiceSettingsDraft) -> list[SettingProblem]:
    """Every reason this form cannot be saved; empty when it can."""
    problems = []
    names = [name.strip() for name in draft.priority if name.strip()]
    if not names:
        problems.append(_problem("priority", reason("priority_empty")))
    seen: set[str] = set()
    known = known_players(draft.player_aliases)
    for name in names:
        if folded_name(name) in seen:
            problems.append(_problem("priority", reason("priority_duplicate", player=name)))
        elif folded_name(name) not in known:
            problems.append(_problem("priority", reason("priority_unknown_player", player=name)))
        seen.add(folded_name(name))
    problems += _pair_problems("voice_aliases", draft.voice_aliases)
    problems += _pair_problems("player_aliases", draft.player_aliases)
    root = draft.temp_root.strip()
    if root and not posixpath.isabs(root):
        problems.append(_problem("temp_root", reason("temp_root_not_absolute", path=root)))
    if draft.max_temp_episodes < 0:
        problems.append(_problem("max_temp_episodes", reason("max_temp_negative")))
    return problems


def clean(draft: VoiceSettingsDraft) -> VoiceSettingsDraft:
    """The draft as it is stored: trimmed, without blank rows or repeated players, with a normalised path."""
    priority = list(dict.fromkeys(name.strip() for name in draft.priority if name.strip()))
    root = draft.temp_root.strip()
    return VoiceSettingsDraft(
        priority=priority, voice_aliases=_pairs(draft.voice_aliases), player_aliases=_pairs(draft.player_aliases),
        temp_root=posixpath.normpath(root) if root else "", max_temp_episodes=draft.max_temp_episodes)


def stored(draft: VoiceSettingsDraft) -> dict[str, str]:
    """The values to write, keyed as the settings table holds them (an empty path means the default)."""
    def dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)
    return {PRIORITY_KEY: dump(draft.priority), VOICE_ALIASES_KEY: dump(draft.voice_aliases),
            PLAYER_ALIASES_KEY: dump(draft.player_aliases), TEMP_ROOT_KEY: draft.temp_root,
            MAX_TEMP_KEY: str(draft.max_temp_episodes)}


def view(settings: VoiceSettings, known_voices: list[str]) -> VoiceSettingsView:
    """What applies now, with the defaults to return to and the voices the system has met."""
    return VoiceSettingsView(
        priority=list(settings.priority), voice_aliases=dict(settings.voice_aliases),
        player_aliases=dict(settings.player_aliases), temp_root=settings.temp_root,
        max_temp_episodes=settings.max_temp_episodes,
        defaults=SettingDefaults(priority=list(DEFAULT_PRIORITY), temp_root=DEFAULT_TEMP_ROOT, max_temp_episodes=0),
        rejected=[_FIELD_OF_KEY[key] for key in settings.rejected if key in _FIELD_OF_KEY],
        known_voices=known_voices)


def preview(draft: GroupsDraft) -> VoiceGroups:
    """Which of the names mean the same voice under the rules of the draft; the draft's own faults come with the answer."""
    problems = _pair_problems("voice_aliases", draft.voice_aliases) + _pair_problems("player_aliases", draft.player_aliases)
    rules = Equivalence.of(_pairs(draft.voice_aliases), _pairs(draft.player_aliases))
    names = list(dict.fromkeys(name.strip() for name in draft.names if name.strip()))
    groups = [VoiceGroup(key=group_key([names[index] for index in members], rules),
                         members=[names[index] for index in members])
              for members in voice_groups(names, rules)]
    return VoiceGroups(groups=groups, problems=problems)
