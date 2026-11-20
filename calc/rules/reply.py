"""What the constructor answers: a profile applied to the lines of a request, the rules of a profile with their marks, the
name of a file by the numbers that are ready or by a profile. Each answer is made of its inputs alone; the profile, the
card of the series and the stored rules are asked and read by the module."""
from __future__ import annotations

from collections import Counter

from core.contracts import RuleResult, RulesApplyReply, RulesApplyRequest
from core.reasons import reason

from . import formatter
from .engine import CompiledProfile, process_title, validate_rule
from .numbering import season_of, settle_parts

# The numbers the answer names itself; every other extracted key rides along as an extra field.
_NUMBER_KEYS = ("episode", "start", "end", "part")


def result_of(outcome: dict, series_season) -> RuleResult:
    """The constructor's answer for one line, built from the engine's outcome."""
    events, errors = outcome.get("events") or [], outcome.get("errors") or []
    if outcome.get("excluded"):
        return RuleResult(excluded=True, events=events, errors=errors)
    extracted = dict(outcome.get("extracted") or {})
    season = season_of(series_season, extracted.pop("season", None))
    voiceover = extracted.pop("voiceover", None)
    numbers = {key: extracted.pop(key, None) for key in _NUMBER_KEYS}
    return RuleResult(season=season, voiceover=voiceover, events=events,
                      errors=errors, **numbers, **extracted)


def repeated_keys(request: RulesApplyRequest) -> list[str]:
    """The keys the request names more than once: an answer is by key, so such a request is refused."""
    return sorted(key for key, count in Counter(line.key for line in request.lines).items() if count > 1)


def answers_of(profile: CompiledProfile, request: RulesApplyRequest) -> dict[str, RuleResult]:
    """The answer of the profile for every line of the request, by its key."""
    return settle_parts(request.lines, {line.key: result_of(process_title(profile, line.text), request.series_season)
                                        for line in request.lines})


def reply_of(profile: CompiledProfile, request: RulesApplyRequest) -> RulesApplyReply:
    """`rules.apply` answered: every line by its key, and the rules of the profile that are broken."""
    return RulesApplyReply(results=answers_of(profile, request), invalid_rules=profile.invalid_rules)


def marked(rules: list[dict], invalid_rules: list[dict]) -> list[dict]:
    """The stored rules of a profile, each with `valid` and, when the engine cannot compile it, `error`."""
    errors = {bad["rule_id"]: bad["error"] for bad in invalid_rules}
    return [{**rule, "valid": errors.get(rule.get("id")) is None, "error": errors.get(rule.get("id"))} for rule in rules]


def checked(data: dict) -> None:
    """A rule the engine cannot compile is refused to the user in the engine's words: a refusal, not a failure of the
    module (no trace in the journal)."""
    try:
        validate_rule(data)
    except ValueError as exc:
        raise ValueError(reason("rule_invalid", detail=str(exc))) from None


def filename_reply(series: dict, media_item: dict, episode_override: int | None = None,
                   original_filename: str | None = None) -> dict:
    """`rules.format_filename`: the name comes from the numbers of the item and the series card; no rule is applied."""
    metadata = formatter.build_metadata(series, media_item)
    if episode_override is not None:
        metadata["episode"] = episode_override
        metadata.pop("start", None)
        metadata.pop("end", None)
    return {"filename": formatter.format_name(series, metadata, original_filename=original_filename)}


def torrent_file_reply(profile: CompiledProfile, series: dict, file_basename: str, original_path: str) -> dict:
    """`rules.format_torrent_file`: the name of a torrent file in its 'Season NN' folder, the profile's rules applied to the
    name of the file; `filename` is None when the season cannot be told (the file is skipped)."""
    extracted = process_title(profile, file_basename).get("extracted") or {}
    season = formatter.torrent_season_folder(series, extracted, original_path)
    if season is None:
        return {"filename": None, "extracted": extracted}
    # The season the card may have overridden must reach `extracted`: a reader of the stored record would see the raw one.
    extracted["season"] = season
    formatter.merge_series_defaults(series, extracted, extracted)
    name = formatter.format_name(series, extracted, original_filename=file_basename, target_directory=f"Season {season:02d}")
    return {"filename": name, "extracted": extracted}
