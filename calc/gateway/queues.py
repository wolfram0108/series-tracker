"""The queues as the window of the old contract takes them."""
from __future__ import annotations

from typing import Any, Iterable, Mapping


def agent_tasks(tasks: Iterable[Mapping[str, Any]]) -> list[dict]:
    """The tasks of the torrent pipeline with the `hash` field of the old contract (the HTTP answer and the event of the stream are the
    same shape: finding 39)."""
    return [{"hash": task.get("torrent_hash"), **task} for task in tasks]
