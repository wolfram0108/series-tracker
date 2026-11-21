"""What the tabs of the logs show: the groups of logging by modules, the entries of the log files filtered and ordered, the groups they hold."""
from __future__ import annotations

import json
from typing import Iterable, Mapping

from calc.gateway.flags import DEBUG_PREFIX, flag_on

# The groups of logging the window lists: a code of each section and the modules in it; the window words both (`logs.section.<code>`,
# `logs.module.<name>` of its catalog).
LOGGING_MODULES: dict[str, list[dict[str, str]]] = {
    "core": [{"name": "gateway"}, {"name": "catalog"}, {"name": "settings"}, {"name": "auth"}, {"name": "runlog"}],
    "pipeline": [{"name": "scan"}, {"name": "torrents"}, {"name": "unpack"}, {"name": "downloads"}, {"name": "planner"},
                 {"name": "renaming"}, {"name": "library"}, {"name": "voices"}, {"name": "conform"}],
    "sources": [{"name": "sources"}, {"name": "trackerauth"}, {"name": "rules"}, {"name": "composition"}, {"name": "metadata"}],
}

# The files of the log, the most serious first: what is read when the window names no level.
LOG_FILES = ("error.log", "warning.log", "info.log", "debug.log")


def log_files(level: str | None) -> list[str]:
    """The files a request for a level reads: the one of that level, or all of them."""
    return [f"{level.lower()}.log"] if level else list(LOG_FILES)


def logging_structure(flags: Mapping[str, str]) -> dict[str, list[dict]]:
    """The groups of logging by sections, each with whether its debug is on (`flags` are the settings `debug_enabled_<group>`)."""
    return {section: [{**module, "enabled": flag_on(flags.get(f"{DEBUG_PREFIX}{module['name']}"))} for module in modules]
            for section, modules in LOGGING_MODULES.items()}


def log_entries(files: Iterable[Iterable[str]], group: str | None, limit: int) -> list[dict]:
    """The entries of the lines of the files (each file's lines in order), the newest first: those of a group when one is named, the
    first `limit`. A line that is no JSON is passed over."""
    entries = []
    for lines in files:
        for line in reversed(list(lines)):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if group and entry.get("group") != group:
                continue
            entries.append(entry)
    entries.sort(key=lambda entry: entry.get("timestamp", ""), reverse=True)
    return entries[:limit]


def groups_in_logs(files: Iterable[Iterable[str]]) -> list[str]:
    """The groups the lines of the files hold, in order."""
    groups: set[str] = set()
    for lines in files:
        for line in lines:
            try:
                found = json.loads(line).get("group")
            except json.JSONDecodeError:
                continue
            if found:
                groups.add(found)
    return sorted(groups)
