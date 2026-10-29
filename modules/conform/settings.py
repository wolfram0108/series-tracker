"""What the settings screen of the alignment server shows and what a save may write: the address and the path map.

Pure functions over stored values: the module only carries their results across the bus. A save is checked as a
whole and refused with coded problems; a refused value is never written.
"""
from __future__ import annotations

import json
from typing import Mapping
from urllib.parse import urlsplit

from core.contracts import (ConformSettingsDraft, ConformSettingsView, PathPair, Reason, SettingProblem)
from core.reasons import parse as parse_reason, reason


URL_KEY = "conform.url"
PATH_MAP_KEY = "conform.path_map"


def _problem(field: str, stored: str) -> SettingProblem:
    return SettingProblem(field=field, detail=Reason.model_validate(parse_reason(stored)))


def _prefix(path: str) -> str:
    return path.strip().rstrip("/") or ("/" if path.strip().startswith("/") else "")


def pairs(mapping: Mapping[str, str]) -> list[PathPair]:
    return [PathPair(local=local, daemon=daemon) for local, daemon in mapping.items()]


def path_map_of(raw: str | None) -> dict[str, str]:
    """The stored map; empty when nothing usable is stored: there is no default, the owner names the folders."""
    try:
        value = json.loads(raw or "null")
    except (TypeError, ValueError):
        value = None
    if isinstance(value, dict) and value and all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        return value
    return {}


def clean(draft: ConformSettingsDraft) -> ConformSettingsDraft:
    """Address and prefixes trimmed, a row left blank dropped."""
    rows = [PathPair(local=_prefix(row.local), daemon=_prefix(row.daemon)) for row in draft.path_map
            if row.local.strip() or row.daemon.strip()]
    return ConformSettingsDraft(url=draft.url.strip().rstrip("/"), path_map=rows)


def check(draft: ConformSettingsDraft) -> list[SettingProblem]:
    """What is wrong with the draft once it is cleaned; a draft that passes needs no further repair."""
    draft = clean(draft)
    problems = []
    if draft.url:
        found = urlsplit(draft.url)
        if found.scheme not in ("http", "https") or not found.hostname or " " in draft.url:
            problems.append(_problem("url", reason("conform_url_invalid", url=draft.url)))
    if draft.url and not draft.path_map:
        problems.append(_problem("path_map", reason("path_map_empty")))
    seen: set[str] = set()
    for row in draft.path_map:
        if not row.local or not row.daemon:
            problems.append(_problem("path_map", reason("path_row_incomplete")))
            continue
        for path in (row.local, row.daemon):
            if not path.startswith("/"):
                problems.append(_problem("path_map", reason("path_not_absolute", path=path)))
        if row.local in seen:
            problems.append(_problem("path_map", reason("path_duplicate", path=row.local)))
        seen.add(row.local)
    return problems


def stored(draft: ConformSettingsDraft) -> dict[str, str]:
    """The settings to write for a cleaned draft."""
    return {URL_KEY: draft.url,
            PATH_MAP_KEY: json.dumps({row.local: row.daemon for row in draft.path_map}, ensure_ascii=False)}


def view(url: str, path_map: Mapping[str, str]) -> ConformSettingsView:
    return ConformSettingsView(url=url, path_map=pairs(path_map))
