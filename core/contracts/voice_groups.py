"""The question the layout asks the voices module: to which user group does each voice-over name belong."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GroupsResolveRequest(BaseModel):
    names: list[str]
    # The stored settings of the voice-overs as a run took them (RunSnapshot.voices): resolved by them, not by the current.
    values: dict[str, Any] | None = None


class GroupsResolveReply(BaseModel):
    """A name that matches no trigger of any group maps to None."""

    groups: dict[str, str | None] = Field(default_factory=dict)
