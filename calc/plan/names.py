"""The names of the files of a plan as the series' own rule of names gives them (`rules.format_filename`): the episode as the rule is
asked about it, the key a name is kept by, the goals whose name is not held yet, and the namer a plan is counted with. Pure: the
module of the planner asks the rule between two plans and keeps what it answers."""
from __future__ import annotations

import json
import posixpath
from typing import Callable, Iterable, Mapping

from .planner import name_of


def item_of(slot, video) -> dict:
    """The episode as the rule of names is asked about it (`media_item` of `rules.format_filename`)."""
    snapshot = video.snapshot
    item = {"season": slot[0], "episode_start": slot[1], "episode_end": None, "part": slot[2] if len(slot) > 2 else None,
            "resolution": snapshot.height, "voiceover_tag": snapshot.group, "source_url": snapshot.link}
    if video.kind == "cut" or (video.container is not None and video.container.video.cut_of is not None):
        item["cut"] = json.dumps({"height": snapshot.height, "voice_group": snapshot.group})
    return item


def key_of(item: dict) -> str:
    return json.dumps(item, sort_keys=True, ensure_ascii=False)


def wanted_names(goals: Iterable, names: Mapping[tuple, str]) -> dict[tuple, tuple]:
    """The goals the names do not hold yet, by the key a name is kept by (the item as a text, whether the file is off): what
    the rule is given is what tells two names apart, and nothing else."""
    wanted = {}
    for goal in goals:
        item = item_of(goal.slot, goal.video)
        key = (key_of(item), goal.off)
        if key not in names:
            wanted[key] = (goal.slot, goal.video, item)
    return wanted


def filename_of(made: str, off: bool) -> str:
    """The name of the file of a goal out of the name the rule made: always a container, and `.off` for a file switched off."""
    return posixpath.splitext(made)[0] + ".mkv" + (".off" if off else "")


def namer_over(names: Mapping[tuple, str]) -> Callable:
    """The namer of a plan over the names held (read as they are when the plan asks): the rule's name, else the planner's own."""
    def namer(slot, video, off):
        return names.get((key_of(item_of(slot, video)), off)) or name_of(slot, video.snapshot, off)
    return namer
