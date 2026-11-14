"""What the settings screen of the voices shows and what a save may write: the groups of voice-overs and the order of sources.

Pure functions over the typed settings: the module only carries their results across the bus. A group is a name
with the list of names (triggers) that put a voice-over into it. A save is checked here as a whole and refused
with coded problems; a refused value is never written and never quietly replaced by a default.
"""
from __future__ import annotations

import json
from typing import Collection, Mapping, Sequence

from core.contracts import Reason, SettingProblem, VoiceSettingsDraft, VoiceSettingsView
from core.reasons import parse as parse_reason, reason
from core.voice_names import fold_name

from core.contracts.voices import LANGUAGES

from .settings import DEFAULT_PRIORITY, LANGUAGES_KEY, PLAYERS_OFF_KEY, PRIORITY_KEY, VOICE_GROUPS_KEY, VoiceSettings

# The field of the screen behind each stored key, for naming the stored value that was refused.
_FIELD_OF_KEY = {PRIORITY_KEY: "priority", VOICE_GROUPS_KEY: "voice_groups", LANGUAGES_KEY: "voice_languages",
                 PLAYERS_OFF_KEY: "players_off"}


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


def player_names(player_groups: Mapping[str, list[str]], system: Sequence[str] = ()) -> list[str]:
    """Players the order of sources may name: the built-in kinds, every player the sources know (`system`) and every name
    the stored player groups mention."""
    named: dict[str, str] = {}
    for name in [*DEFAULT_PRIORITY, *system, *(each for group, triggers in player_groups.items() for each in (group, *triggers))]:
        if fold_name(name):
            named.setdefault(fold_name(name), name)
    return list(named.values())


def _language_problems(draft: VoiceSettingsDraft) -> list[SettingProblem]:
    """A code that is not one of `LANGUAGES` is refused. A group without a language is kept: the editor marks it until
    the user gives it one, and a track of it is written without a language."""
    field = "voice_languages"
    languages = {name.strip(): code for name, code in draft.voice_languages.items()}
    return [_problem(field, reason("group_language_unknown", group=name, language=languages[name]))
            for name in _cleaned(draft.voice_groups) if fold_name(name) and languages.get(name) not in (None, *LANGUAGES)]


_KEYS = (PRIORITY_KEY, PLAYERS_OFF_KEY, VOICE_GROUPS_KEY, LANGUAGES_KEY)


def changed(draft: VoiceSettingsDraft, saved: VoiceSettings, system: Sequence[str] = ()) -> set[str]:
    """The stored keys this save changes: a value that differs from what applies now, or whose stored value was refused (a
    save puts it right). A key the form only brings back as it was is neither checked nor written: the groups are saved
    whatever the order of sources holds, and the order whatever the groups hold."""
    cleaned = clean(draft)
    now = {PRIORITY_KEY: effective_order(saved.priority, system), PLAYERS_OFF_KEY: list(saved.players_off),
           VOICE_GROUPS_KEY: {name: list(triggers) for name, triggers in saved.voice_groups.items()},
           LANGUAGES_KEY: dict(saved.voice_languages)}
    given = {PRIORITY_KEY: cleaned.priority, PLAYERS_OFF_KEY: cleaned.players_off,
             VOICE_GROUPS_KEY: cleaned.voice_groups, LANGUAGES_KEY: cleaned.voice_languages}
    return {key for key in _KEYS if given[key] != now[key] or key in saved.rejected}


def check(draft: VoiceSettingsDraft, player_groups: Mapping[str, list[str]],
          system: Sequence[str] = (), kept: Sequence[str] = (), keys: Collection[str] | None = None) -> list[SettingProblem]:
    """Every reason this form cannot be saved; empty when it can. Only the stored `keys` it changes are checked (all of
    them when not given). `kept` are the players of the order and of the off-list that are stored already: the sources may
    not show one of them now (the provider of the lines is not up), and it is no error of the user's. Only a player new
    here must be a known one."""
    wanted = set(_KEYS if keys is None else keys)
    problems = []
    names = [name.strip() for name in draft.priority if name.strip()]
    if PRIORITY_KEY in wanted and not names:
        problems.append(_problem("priority", reason("priority_empty")))
    known = {fold_name(name) for name in (*player_names(player_groups, system), *kept)}
    for key, field, listed in ((PRIORITY_KEY, "priority", names),
                               (PLAYERS_OFF_KEY, "players_off", [name.strip() for name in draft.players_off])):
        if key not in wanted:
            continue
        seen: set[str] = set()
        for name in listed:
            if fold_name(name) in seen:
                problems.append(_problem(field, reason("priority_duplicate", player=name)))
            elif fold_name(name) not in known:
                problems.append(_problem(field, reason("priority_unknown_player", player=name)))
            seen.add(fold_name(name))
    if VOICE_GROUPS_KEY in wanted:
        problems += _group_problems(draft.voice_groups)
    if wanted & {VOICE_GROUPS_KEY, LANGUAGES_KEY}:
        problems += _language_problems(draft)
    return problems


def clean(draft: VoiceSettingsDraft) -> VoiceSettingsDraft:
    """The draft as it is stored: trimmed, without blank rows or repeated names."""
    priority = list(dict.fromkeys(name.strip() for name in draft.priority if name.strip()))
    groups = _cleaned(draft.voice_groups)
    languages = {name.strip(): code for name, code in draft.voice_languages.items() if name.strip() in groups}
    off = list(dict.fromkeys(name.strip() for name in draft.players_off if name.strip()))
    return VoiceSettingsDraft(priority=priority, voice_groups=groups, voice_languages=languages, players_off=off)


def stored(draft: VoiceSettingsDraft, keys: Collection[str] | None = None) -> dict[str, str]:
    """The values to write, keyed as the settings table holds them; only `keys` of them when given."""
    def dump(value: object) -> str:
        return json.dumps(value, ensure_ascii=False)
    values = {PRIORITY_KEY: dump(draft.priority), PLAYERS_OFF_KEY: dump(draft.players_off), VOICE_GROUPS_KEY: dump(draft.voice_groups),
              LANGUAGES_KEY: dump(draft.voice_languages)}
    return values if keys is None else {key: value for key, value in values.items() if key in keys}


def effective_order(priority: Sequence[str], system: Sequence[str]) -> list[str]:
    """The order of sources that applies: the one the user saved, then every other player the sources know, in their
    order. A player the order does not name would rank last with every other one; here it has its place, shown and
    taken alike."""
    held = {fold_name(name) for name in priority}
    return [*priority, *(name for name in system if fold_name(name) not in held)]


def view(settings: VoiceSettings, players: Sequence[Mapping[str, str]] = ()) -> VoiceSettingsView:
    """What applies now, with the order to return to; `players` are those the sources know ({name, label})."""
    system = [each["name"] for each in players]
    return VoiceSettingsView(
        priority=effective_order(settings.priority, system),
        player_labels={each["name"]: each.get("label") or each["name"] for each in players},
        voice_groups={name: list(triggers) for name, triggers in settings.voice_groups.items()},
        voice_languages=dict(settings.voice_languages),
        players_off=list(settings.players_off),
        default_priority=effective_order(DEFAULT_PRIORITY, system),
        player_groups={name: list(triggers) for name, triggers in settings.player_groups.items()},
        rejected=[_FIELD_OF_KEY[key] for key in settings.rejected if key in _FIELD_OF_KEY],
        cut_max_height=settings.cut_max_height)
