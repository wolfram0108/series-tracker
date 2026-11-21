"""The flags of the settings: a flag is kept as the text `true` or `false`, and the groups of the log that print debug lines are the
settings `debug_enabled_<group>`."""
from __future__ import annotations

from typing import Any, Mapping

DEBUG_PREFIX = "debug_enabled_"


def flag_text(value: Any) -> str:
    """A flag the window sends, as the text the settings keep."""
    return str(value).lower()


def flag_on(text: str | None) -> bool:
    """A flag the settings keep, as a boolean; one never set is off."""
    return (text or "false") == "true"


def debug_groups(values: Mapping[str, str]) -> set[str]:
    """The groups of the log whose debug is on, by the settings `debug_enabled_<group>` read by their prefix."""
    return {key.removeprefix(DEBUG_PREFIX) for key, value in values.items() if value == "true"}
