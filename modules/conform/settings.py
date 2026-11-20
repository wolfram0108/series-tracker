"""What the settings screen of the alignment server shows and what a save may write: the address and the work folder,
and how the daemon is asked to align (`ConformParams`: the fields of its jobs, checked against what the daemon says it
accepts). Whether finished jobs clear themselves from the queue is a pipeline flag (`conform.clear_finished`, "true" or
"false"), written by the debug tab like the other flags and only read here.

Pure functions over stored values: the module only carries their results across the bus. A save is checked as a whole and
refused with coded problems; a refused value is never written. The folders are named by the owner: nothing is assumed.
"""
from __future__ import annotations

import math
import os
from typing import Any, Mapping
from urllib.parse import urlsplit

from core.contracts import (ConformDomain, ConformParams, ConformSettingsDraft, ConformSettingsView, Reason,
                            SettingProblem)
from core.reasons import parse as parse_reason, reason

from . import paths

PREFIX = "conform."
URL_KEY = "conform.url"
WORK_DIR_KEY = "conform.work_dir"
REMOTE_DIR_KEY = "conform.work_dir_remote"
CLEAR_KEY = "conform.clear_finished"
AUDIO_KEY = "conform.audio_method"
DRIFT_KEY = "conform.drift_speed_pct"
KEEP_KEY = "conform.keep_tmp"
FILL_KEY = "conform.fill_silence"
EDGES_KEY = "conform.extend_edges"
REVISION_KEY = "conform.revision"
# What the service asked before these were settings: MuQ, no checkpoints, the daemon's own drift speed, the cuts filled
# with the reference, the reference of a compilation extended to the whole episode.
DEFAULTS = ConformParams(audio_method="muq", drift_speed_pct=None, keep_tmp=False, fill_silence=True, extend_edges=True)


def _problem(field: str, stored: str) -> SettingProblem:
    return SettingProblem(field=field, detail=Reason.model_validate(parse_reason(stored)))


def mapping_of(work_dir: str | None, work_dir_remote: str | None) -> dict[str, str]:
    """{folder here: the same folder at the daemon}; empty until the owner names both."""
    return {work_dir: work_dir_remote} if work_dir and work_dir_remote else {}


def clean(draft: ConformSettingsDraft) -> ConformSettingsDraft:
    """Address and folders trimmed."""
    return ConformSettingsDraft(url=draft.url.strip().rstrip("/"), work_dir=paths.folder(draft.work_dir),
                                work_dir_remote=paths.folder(draft.work_dir_remote))


def check(draft: ConformSettingsDraft) -> list[SettingProblem]:
    """What is wrong with the draft once it is cleaned: an address needs both folders, a folder is absolute and is
    not the top of a storage (results would be written beside everything else that lives there). The folder here is a
    path of this server; the folder of the daemon is a path in the daemon's own style (POSIX or Windows)."""
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
    here = (draft.work_dir, os.path.isabs, lambda path: path == "/" or os.path.ismount(path))
    there = (draft.work_dir_remote, paths.is_absolute, paths.is_root)
    for field, (path, is_absolute, is_root) in (("work_dir", here), ("work_dir_remote", there)):
        if path and not is_absolute(path):
            problems.append(_problem(field, reason("work_dir_not_absolute", path=path)))
        elif path and is_root(path):
            problems.append(_problem(field, reason("work_dir_is_root", path=path)))
    return problems


def stored(draft: ConformSettingsDraft) -> dict[str, str]:
    """The settings to write for a cleaned draft."""
    return {URL_KEY: draft.url, WORK_DIR_KEY: draft.work_dir, REMOTE_DIR_KEY: draft.work_dir_remote}


def _flag(raw: str | None, default: bool) -> bool:
    return default if raw not in ("true", "false") else raw == "true"


def params_of(values: Mapping[str, str | None]) -> ConformParams:
    """The parameters the stored values give; a value never stored, or one that is no value of its kind, is the default."""
    try:
        drift = float(values.get(DRIFT_KEY) or "") if values.get(DRIFT_KEY) else None
    except ValueError:
        drift = None
    return ConformParams(audio_method=(values.get(AUDIO_KEY) or "").strip() or DEFAULTS.audio_method,
                         drift_speed_pct=drift if drift is not None and math.isfinite(drift) else None,
                         keep_tmp=_flag(values.get(KEEP_KEY), DEFAULTS.keep_tmp),
                         fill_silence=_flag(values.get(FILL_KEY), DEFAULTS.fill_silence),
                         extend_edges=_flag(values.get(EDGES_KEY), DEFAULTS.extend_edges))


def domain_of(raw: Any) -> ConformDomain | None:
    """What the daemon accepts, from the `params` of its `GET /conform/settings`; None for an answer of another form."""
    try:
        audio, drift = raw["audio_method"], raw["drift_speed_pct"]
        return ConformDomain(audio_methods=[str(each) for each in audio["values"]], audio_default=str(audio["default"]),
                             drift_min=float(drift["min"]), drift_max=float(drift["max"]),
                             drift_default=float(drift["default"]))
    except (KeyError, TypeError, ValueError):
        return None


def check_params(params: ConformParams, domain: ConformDomain | None) -> list[SettingProblem]:
    """A method the daemon names and a drift speed within its range; while the daemon has not said what it accepts, the
    parameters cannot be checked and are not written."""
    if domain is None:
        return [_problem("params", reason("conform_params_unknown"))]
    problems = []
    if params.audio_method not in domain.audio_methods:
        problems.append(_problem("audio_method", reason("conform_audio_method_unknown", method=params.audio_method,
                                                        known=", ".join(domain.audio_methods))))
    drift = params.drift_speed_pct
    if drift is not None and not (math.isfinite(drift) and domain.drift_min <= drift <= domain.drift_max):
        problems.append(_problem("drift_speed_pct", reason("conform_drift_out_of_range", value=str(drift),
                                                           low=str(domain.drift_min), high=str(domain.drift_max))))
    return problems


def stored_params(params: ConformParams) -> dict[str, str]:
    """The settings to write for checked parameters; a drift speed of none leaves the daemon's own."""
    def flag(value: bool) -> str:
        return "true" if value else "false"
    return {AUDIO_KEY: params.audio_method, DRIFT_KEY: "" if params.drift_speed_pct is None else repr(params.drift_speed_pct),
            KEEP_KEY: flag(params.keep_tmp), FILL_KEY: flag(params.fill_silence), EDGES_KEY: flag(params.extend_edges)}


def episode_fields(params: ConformParams) -> dict[str, Any]:
    """The fields of the job of an episode (`POST /conform/enqueue`) the parameters give."""
    return {"audio_method": params.audio_method, "keep_tmp": params.keep_tmp, "fill_silence": params.fill_silence,
            **({"drift_speed_pct": params.drift_speed_pct} if params.drift_speed_pct is not None else {})}


def compilation_fields(params: ConformParams) -> dict[str, Any]:
    """The fields of the jobs of a compilation (`POST /conform/reverse`) the parameters give."""
    return {"audio_method": params.audio_method, "keep_tmp": params.keep_tmp, "extend_edges": params.extend_edges,
            **({"drift_speed_pct": params.drift_speed_pct} if params.drift_speed_pct is not None else {})}


def revision_of(values: Mapping[str, str | None]) -> int:
    raw = values.get(REVISION_KEY) or "0"
    return int(raw) if raw.isdigit() else 0


def next_revision(values: Mapping[str, str | None]) -> dict[str, str]:
    """The revision a save writes with its values: one more than the stored one."""
    return {REVISION_KEY: str(revision_of(values) + 1)}


def view(url: str, values: Mapping[str, str | None], domain: ConformDomain | None = None) -> ConformSettingsView:
    return ConformSettingsView(url=url, work_dir=values.get(WORK_DIR_KEY) or "",
                               work_dir_remote=values.get(REMOTE_DIR_KEY) or "",
                               clear_finished=values.get(CLEAR_KEY) == "true", params=params_of(values), domain=domain,
                               revision=revision_of(values))
