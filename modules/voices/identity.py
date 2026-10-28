"""Voice-over identity: which names mean the same voice across players.

A studio is spelled differently by every player ("RedHeadSound", "Red Head Sound") and a
combined credit ("AniMy / RuChiMe") names several studios at once. Two names are the same voice
when a token of one equals a token of the other after normalising and applying the rules.
The rules are data (`Equivalence`: synonyms of voice-overs and of players), read from settings by
the module and handed to these pure functions; the constructor of rules edits that data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Mapping

# Credits that name several studios at once are split on these.
_SEPARATORS = re.compile(r"[/&+,]|\sи\s|\sand\s", re.IGNORECASE)
_NOISE = re.compile(r"[\W_]+", re.UNICODE)

# Built-in synonyms: normalised token -> normalised canonical token. Data, not logic.
ALIASES: dict[str, str] = {}


def _fold(name: str) -> str:
    return _NOISE.sub("", name.casefold())


def usable_aliases(raw: object) -> dict[str, str]:
    """The entries of user data that pair two non-empty names; anything else is not trusted."""
    if not isinstance(raw, dict):
        return {}
    return {alias: canonical for alias, canonical in raw.items()
            if isinstance(alias, str) and isinstance(canonical, str) and _fold(alias) and _fold(canonical)}


def _folded(raw: object) -> dict[str, str]:
    """{alias: canonical} in the folded form the matching works with."""
    return {_fold(alias): _fold(canonical) for alias, canonical in usable_aliases(raw).items()}


@dataclass(frozen=True)
class Equivalence:
    """Which names mean the same: synonyms of voice-over studios and of player kinds."""

    voices: Mapping[str, str] = field(default_factory=dict)
    players: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, voices: object = None, players: object = None) -> "Equivalence":
        return cls(_folded(voices), _folded(players))

    def player(self, kind: str) -> str:
        return self.players.get(_fold(kind), _fold(kind))


NO_RULES = Equivalence()


def tokens(name: str, rules: Equivalence = NO_RULES) -> frozenset[str]:
    """Normalised, synonym-resolved studio tokens of one credit."""
    synonyms = {**ALIASES, **rules.voices}
    found = set()
    for part in _SEPARATORS.split(name):
        token = _fold(part)
        if token:
            found.add(synonyms.get(token, token))
    return frozenset(found)


def same_voice(first: str, second: str, rules: Equivalence = NO_RULES) -> bool:
    return not tokens(first, rules).isdisjoint(tokens(second, rules))


def voice_groups(names: list[str], rules: Equivalence = NO_RULES) -> list[list[int]]:
    """Indices of `names` grouped by voice; a group lists its members in the given order.

    Matching is transitive: "A / B" joins a group named "A" and a group named "B"."""
    parent = list(range(len(names)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owner: dict[str, int] = {}
    for index, name in enumerate(names):
        for token in tokens(name, rules):
            if token in owner:
                parent[find(index)] = find(owner[token])
            else:
                owner[token] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(names)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda members: members[0])


def group_key(names: list[str], rules: Equivalence = NO_RULES) -> str:
    """Stable key of a voice: the smallest token of any spelling, so it survives a changed listing order."""
    found = set().union(*(tokens(name, rules) for name in names)) if names else set()
    return min(found) if found else ""
