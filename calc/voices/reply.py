"""What the voices module answers, made of what it is given: the group of each name, the request for the players of the sources
of every series, and what a save of the settings does. The stored values, the players and the series are asked and read by
the module."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from core.contracts import (GroupsResolveReply, GroupsResolveRequest, PlayerFacts, PlayersListRequest, SettingProblem,
                            VoiceSettingsDraft)

from . import editor
from .settings import VoiceSettings


def groups_reply(settings: VoiceSettings, request: GroupsResolveRequest) -> GroupsResolveReply:
    """`voices.groups.resolve`: the user group of every name asked, None for a name no group holds."""
    return GroupsResolveReply(groups={name: settings.voices.group_of(name) for name in request.names})


def players_request(series: Sequence[Mapping[str, Any]]) -> PlayersListRequest:
    """The addresses of the sources of every series: the pages that say where each player is."""
    return PlayersListRequest(urls=[source["url"] for each in series for source in each.get("sources") or [] if source.get("url")])


@dataclass(frozen=True)
class Save:
    """What a save of the settings comes to: the problems that refuse it, or the values it writes (none: nothing it changes)."""

    problems: tuple[SettingProblem, ...]
    values: Mapping[str, str]


def save_of(draft: VoiceSettingsDraft, current: VoiceSettings, players: Sequence[PlayerFacts]) -> Save:
    """Only the keys the form changes are checked and written: the groups are saved whatever the order of sources holds (a
    provider that is not up leaves its lines unknown), and the order whatever the groups hold."""
    system = [each.name for each in players]
    keys = editor.changed(draft, current, system)
    problems = editor.check(draft, current.player_groups, system, kept=[*current.priority, *current.players_off], keys=keys)
    values = editor.stored(editor.clean(draft), keys) if not problems and keys else {}
    return Save(tuple(problems), values)
