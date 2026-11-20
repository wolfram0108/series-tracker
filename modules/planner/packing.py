"""The book of the cards of the packing (core/contracts/packing.py): one card to one container a sync builds. Bookkeeping over
a clock: the planner tells what happens, the book keeps the stages, the progress of the build and the steps of putting it in
place. The id of a card and the tracks of its container are calc/view/packing.py."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable, Iterable

from calc.plan.tables import label_of
from calc.view.packing import card_id, tracks_of
from core.contracts import PACK_STAGES, DownloadVariant, PackCard, PackJournal, SeriesCard

ENDED = ("done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PackBook:
    def __init__(self, clock: Callable[[], float] = time.monotonic, wall: Callable[[], str] = _now) -> None:
        self.cards: dict[str, PackCard] = {}
        self._clock, self._wall = clock, wall
        self._since: dict[str, float] = {}
        self._stage_since: dict[str, float] = {}
        self._steps: dict[str, set[str]] = {}  # the names of the files whose steps of the journal are the card's
        self._wait_since: dict[str, float] = {}
        self.journals: dict[int, PackJournal] = {}  # the journal of each series being carried out
        self._journal_since: dict[int, float] = {}

    # --- the journal of a series ----------------------------------------------------------------------------------

    def journal_begins(self, series: SeriesCard, run: str, steps: list[tuple], kind: str = "sync") -> None:
        """A journal of the series is carried out: every step of it is counted, whichever file it acts on."""
        self.journals[series.id] = PackJournal(series_id=series.id, series_name=series.name or "",
                                                  sync_started=run, kind=kind, steps_of=len(steps),
                                                  started_at=self._wall())
        self._journal_since[series.id] = self._clock()

    def journal_step(self, series_id: int, step: tuple) -> None:
        """A step of the journal begins: its kind and the file it acts on (the name it takes, for a move or a publish)."""
        journal = self.journals.get(series_id)
        if journal is not None:
            journal.step = step[0]
            journal.file = str(step[2] if step[0] in ("move", "publish") else step[1]).removesuffix(".moving")

    def journal_step_done(self, series_id: int) -> None:
        journal = self.journals.get(series_id)
        if journal is not None:
            journal.steps_done += 1

    def journal_ends(self, series_id: int) -> None:
        self.journals.pop(series_id, None)
        self._journal_since.pop(series_id, None)

    def journals_view(self) -> list[PackJournal]:
        now = self._clock()
        return [journal.model_copy(update={"seconds": now - self._journal_since.get(series_id, now)})
                for series_id, journal in sorted(self.journals.items())]

    # --- what happens to a container ------------------------------------------------------------------------------

    def planned(self, series: SeriesCard, run: str, made, before: dict, order: dict[int, tuple[str, ...]],
                marks: dict[int, tuple[int, int]]) -> None:
        """A plan of a sync: every container it builds is queued; a card of this sync no longer built goes."""
        wanted = set()
        for group in made.groups:
            for slot in group.built:
                goal = made.goal(slot)
                key = card_id(series.id, slot)
                wanted.add(key)
                old = self.cards.get(key)
                if old is not None and old.sync_started == run and old.name == goal.name:
                    continue
                previous = goal.video.container or next(
                    (each for each in before.values() if tuple(each.video.episode) == tuple(slot) and not each.off), None)
                tracks, reason = tracks_of(goal, previous, order.get(slot[0], ()))
                video = goal.video
                rid = video.row.id if video.row is not None else video.container.video.id if video.container else video.timeline
                n, color = marks.get(rid.source_id, (0, 0))
                card = PackCard(
                    id=key, series_id=series.id, series_name=series.name or "", sync_started=run,
                    episode=label_of(slot), name=goal.name, tracks=tracks, reason=reason, queued_at=self._wall(),
                    video=DownloadVariant(source_id=rid.source_id, source_n=n, source_color=color, place=video.snapshot.source,
                                          player=video.snapshot.player,
                                          player_label=(video.row.player_label if video.row is not None else "") or video.snapshot.player,
                                          height=video.snapshot.height or None))
                self.forget(key)
                self.cards[key] = card
                self._since[key] = self._clock()
                self._steps[key] = {goal.name} | ({previous.name} if previous is not None else set())
                self._enter(card, "queue")
        for key, card in list(self.cards.items()):
            if card.series_id == series.id and card.sync_started == run and key not in wanted and card.status == "queued":
                self.forget(key)

    def report(self, key: str, stage: str, figures: dict) -> bool:
        """What the build tells as it goes: "build" {progress, speed_x, eta, written_mb}, "verify", "built" (made aside and
        read back). True when the card came to another stage."""
        card = self.cards.get(key)
        if card is None:
            return False
        if stage == "built":
            self.built(key)
            return True
        moved = self._active(card) != stage
        if moved:
            self._enter(card, stage)
        if stage == "build":
            for name in ("progress", "speed_x", "eta", "written_mb"):
                setattr(card.figures, name, figures.get(name))
        return moved

    def built(self, key: str) -> None:
        """The container is made aside and read back: it waits, in no stage, for the journal of its group, which puts the
        files of the group in place (one journal of a series at a time)."""
        card = self.cards.get(key)
        if card is not None and card.status not in ENDED:
            self._leave(card, "done")
            card.status = "built"
            card.figures.progress = card.figures.speed_x = card.figures.eta = None
            self._wait_since[key] = self._clock()

    def waited(self, keys: Iterable[str]) -> None:
        """Not built: a step of the plan the container needs did not succeed; the file before stays."""
        for key in keys:
            card = self.cards.get(key)
            if card is not None and card.status not in ENDED:
                card.waited = True
        self.failed(keys)

    def failed(self, keys: Iterable[str], failure: str = "") -> None:
        """The build failed, for `failure` (the code of a build that failed, core/container.py; "" for another error): the
        file before stays."""
        for key in keys:
            card = self.cards.get(key)
            if card is None or card.status in ENDED:
                continue
            card.failure = failure
            self._leave(card, "failed")
            card.status = "failed"
            card.finished_at = self._wall()
            card.seconds = self._clock() - self._since.get(key, self._clock())

    def journal(self, series_id: int, steps: list[tuple]) -> None:
        """The journal of a group of the sync is written: every built card of the series whose files it names enters its
        stage of putting in place and counts the steps that name them. A card built for another group waits for the
        journal of its own."""
        for key, card in self.cards.items():
            if card.series_id == series_id and card.status == "built" and any(self._mine(key, step) for step in steps):
                card.figures.wait_s = self._clock() - self._wait_since.pop(key, self._clock())
                self._enter(card, "apply")
                card.figures.steps_of = sum(1 for step in steps if self._mine(key, step))
                card.figures.steps_done = 0

    def step_done(self, step: tuple) -> None:
        for key, card in self.cards.items():
            if card.status == "apply" and self._mine(key, step):
                card.figures.steps_done = (card.figures.steps_done or 0) + 1

    def applied(self, series_id: int, size_of: Callable[[str], float | None]) -> None:
        """The journal is carried out: every card of the series that waited for it is done."""
        for key, card in self.cards.items():
            if card.series_id == series_id and card.status == "apply":
                self._leave(card, "done")
                card.status = "done"
                card.figures.size_mb = size_of(card.name)
                card.finished_at = self._wall()
                card.seconds = self._clock() - self._since.get(key, self._clock())

    # --- the life of the cards --------------------------------------------------------------------------------------

    def sync_started(self, series_id: int) -> list[str]:
        """A sync of the series begins: what the one before left goes."""
        gone = [key for key, card in self.cards.items() if card.series_id == series_id]
        for key in gone:
            self.forget(key)
        return gone

    def sync_ended(self, series_id: int) -> tuple[list[PackCard], list[str]]:
        """A sync of the series ended: a failed card stays, every other goes. Returns (the cards that stay, the keys that
        go)."""
        stay, gone = [], []
        for key, card in list(self.cards.items()):
            if card.series_id != series_id:
                continue
            if card.status == "failed":
                stay.append(card)
            else:
                gone.append(key)
                self.forget(key)
        return stay, gone

    def kept(self, card: PackCard) -> None:
        self.cards[card.id] = card

    def forget(self, key: str) -> None:
        for book in (self.cards, self._since, self._stage_since, self._steps, self._wait_since):
            book.pop(key, None)

    def running(self) -> bool:
        return bool(self.journals) or any(card.status not in ENDED for card in self.cards.values())

    def view(self) -> list[PackCard]:
        now = self._clock()
        out = []
        for key, card in sorted(self.cards.items(), key=lambda item: (item[1].series_id, item[1].episode)):
            if card.status in ENDED:
                out.append(card)  # nothing of an ended card counts on: it is shown as it is, not copied on every beat
                continue
            shown = card.model_copy(deep=True)
            shown.seconds = now - self._since.get(key, now)
            active = next((each for each in shown.stages if each.state == "active"), None)
            if active is not None:
                active.seconds += now - self._stage_since.get(key, now)
            if card.status == "built":
                shown.figures.wait_s = now - self._wait_since.get(key, now)
            out.append(shown)
        return out

    # --- the stages -------------------------------------------------------------------------------------------------

    def _mine(self, key: str, step: tuple) -> bool:
        """A step of the journal names the files it acts on (`publish` its target, `move` the name it takes away, out of
        the way under `.moving`, `remove` and `retag` the file): it is the card's when it names its new or old file."""
        names = self._steps.get(key, set())
        return any(isinstance(each, str) and each.removesuffix(".moving") in names for each in step[1:])

    @staticmethod
    def _active(card: PackCard) -> str | None:
        return next((stage.name for stage in card.stages if stage.state == "active"), None)

    def _enter(self, card: PackCard, name: str) -> None:
        if self._active(card) == name:
            return
        self._leave(card, "done")
        card.stages[PACK_STAGES.index(name)].state = "active"
        self._stage_since[card.id] = self._clock()
        card.status = "queued" if name == "queue" else name

    def _leave(self, card: PackCard, state: str) -> None:
        stage = next((each for each in card.stages if each.state == "active"), None)
        if stage is None:
            return
        stage.seconds += self._clock() - self._stage_since.pop(card.id, self._clock())
        stage.state = state
