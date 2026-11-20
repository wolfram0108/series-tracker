"""What the rules constructor is asked and what it answers: lines with keys in, numbers by key out.

The lines and the answers cross the bus as records kept by columns (`core.native`): a page is thousands of lines, and no
module makes an object of each of them on the way. A caller with a few lines gives them as a list; the request keeps them
in the one form.
"""
from __future__ import annotations

from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from core import native


class RuleLine(BaseModel):
    """`player` and `voice` are the player and the voice-over of the row the text was made from: the constructor tells
    which lines name one episode of one voice-over by them, and does not read them out of the text."""

    key: str
    text: str
    player: str = ""
    voice: str = ""


def lines_of(lines: Sequence[RuleLine]) -> bytes:
    """Lines given one by one, as the columns the constructor reads (key, text, player, voice)."""
    return native.encode_lines([each.key for each in lines], [each.text for each in lines],
                               [each.player for each in lines], [each.voice for each in lines])


class RulesApplyRequest(BaseModel):
    """`lines` are the lines kept by columns (`lines_of`, or `core.native.lines` of a table); a list of RuleLine is taken
    into that form. `series_season` is the season of the series card: the fallback when no rule extracted a season."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    profile_id: int
    lines: bytes
    series_season: str | int | None = None
    # The rules of the profile as a run took them (RunSnapshot.profiles): applied instead of the stored ones.
    rules: list[dict[str, Any]] | None = None

    @field_validator("lines", mode="before")
    @classmethod
    def _one_form(cls, value):
        if isinstance(value, (list, tuple)):
            return lines_of([each if isinstance(each, RuleLine) else RuleLine.model_validate(each) for each in value])
        return value

    @property
    def card(self) -> str | None:
        """The season of the card as the constructor reads it: its text, none for an empty one."""
        return str(self.series_season) if self.series_season else None


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


_BY_KEY = TypeAdapter(dict[str, RuleResult])


class RulesApplyReply(BaseModel):
    """`results`: the answer of every line, kept by columns in the order of the lines (empty: no line was asked)."""

    model_config = ConfigDict(ser_json_bytes="base64", val_json_bytes="base64")

    results: bytes = b""
    invalid_rules: list[dict[str, Any]] = Field(default_factory=list)

    @classmethod
    def of(cls, results: dict[str, RuleResult], invalid_rules: list[dict[str, Any]] | None = None) -> "RulesApplyReply":
        """The reply of answers given as models, in the order of their keys (a constructor that answers otherwise)."""
        return cls(results=native.encode_answers(_BY_KEY.dump_json(results)), invalid_rules=invalid_rules or [])

    def by_key(self) -> dict[str, RuleResult]:
        """The answers as models, by the key of their line: for a reader of a few lines."""
        return _BY_KEY.validate_json(native.answers_json(self.results))


class RulesProfileChanged(BaseModel):
    profile_id: int
