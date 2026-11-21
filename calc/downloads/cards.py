"""The cards of the downloads (core/contracts/downloads.py): one card to one need of a plan, kept through every fetch the plan
gives it. A `Book` holds the cards and the running clocks of each; every happening is a function that gives the book that follows
it, the time of the happening a given. The module tells what happens, the book keeps the stages, the attempts and the figures, and
says which cards stay when a sync ends."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from core.contracts import STAGES, AttemptLine, DownloadCard, DownloadNeed, DownloadVariant, Figures, Progress

MB = 1024 ** 2
ENDED = ("done", "failed")


@dataclass(frozen=True)
class Book:
    """The cards in the order they came, and for each the clocks that run (monotonic seconds): when it was queued, when its stage under
    way began, when its variant was first tried. A book is never changed: a function of this file gives the next one."""

    cards: Mapping[str, DownloadCard] = field(default_factory=dict)
    since: Mapping[str, float] = field(default_factory=dict)
    stage_since: Mapping[str, float] = field(default_factory=dict)
    variant_since: Mapping[str, float] = field(default_factory=dict)


class _Draft:
    """One card while a happening changes it: the card and its clocks are taken in, changed in the copies this holds, and made again
    by `done`. Nothing outside the function that holds it sees it."""

    def __init__(self, book: Book, key: str, now: float) -> None:
        self.key, self.now = key, now
        self.card = book.cards[key]
        self.stages = list(self.card.stages)
        self.log = list(self.card.log)
        self.fields: dict[str, Any] = {}
        self.figures: dict[str, Any] = {}
        self.since = book.since.get(key)
        self.stage_since = book.stage_since.get(key)
        self.variant_since = book.variant_since.get(key)

    @property
    def status(self) -> str:
        return self.fields.get("status", self.card.status)

    def active(self) -> str | None:
        return next((stage.name for stage in self.stages if stage.state == "active"), None)

    def stage(self, index: int, **changes: Any) -> None:
        self.stages[index] = self.stages[index].model_copy(update=changes)

    def last(self, **changes: Any) -> None:
        self.log[-1] = self.log[-1].model_copy(update=changes)

    def enter(self, name: str) -> None:
        if self.active() == name:
            return
        self.leave("done")
        self.stage(STAGES.index(name), state="active")
        self.stage_since = self.now
        self.fields["status"] = "queued" if name == "queue" else name

    def leave(self, state: str) -> None:
        index = next((at for at, each in enumerate(self.stages) if each.state == "active"), None)
        if index is None:
            return
        stage = self.stages[index]
        self.stage(index, seconds=stage.seconds + self.now - (self.now if self.stage_since is None else self.stage_since),
                   state=state)
        self.stage_since = None

    def fail_attempt(self) -> None:
        stage = self.active()
        self.leave("failed")
        if self.log and self.log[-1].kind == "attempt":
            self.last(outcome="failed", stage=stage,
                      seconds=self.now - (self.now if self.variant_since is None else self.variant_since))

    def done(self, book: Book) -> Book:
        card = self.card.model_copy(update={**self.fields, "stages": tuple(self.stages), "log": tuple(self.log),
                                            **({"figures": self.card.figures.model_copy(update=self.figures)}
                                               if self.figures else {})})
        return Book({**book.cards, self.key: card}, _set(book.since, self.key, self.since),
                    _set(book.stage_since, self.key, self.stage_since),
                    _set(book.variant_since, self.key, self.variant_since))


def _set(clocks: Mapping[str, float], key: str, value: float | None) -> Mapping[str, float]:
    if value is None:
        return {name: each for name, each in clocks.items() if name != key}
    return {**clocks, key: value}


# --- what happens to a need ----------------------------------------------------------------------------------------------

def queued(book: Book, need: DownloadNeed, variant: DownloadVariant | None, now: float, wall: str) -> Book:
    """A fetch of the need waits for its turn: a new card, or the plan rebuilt on another variant of a card it has."""
    key = need.key
    card = book.cards.get(key)
    if card is None or card.need.sync_started != need.sync_started:
        # A need of another sync is a new card: the outcome of the sync before is replaced.
        book = forget(book, key)
        book = replace(book, cards={**book.cards, key: DownloadCard(id=key, need=need, variant=variant, queued_at=wall)},
                       since={**book.since, key: now})
    elif variant is not None and card.variant is not None and variant != card.variant:
        book = replace(book, cards={**book.cards, key: card.model_copy(update={
            "log": (*card.log, AttemptLine(kind="replan", variant=variant)), "replanned": True, "variant": variant, "method": "",
            "figures": Figures(), "finished_at": "",
            "stages": (card.stages[0], *(stage.model_copy(update={"state": "pending"}) for stage in card.stages[1:]))})})
    if key not in book.since:
        book = replace(book, since={**book.since, key: now})
    draft = _Draft(book, key, now)
    draft.enter("queue")
    draft.fields["status"] = "queued"
    return draft.done(book)


def attempt(book: Book, key: str, number: int, of: int, now: float) -> Book:
    """An attempt of the variant begins: the player is asked for the link."""
    draft = _Draft(book, key, now)
    last = draft.log[-1] if draft.log else None
    if last is None or last.kind != "attempt" or last.variant != draft.card.variant:
        draft.log.append(AttemptLine(kind="attempt", variant=draft.card.variant))
        draft.variant_since = now
    draft.last(attempt=number, of=of, outcome="running")
    for index in range(2, len(draft.stages)):
        draft.stage(index, state="pending")
    draft.enter("link")
    return draft.done(book)


def waiting(book: Book, key: str, now: float) -> Book:
    """The attempt waits for a place of the way its file comes (limits.py): the card is in the queue again."""
    if key not in book.cards:
        return book
    draft = _Draft(book, key, now)
    draft.enter("queue")
    return draft.done(book)


def progress(book: Book, key: str, data: Progress, now: float) -> Book:
    """What the download tells as it goes: its phase, the way the file comes, the figures. A report names what it knows (`model_fields_set`):
    a figure it does not name stays as it was."""
    if key not in book.cards:
        return book
    draft = _Draft(book, key, now)
    shown = draft.card.figures

    def current(name: str) -> Any:
        return draft.figures.get(name, getattr(shown, name))

    if data.method:
        draft.fields["method"] = data.method
        if data.method in ("ranged", "ffmpeg"):
            draft.stage(3, state="skipped")
    phase = data.phase
    if phase == "link":
        draft.figures["source_seconds"] = data.source_seconds
    elif phase in ("download", "remux", "check"):
        if draft.active() != phase:
            draft.enter(phase)
            draft.figures.update(progress=None, speed=None, speed_x=None, eta=None)
        if phase == "check" and data.got_seconds is not None:
            draft.figures["got_seconds"] = data.got_seconds
        if "progress" in data.model_fields_set:
            draft.figures.update(progress=data.progress, eta=data.eta)
        if data.total_size_mb:
            draft.figures["total_mb"] = data.total_size_mb
            if current("progress") is not None:
                draft.figures["done_mb"] = round(data.total_size_mb * current("progress") / 100, 1)
        if data.dlspeed:
            draft.figures["speed"] = round(data.dlspeed / MB, 1)
        if data.speed:
            draft.figures["speed_x"] = data.speed
    return draft.done(book)


def attempt_failed(book: Book, key: str, now: float) -> Book:
    """An attempt ended without the file: its stage failed; the next attempt or the end of the fetch follows."""
    if key not in book.cards:
        return book
    draft = _Draft(book, key, now)
    draft.fail_attempt()
    return draft.done(book)


def finished(book: Book, key: str, ok: bool, now: float, wall: str, *, height: int | None = None,
             size_mb: float | None = None) -> Book:
    if key not in book.cards:
        return book
    draft = _Draft(book, key, now)
    if ok:
        draft.leave("done")
        for index, stage in enumerate(draft.stages):
            if stage.state == "pending":
                draft.stage(index, state="skipped")
        if draft.log and draft.log[-1].kind == "attempt":
            draft.last(outcome="done", seconds=now - (now if draft.variant_since is None else draft.variant_since))
        draft.figures.update(got_height=height, size_mb=size_mb, progress=None, eta=None, speed=None, speed_x=None)
        draft.fields["status"] = "done"
    else:
        if draft.active() is not None:
            draft.fail_attempt()
        draft.fields["status"] = "failed"
    draft.fields["finished_at"] = wall
    draft.fields["seconds"] = now - (now if draft.since is None else draft.since)
    return draft.done(book)


def withdrawn(book: Book, key: str) -> Book:
    """The owner does not need it any more: the card goes with the fetch."""
    return forget(book, key)


# --- the life of the cards -----------------------------------------------------------------------------------------------

def run_started(book: Book, series_id: int) -> tuple[Book, list[str]]:
    """A sync of the series begins: what the one before left goes. Returns the book and the keys that went."""
    gone = [key for key, card in book.cards.items() if card.need.series_id == series_id and card.status in ENDED]
    for key in gone:
        book = forget(book, key)
    return book, gone


def run_ended(book: Book, series_id: int) -> tuple[Book, list[DownloadCard], list[str]]:
    """A sync of the series ended: a failed card stays (no variant was left to it) and so does a finished one whose plan was rebuilt; the
    others go. Returns the book, the cards that stay and the keys that go."""
    stay: list[DownloadCard] = []
    gone: list[str] = []
    for key, card in list(book.cards.items()):
        if card.need.series_id != series_id or card.status not in ENDED:
            continue
        if card.status == "failed":
            if not card.log or card.log[-1].kind != "exhausted":
                card = card.model_copy(update={"log": (*card.log, AttemptLine(kind="exhausted"))})
                book = replace(book, cards={**book.cards, key: card})
            stay.append(card)
        elif card.replanned:
            stay.append(card)
        else:
            gone.append(key)
            book = forget(book, key)
    return book, stay, gone


def kept(book: Book, card: DownloadCard) -> Book:
    """A card kept from before (the store, at a start): shown as it was left."""
    return replace(book, cards={**book.cards, card.id: card})


def forget(book: Book, key: str) -> Book:
    return Book(*(_set(each, key, None) for each in (book.cards, book.since, book.stage_since, book.variant_since)))


# --- the view ------------------------------------------------------------------------------------------------------------

def running(book: Book) -> bool:
    return any(card.status not in ENDED for card in book.cards.values())


def view(book: Book, now: float) -> tuple[DownloadCard, ...]:
    """Every card with the time of what is under way counted to `now`."""
    out = []
    for key, card in book.cards.items():
        if card.status not in ENDED:
            changes: dict[str, Any] = {"seconds": now - book.since.get(key, now)}
            stages = list(card.stages)
            at = next((index for index, each in enumerate(stages) if each.state == "active"), None)
            if at is not None:
                stages[at] = stages[at].model_copy(update={"seconds": stages[at].seconds + now - book.stage_since.get(key, now)})
                changes["stages"] = tuple(stages)
            if card.log and card.log[-1].kind == "attempt" and card.log[-1].outcome == "running":
                last = card.log[-1].model_copy(update={"seconds": now - book.variant_since.get(key, now)})
                changes["log"] = (*card.log[:-1], last)
            card = card.model_copy(update=changes)
        out.append(card)
    return tuple(out)
