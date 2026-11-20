"""The identity and the tracks of a card of the packing (core/contracts/packing.py): one card to one container a sync builds, the
container's tracks in its order as numbers of the season, where each comes from, what the file before holds that it does not,
and why a container is built when no track came or went. The book that keeps the cards over a clock is the planner module's
(modules/planner/packing.py)."""
from __future__ import annotations

from calc.plan.tables import label_of
from core.contracts import PackTrack


def card_id(series_id: int, slot) -> str:
    return f"{series_id}:{label_of(slot)}"


def tracks_of(goal, before, order: tuple[str, ...]) -> tuple[list[PackTrack], str]:
    """The tracks of the container a goal builds, in its order, with where each comes from, the tracks of the file before
    that it does not hold (`gone`), and why it is built when no track came or went."""
    number = {label: place for place, label in enumerate(order, start=1)}
    own = goal.video.snapshot.group or "—"
    placed = [PackTrack(number=number.get(way.snapshot.group or way.voice), label=way.snapshot.group or way.voice,
                        kind="kept" if way.kind == "reuse" else "new") for way in goal.tracks]
    placed.insert(goal.own_at, PackTrack(number=number.get(own), label=own, kind="own"))
    held = [track.snapshot.group or "" for track in before.tracks] if before is not None else []
    here = {track.label for track in placed}
    gone = [PackTrack(number=number.get(label), label=label, kind="gone") for label in held if label and label not in here]
    if any(track.kind == "new" for track in placed) or gone or before is None:
        return placed + gone, ""
    if before.video.id != (goal.video.container.video.id if goal.video.container is not None else None):
        return placed + gone, "video"
    if [label for label in held] != [track.label for track in placed if track.kind != "own"]:
        return placed + gone, "order"
    return placed + gone, "names"
