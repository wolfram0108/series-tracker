"""What the rules constructor is asked and what it answers: lines with keys in, numbers by key out."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RuleLine(BaseModel):
    """`player` and `voice` are the player and the voice-over of the row the text was made from: the constructor tells
    which lines name one episode of one voice-over by them, and does not read them out of the text."""

    key: str
    text: str
    player: str = ""
    voice: str = ""


class RulesApplyRequest(BaseModel):
    """`series_season` is the season of the series card: the fallback when no rule extracted a season."""

    profile_id: int
    lines: list[RuleLine]
    series_season: str | int | None = None


class RuleResult(BaseModel):
    """The only source of episode, season, range and part numbers; an excluded line has none of them."""

    model_config = ConfigDict(extra="allow")

    excluded: bool = False
    season: int | None = None
    episode: int | None = None
    start: int | None = None
    end: int | None = None
    part: int | None = None
    voiceover: str | None = None
    events: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[Any] = Field(default_factory=list)


class RulesApplyReply(BaseModel):
    results: dict[str, RuleResult]
    invalid_rules: list[dict[str, Any]] = Field(default_factory=list)


class RulesProfileChanged(BaseModel):
    profile_id: int
