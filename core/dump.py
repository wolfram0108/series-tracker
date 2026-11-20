"""Plain data of any value of the service, for the dump of a run (modules/runlog): what the planner works by, written out
whole so that a reader of the dump sees the very tables a plan was made of.

A dataclass is its fields under its type's name (so is an object of the core that names its fields: `__fields__`), a mapping with keys other than text is a list of [key, value] pairs, a set
is a list in a stable order, an enum is its value; what is none of them is its `repr`, never dropped."""
from __future__ import annotations

import dataclasses
import enum
from typing import Any


def plain(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, enum.Enum):
        return plain(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"@": type(value).__name__,
                **{each.name: plain(getattr(value, each.name)) for each in dataclasses.fields(value)}}
    named = getattr(type(value), "__fields__", None)
    if isinstance(named, (list, tuple)):
        return {"@": type(value).__name__, **{name: plain(getattr(value, name)) for name in named}}
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return {key: plain(each) for key, each in value.items()}
        return [[plain(key), plain(each)] for key, each in value.items()]
    if isinstance(value, (set, frozenset)):
        return sorted((plain(each) for each in value), key=repr)
    if isinstance(value, (list, tuple)):
        return [plain(each) for each in value]
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return repr(value)
