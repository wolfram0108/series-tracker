"""The settings of the service as the owner answers them: a value by its key, the values by a prefix, the saved paths."""
from __future__ import annotations

from .frozen import Frozen


class SettingValue(Frozen):
    """A key and its value: the answer of `settings.value.get` (none for a key that is not set) and `settings.value.set` (the
    value that was written), and the packet of the event `settings.changed`, which tells the value of a secret as an empty
    one, never as it is."""

    key: str
    value: str | None = None


class SettingValues(Frozen):
    """The answer of `settings.values.by_prefix` (a secret is never listed in bulk) and `settings.values.set_many` (the values
    that were written)."""

    values: dict[str, str]


class SavedPath(Frozen):
    id: int
    path: str


class SavedPaths(Frozen):
    """The paths the user saved for the folders of the library, after any change of them: `settings.paths.list`, `.add`,
    `.remove`."""

    paths: tuple[SavedPath, ...]
