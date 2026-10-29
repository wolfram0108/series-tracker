"""The memory of episodes the user switched off, and the one rule that reads it, for every kind of source.

The memory is kept on the series as {season: [episode numbers]}: a switched-off episode stays switched off when
its release or its source is replaced. A torrent file and a row of a web or VK source are judged by the same rule.
"""
from __future__ import annotations


def episodes_of(extracted: dict | None) -> set[int]:
    """The numbers of the episodes a file or a row covers (a single episode or a compilation)."""
    meta = extracted or {}
    start, end = meta.get("start"), meta.get("end")
    if start is not None and end is not None and int(end) >= int(start):
        return set(range(int(start), int(end) + 1))
    episode = meta.get("episode")
    if episode is not None:
        return {int(episode)}
    if start is not None:
        return {int(start)}
    return set()


def parse_memory(raw: object) -> dict[int, set[int]]:
    """The stored JSON memory -> {season: {episode numbers}}; what is not a season with numbers is dropped."""
    if not isinstance(raw, dict):
        return {}
    memory: dict[int, set[int]] = {}
    for season, episodes in raw.items():
        try:
            key = int(season)
        except (TypeError, ValueError):
            continue
        numbers = {int(e) for e in (episodes or []) if e is not None}
        if numbers:
            memory[key] = numbers
    return memory


def covered(season: int | None, episodes: set[int], memory: dict[int, set[int]]) -> bool:
    """True when nothing the file holds is still wanted: every episode it covers is in the memory of its season.

    A compilation is switched off only when none of its episodes is wanted, otherwise the wanted ones would be lost."""
    if season is None or not episodes:
        return False
    return episodes <= memory.get(int(season), set())
