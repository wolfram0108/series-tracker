"""The engine of the rules constructor (Р-8) as the module uses it: the compiled engine of `core.native`.

Data format (a constraint): profile → rules (by priority) → conditions; templates are JSON lists of the blocks of the
visual constructor. A profile is compiled once (its templates made into patterns at load); titles are read without
compiling again. A rule that does not compile is left out of the profile and told with its reason (decision Б).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from core import native

# The words of the constructor: the values the engine understands. The source is the engine itself, not the lists of the
# panels: a panel offers a subset, and a client of the API may send anything.
_WORDS = native.vocabulary()
CONDITION_TYPES = tuple(_WORDS["condition_types"])
EXTRACT_TYPES = tuple(_WORDS["extract_types"])
ASSIGN_TYPES = tuple(_WORDS["assign_types"])
ACTION_TYPES = tuple(_WORDS["action_types"])
BLOCK_TYPES = tuple(_WORDS["block_types"])
# The actions of the torrent editor (stage 15): a value set as it is, under its own key of the result.
EDITOR_ASSIGN = dict(zip(_WORDS["editor_actions"], _WORDS["editor_keys"]))


@dataclass(frozen=True)
class CompiledProfile:
    profile_id: int
    compiled: native.Profile = field(repr=False)
    invalid_rules: list[dict] = field(default_factory=list)  # [{rule_id, name, error}] — decision Б


def compile_profile(profile_id: int, raw_rules: list[dict]) -> CompiledProfile:
    """raw_rules — in the form of the tables parser_rules / parser_rule_conditions."""
    compiled = native.Profile(json.dumps(raw_rules, ensure_ascii=False, default=str))
    return CompiledProfile(profile_id, compiled, json.loads(compiled.invalid))


def process_title(profile: CompiledProfile, title: str) -> dict[str, Any]:
    """{excluded, extracted, events, errors} of one title."""
    return json.loads(profile.compiled.process(title))


def parse_range_spec(raw: str | None) -> list[tuple[int, int]]:
    """The intervals of a range block («1, 3-10»), sorted, adjacent ones merged; ValueError for an empty or malformed one."""
    return native.range_spec(str(raw or ""))


def validate_rule(data: dict) -> None:
    """Checks a rule before it is written: ValueError with the reason. The rule is compiled by the code that applies the
    profile — there is no validator of its own, so the check and the run cannot part."""
    rule = {
        "id": 0,
        "name": data.get("name") or "Новое правило",
        "action_pattern": data.get("action_pattern") or "[]",
        "continue_after_match": data.get("continue_after_match") or 0,
        "conditions": data.get("conditions") or [],
        "rule_table": data.get("rule_table"),
    }
    error = native.rule_error(json.dumps(rule, ensure_ascii=False, default=str))
    if error is not None:
        raise ValueError(error)
