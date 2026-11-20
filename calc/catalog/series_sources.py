"""Pure rules of the sources of a series: what a source may carry, what left `series`.

No bus and no database here, so that the repository and the module apply the same rules.
"""
from __future__ import annotations

from calc.row_limit import normalized
from core.contracts import SeriesSource
from core.reasons import reason

# The columns that left `series`: the first four live in a source now, the last one went with its feature.
# They are refused by name, so that a caller still writing them fails instead of being ignored.
REMOVED_FIELDS = ("url", "source_type", "parser_profile_id", "vk_search_mode", "vk_quality_priority")

# What a caller may write to a source; `id` and `series_id` belong to the table.
SOURCE_FIELDS = ("position", "url", "source_type", "parser_profile_id", "vibix_season", "players_off", "rows_per_line")


def refuse_removed_fields(fields: dict) -> None:
    for name in REMOVED_FIELDS:
        if name in fields:
            raise ValueError(reason("series_field_removed", field=name))


def checked_fields(fields: dict, stored: dict | None = None) -> dict:
    """The fields of a source checked against the contract.

    A new source (`stored` is None) comes back whole, with the defaults of the contract, except the position that
    the caller did not choose; a stored one comes back with the fields given, validated as part of the whole.
    """
    if not isinstance(fields, dict):
        raise ValueError(reason("source_not_dict"))
    unknown = sorted(set(fields) - set(SOURCE_FIELDS))
    if unknown:
        raise ValueError(reason("source_field_unknown", field=unknown[0], allowed=", ".join(SOURCE_FIELDS)))
    model = SeriesSource.model_validate({**(stored or {"id": 0, "series_id": 0}), **fields})
    if not model.url.strip():
        raise ValueError(reason("source_url_empty"))
    if model.vibix_season is not None and model.vibix_season < 1:
        raise ValueError(reason("vibix_season_from_one"))
    rows = normalized(model.rows_per_line)
    if rows is None:
        raise ValueError(reason("rows_per_line_invalid", value=str(model.rows_per_line)[:40]))
    model = model.model_copy(update={"rows_per_line": rows})
    values = model.model_dump(mode="json")
    values["players_off"] = list(dict.fromkeys(values["players_off"]))
    names = tuple(fields) if stored else tuple(name for name in SOURCE_FIELDS
                                               if name != "position" or "position" in fields)
    return {name: values[name] for name in names}


def checked_sources(raw: object) -> list[dict]:
    """The sources a new series starts with: at least one, none twice, positions in the order given."""
    if not isinstance(raw, list) or not raw:
        raise ValueError(reason("no_source"))
    drafts = []
    for index, source in enumerate(raw):
        drafts.append({"position": index, **checked_fields(source)})
    urls = [draft["url"] for draft in drafts]
    for url in urls:
        if urls.count(url) > 1:
            raise ValueError(reason("source_exists", url=url))
    return drafts
