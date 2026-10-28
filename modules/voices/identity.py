"""Voice-over identity: which names mean the same voice across players.

A studio is spelled differently by every player ("RedHeadSound", "Red Head Sound") and a
combined credit ("AniMy / RuChiMe") names several studios at once. Two names are the same voice
when a token of one equals a token of the other after normalising and applying ALIASES.
The first version is deliberately small: the rule constructor replaces it behind the same functions.
"""
from __future__ import annotations

import re

# Credits that name several studios at once are split on these.
_SEPARATORS = re.compile(r"[/&+,]|\sи\s|\sand\s", re.IGNORECASE)
_NOISE = re.compile(r"[\W_]+", re.UNICODE)

# Known synonyms: normalised token -> normalised canonical token. Data, not logic.
ALIASES: dict[str, str] = {}


def tokens(name: str) -> frozenset[str]:
    """Normalised, alias-resolved studio tokens of one credit."""
    found = set()
    for part in _SEPARATORS.split(name):
        token = _NOISE.sub("", part.casefold())
        if token:
            found.add(ALIASES.get(token, token))
    return frozenset(found)


def same_voice(first: str, second: str) -> bool:
    return not tokens(first).isdisjoint(tokens(second))


def voice_groups(names: list[str]) -> list[list[int]]:
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
        for token in tokens(name):
            if token in owner:
                parent[find(index)] = find(owner[token])
            else:
                owner[token] = index
    groups: dict[int, list[int]] = {}
    for index in range(len(names)):
        groups.setdefault(find(index), []).append(index)
    return sorted(groups.values(), key=lambda members: members[0])


def group_key(names: list[str]) -> str:
    """Stable key of a voice: the smallest token of any spelling, so it survives a changed listing order."""
    found = set().union(*(tokens(name) for name in names)) if names else set()
    return min(found) if found else ""
