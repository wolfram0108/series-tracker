"""Which names mean the same voice-over (or the same player): only what the user wrote down in a group.

A group is a name with a list of triggers. A name belongs to a group when, folded (`core.voice_names.fold_name`: no
case, no spaces, no punctuation), it equals the folded name of the group or one of its triggers. A combined credit
"A / B" is one name: it is not split, so it joins neither the group of "A" nor the group of "B" (law Z-6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from core.voice_names import fold_name


def usable_groups(raw: object) -> dict[str, list[str]]:
    """The groups of user data that have a name and lists of names; anything else is not trusted."""
    if not isinstance(raw, dict):
        return {}
    groups = {}
    for name, triggers in raw.items():
        if isinstance(name, str) and fold_name(name) and isinstance(triggers, list):
            groups[name] = [trigger for trigger in triggers if isinstance(trigger, str) and fold_name(trigger)]
    return groups


@dataclass(frozen=True)
class Groups:
    """The user groups in the form the matching works with: folded name or trigger -> group.

    A group's own name outranks the trigger of another group; of two groups with the same trigger the one written
    first wins, so a stored value that the editor would have refused still answers one way."""

    index: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, groups: Mapping[str, list[str]]) -> "Groups":
        index: dict[str, str] = {}
        for name in groups:
            index.setdefault(fold_name(name), name)
        for name, triggers in groups.items():
            for trigger in triggers:
                index.setdefault(fold_name(trigger), name)
        return cls(index)

    def group_of(self, name: str) -> str | None:
        """The group the name belongs to; None for a name nobody grouped (and for a name with nothing to compare)."""
        key = fold_name(name)
        return self.index.get(key) if key else None

    def canonical(self, name: str) -> str:
        """The folded form that stands for the name when two names are compared: its group's, else its own."""
        group = self.group_of(name)
        return fold_name(group if group is not None else name)


NO_GROUPS = Groups()
