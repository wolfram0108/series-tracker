"""What makes a live run of the alignment a pass: positive evidence of every voice-over, not the absence of a bad sign.

Pure functions over what the instrument has read (the voice-overs of the window, the database, the disk, the service), so
that the rule is tested without a stand: a pass needs, for each voice-over, a result of the service for the file it was
sent as and a track at the path the database names, beside a reference video that exists, and nothing left in the
temporary folder. A result and a track are counted for one voice-over only. The instrument looks at one episode as a whole
(one reference); a run with parts is refused, not passed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping


def slot_key(record: Mapping[str, Any]) -> str:
    """What a voice-over is: season, episode, part (0 for the episode as a whole) and the key of the voice. The label is
    only its name: two parts of one voice-over have one."""
    return f"{record.get('season')}/{record.get('episode')}/{record.get('part') or 0}/{record.get('voice_key')}"


def judge_alignment(dubs: Iterable[Mapping[str, Any]], outputs: Mapping[str, str], inputs: Mapping[str, str],
                    reference: Path | None, pairs: Iterable[Mapping[str, Any]], temporary: Iterable[str]) -> list[str]:
    """The reasons the run is not a pass; none means a pass.

    `outputs` is {`slot_key`: the path of the track} as the database holds it for the voice-overs that are placed; `inputs`
    is {`slot_key`: the name of the file the voice-over was sent to the service as}; `pairs` are the results of the service
    for the jobs of the episode: each voice-over needs its own, for its own file, the last of them ok."""
    dubs, pairs, temporary = list(dubs), list(pairs), list(temporary)
    problems: list[str] = []
    if not dubs:
        return ["нет ни одной озвучки"]
    parts = sorted({dub["part"] for dub in dubs if dub.get("part")})
    if parts:
        problems.append(f"прибор проверяет серию целиком, без частей: озвучки частей {parts} им не проверены")
    unfinished = [dub.get("voice_label") for dub in dubs if dub.get("state") != "done"]
    if unfinished:
        problems.append(f"озвучки не готовы: {unfinished}")
    if reference is None or not reference.is_file():
        problems.append("референса нет на диске")
    counted: set[int] = set()
    for dub in dubs:
        label, key = dub.get("voice_label"), slot_key(dub)
        name = inputs.get(key)
        matching = [index for index, pair in enumerate(pairs) if name and pair.get("dub") == name]
        free = [index for index in matching if index not in counted]
        if not matching:
            problems.append(f"нет результата сервиса для озвучки {label!r} ({name!r})")
        elif not free:
            problems.append(f"результат сервиса для {name!r} уже засчитан другой озвучке")
        else:
            counted.add(free[-1])
            found = pairs[free[-1]]
            if not found.get("ok") or found.get("error"):
                problems.append(f"пара озвучки {label!r} не ok: {found.get('error')}")
        path = outputs.get(key)
        if not path or not Path(path).is_file():
            problems.append(f"дорожки озвучки {label!r} нет по пути базы: {path!r}")
        elif reference is not None and not (Path(path).parent == reference.parent and Path(path).name.startswith(reference.stem)):
            problems.append(f"дорожка озвучки {label!r} лежит не рядом с референсом: {path}")
    if temporary:
        problems.append(f"во временной папке осталось {len(temporary)}")
    return problems
