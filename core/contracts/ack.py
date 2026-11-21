"""The answers of a command that has nothing to tell but how it ended: it was done, what it made, what of the change applied.

A command is answered with the model of its kind, not with a dictionary: the caller reads a field, and the registry
(`core/topics.py`) names the model of every such answer.
"""
from __future__ import annotations

from typing import Any

from .frozen import Frozen


class Done(Frozen):
    """The command was carried out; there is nothing more to tell."""

    ok: bool = True


class Created(Frozen):
    """The command made a record: its number."""

    id: int


class Applied(Frozen):
    """What of a change the owner took: the fields that differed from the stored ones; none means nothing was written."""

    applied: dict[str, Any]
