"""What the settings screen of the alignment server shows and what a save may write: the address and the work folder.
Whether finished jobs clear themselves from the queue is a pipeline flag (`conform.clear_finished`, "true" or "false"),
written by the debug tab like the other flags and only read here.

Pure functions over stored values: the module only carries their results across the bus. A save is checked as a whole and
refused with coded problems; a refused value is never written. The folders are named by the owner: nothing is assumed.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from core.contracts import ConformSettingsDraft, ConformSettingsView, Reason, SettingProblem
from core.reasons import parse as parse_reason, reason

URL_KEY = "conform.url"
WORK_DIR_KEY = "conform.work_dir"
REMOTE_DIR_KEY = "conform.work_dir_remote"
CLEAR_KEY = "conform.clear_finished"


def _problem(field: str, stored: str) -> SettingProblem:
    return SettingProblem(field=field, detail=Reason.model_validate(parse_reason(stored)))


def _folder(path: str) -> str:
    return path.strip().rstrip("/") or ("/" if path.strip().startswith("/") else "")


def mapping_of(work_dir: str | None, work_dir_remote: str | None) -> dict[str, str]:
    """{folder here: the same folder at the daemon}; empty until the owner names both."""
    return {work_dir: work_dir_remote} if work_dir and work_dir_remote else {}


def clean(draft: ConformSettingsDraft) -> ConformSettingsDraft:
    """Address and folders trimmed."""
    return ConformSettingsDraft(url=draft.url.strip().rstrip("/"), work_dir=_folder(draft.work_dir),
                                work_dir_remote=_folder(draft.work_dir_remote))


def check(draft: ConformSettingsDraft) -> list[SettingProblem]:
    """What is wrong with the draft once it is cleaned: an address needs both folders, a folder is absolute and is
    not the top of a storage (results would be written beside everything else that lives there)."""
    draft = clean(draft)
    problems = []
    if draft.url:
        found = urlsplit(draft.url)
        if found.scheme not in ("http", "https") or not found.hostname or " " in draft.url:
            problems.append(_problem("url", reason("conform_url_invalid", url=draft.url)))
        if not draft.work_dir:
            problems.append(_problem("work_dir", reason("work_dir_here_missing")))
        if not draft.work_dir_remote:
            problems.append(_problem("work_dir_remote", reason("work_dir_daemon_missing")))
    for field, path in (("work_dir", draft.work_dir), ("work_dir_remote", draft.work_dir_remote)):
        if path and not os.path.isabs(path):
            problems.append(_problem(field, reason("work_dir_not_absolute", path=path)))
        elif path == "/" or (field == "work_dir" and path and os.path.ismount(path)):
            problems.append(_problem(field, reason("work_dir_is_root", path=path)))
    return problems


def stored(draft: ConformSettingsDraft) -> dict[str, str]:
    """The settings to write for a cleaned draft."""
    return {URL_KEY: draft.url, WORK_DIR_KEY: draft.work_dir, REMOTE_DIR_KEY: draft.work_dir_remote}


def view(url: str, work_dir: str, work_dir_remote: str, clear_finished: str | None = None) -> ConformSettingsView:
    return ConformSettingsView(url=url, work_dir=work_dir, work_dir_remote=work_dir_remote,
                               clear_finished=clear_finished == "true")
