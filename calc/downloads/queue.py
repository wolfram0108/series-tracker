"""The queue of the downloads as the window shows it: the cards, with the place of each waiting one."""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from core.contracts import DownloadCard, DownloadLimits, DownloadQueue

from .cards import ENDED
from .limits import room, total


def under_way(cards: Iterable[DownloadCard]) -> int:
    """The downloads that wait or go: a card of an ended one is no work."""
    return sum(card.status not in ENDED for card in cards)


def queue_of(cards: Iterable[DownloadCard], *, need_of: Mapping[str, str], placing: Mapping[str, str], waiting: Sequence[str],
             going: int, busy: Mapping[str, int], limits: DownloadLimits) -> DownloadQueue:
    """The cards, with the place of each waiting one: those waiting for a place of their way first (`placing`: fetch key -> way), with how
    many of their way go (`busy`) of how many may; then the queue (`waiting`: the needs in order), with how many fetches go (`going`) of how
    many may whatever their way. `need_of` names the need of each fetch key."""
    places: dict[str, tuple[int, int, int]] = {}
    for number, (key, way) in enumerate(placing.items(), start=1):
        places[need_of[key]] = (number, busy[way], room(limits, way))
    for number, need in enumerate(waiting, start=len(places) + 1):
        places[need] = (number, going, total(limits))
    shown = tuple(card.model_copy(update=dict(zip(("position", "running", "limit"), places[card.id]))) if card.id in places else card
                  for card in cards)
    return DownloadQueue(tasks=shown, count=under_way(shown))
