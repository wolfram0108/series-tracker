"""The cards of the downloads (core/contracts/downloads.py): one card to one need of a plan, kept through every fetch the plan
gives it. Pure bookkeeping over a clock: the module tells what happens, the book keeps the stages, the attempts and the
figures, and says which cards stay when a sync ends."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable

from core.contracts import STAGES, AttemptLine, DownloadCard, DownloadNeed, DownloadVariant, Figures

MB = 1024 ** 2
ENDED = ("done", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CardBook:
    def __init__(self, clock: Callable[[], float] = time.monotonic, wall: Callable[[], str] = _now) -> None:
        self.cards: dict[str, DownloadCard] = {}
        self._clock, self._wall = clock, wall
        # The running clocks of a card: when it was queued, when its stage under way began, when its variant was first tried.
        self._since: dict[str, float] = {}
        self._stage_since: dict[str, float] = {}
        self._variant_since: dict[str, float] = {}

    # --- what happens to a need ------------------------------------------------------------------------------------

    def queued(self, need: DownloadNeed, variant: DownloadVariant | None) -> DownloadCard:
        """A fetch of the need waits for its turn: a new card, or the plan rebuilt on another variant of a card it has."""
        card = self.cards.get(need.key)
        now = self._clock()
        if card is None or card.need.sync_started != need.sync_started:
            # A need of another sync is a new card: the outcome of the sync before is replaced.
            self.forget(need.key)
            card = DownloadCard(id=need.key, need=need, variant=variant, queued_at=self._wall())
            self.cards[need.key] = card
            self._since[need.key] = now
        elif variant is not None and card.variant is not None and variant != card.variant:
            card.log.append(AttemptLine(kind="replan", variant=variant))
            card.replanned = True
            card.variant, card.method, card.figures = variant, "", Figures()
            card.finished_at = ""
            for stage in card.stages[1:]:
                stage.state = "pending"
        self._since.setdefault(need.key, now)
        self._enter(card, "queue")
        card.status = "queued"
        return card

    def attempt(self, key: str, number: int, of: int) -> None:
        """An attempt of the variant begins: the player is asked for the link."""
        card = self.cards[key]
        last = card.log[-1] if card.log else None
        if last is None or last.kind != "attempt" or last.variant != card.variant:
            card.log.append(AttemptLine(kind="attempt", variant=card.variant))
            self._variant_since[key] = self._clock()
            last = card.log[-1]
        last.attempt, last.of, last.outcome = number, of, "running"
        for stage in card.stages[2:]:
            stage.state = "pending"
        self._enter(card, "link")

    def progress(self, key: str, data: dict) -> None:
        """What the download tells as it goes: its phase, the way the file comes, the figures."""
        card = self.cards.get(key)
        if card is None:
            return
        if data.get("method"):
            card.method = data["method"]
            if card.method in ("ranged", "ffmpeg"):
                card.stages[3].state = "skipped"
        phase = data.get("phase")
        figures = card.figures
        if phase == "link":
            figures.source_seconds = data.get("source_seconds")
        elif phase in ("download", "remux", "check"):
            if self._active(card) != phase:
                self._enter(card, phase)
                figures.progress = figures.speed = figures.speed_x = figures.eta = None
            if phase == "check" and data.get("got_seconds") is not None:
                figures.got_seconds = data["got_seconds"]
            if "progress" in data:
                figures.progress = data.get("progress")
                figures.eta = data.get("eta")
            if data.get("total_size_mb"):
                figures.total_mb = data["total_size_mb"]
                if figures.progress is not None:
                    figures.done_mb = round(figures.total_mb * figures.progress / 100, 1)
            if data.get("dlspeed"):
                figures.speed = round(data["dlspeed"] / MB, 1)
            if data.get("speed"):
                figures.speed_x = data["speed"]

    def attempt_failed(self, key: str) -> None:
        """An attempt ended without the file: its stage failed; the next attempt or the end of the fetch follows."""
        card = self.cards.get(key)
        if card is None:
            return
        stage = self._active(card)
        self._leave(card, "failed")
        if card.log and card.log[-1].kind == "attempt":
            card.log[-1].outcome, card.log[-1].stage = "failed", stage
            card.log[-1].seconds = self._clock() - self._variant_since.get(key, self._clock())

    def finished(self, key: str, ok: bool, *, height: int | None = None, size_mb: float | None = None) -> None:
        card = self.cards.get(key)
        if card is None:
            return
        if ok:
            self._leave(card, "done")
            for stage in card.stages:
                if stage.state == "pending":
                    stage.state = "skipped"
            if card.log and card.log[-1].kind == "attempt":
                card.log[-1].outcome = "done"
                card.log[-1].seconds = self._clock() - self._variant_since.get(key, self._clock())
            card.figures.got_height, card.figures.size_mb = height, size_mb
            card.figures.progress = card.figures.eta = card.figures.speed = card.figures.speed_x = None
            card.status = "done"
        else:
            if self._active(card) is not None:
                self.attempt_failed(key)
            card.status = "failed"
        card.finished_at = self._wall()
        card.seconds = self._clock() - self._since.get(key, self._clock())

    def withdrawn(self, key: str) -> None:
        """The owner does not need it any more: the card goes with the fetch."""
        self.forget(key)

    # --- the life of the cards --------------------------------------------------------------------------------------

    def run_started(self, series_id: int) -> list[str]:
        """A sync of the series begins: what the one before left goes."""
        gone = [key for key, card in self.cards.items() if card.need.series_id == series_id and card.status in ENDED]
        for key in gone:
            self.forget(key)
        return gone

    def run_ended(self, series_id: int) -> tuple[list[DownloadCard], list[str]]:
        """A sync of the series ended: a failed card stays (no variant was left to it) and so does a finished one whose plan
        was rebuilt; the others go. Returns (the cards that stay, the keys that go)."""
        stay, gone = [], []
        for key, card in list(self.cards.items()):
            if card.need.series_id != series_id or card.status not in ENDED:
                continue
            if card.status == "failed":
                if not card.log or card.log[-1].kind != "exhausted":
                    card.log.append(AttemptLine(kind="exhausted"))
                stay.append(card)
            elif card.replanned:
                stay.append(card)
            else:
                gone.append(key)
                self.forget(key)
        return stay, gone

    def kept(self, card: DownloadCard) -> None:
        """A card kept from before (the store, at a start): shown as it was left."""
        self.cards[card.id] = card

    def forget(self, key: str) -> None:
        for book in (self.cards, self._since, self._stage_since, self._variant_since):
            book.pop(key, None)

    # --- the view ---------------------------------------------------------------------------------------------------

    def running(self) -> bool:
        return any(card.status not in ENDED for card in self.cards.values())

    def view(self) -> list[DownloadCard]:
        """Every card with the time of what is under way counted to now."""
        now = self._clock()
        out = []
        for key, card in self.cards.items():
            shown = card.model_copy(deep=True)
            if card.status not in ENDED:
                shown.seconds = now - self._since.get(key, now)
                active = next((each for each in shown.stages if each.state == "active"), None)
                if active is not None:
                    active.seconds += now - self._stage_since.get(key, now)
                if shown.log and shown.log[-1].kind == "attempt" and shown.log[-1].outcome == "running":
                    shown.log[-1].seconds = now - self._variant_since.get(key, now)
            out.append(shown)
        return out

    # --- the stages -------------------------------------------------------------------------------------------------

    @staticmethod
    def _active(card: DownloadCard) -> str | None:
        return next((stage.name for stage in card.stages if stage.state == "active"), None)

    def _enter(self, card: DownloadCard, name: str) -> None:
        if self._active(card) == name:
            return
        self._leave(card, "done")
        stage = card.stages[STAGES.index(name)]
        stage.state = "active"
        self._stage_since[card.id] = self._clock()
        card.status = "queued" if name == "queue" else name

    def _leave(self, card: DownloadCard, state: str) -> None:
        stage = next((each for each in card.stages if each.state == "active"), None)
        if stage is None:
            return
        stage.seconds += self._clock() - self._stage_since.pop(card.id, self._clock())
        stage.state = state
