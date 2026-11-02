"""What makes a live run of the alignment a pass: positive evidence of every voice-over, not the absence of a bad sign.

Pure functions over what the instrument has read (the voice-overs of the window, the database, the disk, the service), so
that the rule is tested without a stand: a pass needs a result of the service for every voice-over, the track of each at
the path the database names, beside a reference video that exists, and nothing left in the temporary folder.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping


def judge_alignment(dubs: Iterable[Mapping[str, Any]], outputs: Mapping[str, str], reference: Path | None,
                    pairs: Iterable[Mapping[str, Any]], temporary: Iterable[str]) -> list[str]:
    """The reasons the run is not a pass; none means a pass.

    `outputs` is {label of the voice-over: the path of its track} as the database holds it for the voice-overs that are
    placed; `pairs` are the results of the service for the jobs of the episode: each voice-over needs its own, the last of them ok."""
    dubs, pairs, temporary = list(dubs), list(pairs), list(temporary)
    problems: list[str] = []
    if not dubs:
        return ["нет ни одной озвучки"]
    unfinished = [dub.get("voice_label") for dub in dubs if dub.get("state") != "done"]
    if unfinished:
        problems.append(f"озвучки не готовы: {unfinished}")
    if reference is None or not reference.is_file():
        problems.append("референса нет на диске")
    for dub in dubs:
        label = dub.get("voice_label")
        # The service names a pair by the file it was given: the series, the episode and the voice-over with its spaces made underscores.
        found = [pair for pair in pairs if str(pair.get("dub", "")).endswith(f"_{str(label).replace(' ', '_')}.mp4")]
        if not found:
            problems.append(f"нет результата сервиса для озвучки {label!r}")
        elif not found[-1].get("ok") or found[-1].get("error"):
            problems.append(f"пара озвучки {label!r} не ok: {found[-1].get('error')}")
        path = outputs.get(label)
        if not path or not Path(path).is_file():
            problems.append(f"дорожки озвучки {label!r} нет по пути базы: {path!r}")
        elif reference is not None and not (Path(path).parent == reference.parent and Path(path).name.startswith(reference.stem)):
            problems.append(f"дорожка озвучки {label!r} лежит не рядом с референсом: {path}")
    if temporary:
        problems.append(f"во временной папке осталось {len(temporary)}")
    return problems
