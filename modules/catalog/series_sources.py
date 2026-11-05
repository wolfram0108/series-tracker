"""Pure rules of the sources of a series: what a source may carry, what kind a series is, what left `series`.

No bus and no database here, so that the repository and the module apply the same rules.
"""
from __future__ import annotations

from core.contracts import SeriesKind, SeriesSource, SourceType
from core.reasons import reason

# The columns that left `series`: the first four live in a source now, the last one went with its feature.
# They are refused by name, so that a caller still writing them fails instead of being ignored.
REMOVED_FIELDS = ("url", "source_type", "parser_profile_id", "vk_search_mode", "vk_quality_priority")

# What a caller may write to a source; `id` and `series_id` belong to the table.
SOURCE_FIELDS = ("position", "url", "source_type", "parser_profile_id", "vibix_season", "players_off")


def kind_of_type(source_type: str) -> str:
    """The kind of series a source of this type belongs to: a torrent, or video of any site."""
    return (SeriesKind.TORRENT if source_type == SourceType.TORRENT else SeriesKind.VIDEO).value


def kind_of(sources: list[dict]) -> str:
    """The kind of a series: the kind of its sources, which refuse_mixed_kinds keeps one; `video` with none."""
    torrent = any(kind_of_type(source["source_type"]) == SeriesKind.TORRENT for source in sources)
    return (SeriesKind.TORRENT if torrent else SeriesKind.VIDEO).value


def refuse_mixed_kinds(source_types: list[str]) -> None:
    """A series is a torrent one or a video one: torrent files are not laid out into episodes (stage 10), so
    the two do not meet in one series."""
    if len({kind_of_type(each) for each in source_types}) > 1:
        raise ValueError(reason("mixed_kinds"))


def refuse_removed_fields(fields: dict) -> None:
    for name in REMOVED_FIELDS:
        if name in fields:
            raise ValueError(f"поле сериала '{name}' удалено: источники задаются полем sources "
                             f"и запросами catalog.sources.*")


def checked_fields(fields: dict, stored: dict | None = None) -> dict:
    """The fields of a source checked against the contract.

    A new source (`stored` is None) comes back whole, with the defaults of the contract, except the position that
    the caller did not choose; a stored one comes back with the fields given, validated as part of the whole.
    """
    if not isinstance(fields, dict):
        raise ValueError("источник задаётся словарём полей")
    unknown = sorted(set(fields) - set(SOURCE_FIELDS))
    if unknown:
        raise ValueError(f"поле источника '{unknown[0]}' неизвестно; допустимы: {', '.join(SOURCE_FIELDS)}")
    model = SeriesSource.model_validate({**(stored or {"id": 0, "series_id": 0}), **fields})
    if not model.url.strip():
        raise ValueError("url: адрес источника не может быть пустым")
    if model.vibix_season is not None and model.vibix_season < 1:
        raise ValueError("vibix_season: сезоны каталога Vibix считаются с 1")
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
    refuse_mixed_kinds([draft["source_type"] for draft in drafts])
    urls = [draft["url"] for draft in drafts]
    for url in urls:
        if urls.count(url) > 1:
            raise ValueError(reason("source_exists", url=url))
    return drafts
