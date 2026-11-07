"""Which voice-overs of a series are planned, and where each one comes from: a pure function, no bus and no database.

The input is what the other modules already decided: the rows of the layout (their numbers are the constructor's,
their voice-over group is `voice_label`), the reference of every episode (`scan`) and the output levers of every
episode (`composition`). The voice-overs of an episode are every voice of the layout but the one already in the
reference video, except the voices the levers turn off (`voice.take`); the candidates of a voice are its rows that cover
the episode and play, but the variants the user left out (`voice.allow`), in the queue of `core.variant_queue`: the
variants of the user's order (`voice.order`) first, in his order, then the others by the rule of voice-overs.
A row that covers several episodes (a compilation) is a candidate like any other; its video is one file for every
episode it covers.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, NamedTuple, Sequence

from core.variant_queue import VOICE_RANKING, Candidate as QueueItem, Order, ranked
from core.contracts import (DubStatus as S, VOICE_ALLOW, VOICE_ORDER, VOICE_TAKE, CompositionRow, EffectiveLever, EpisodeSettings,
                            Reference, ReferenceMark, RowState, group_target, parse_variant_target)


# A reference with one of these marks is a video of the layout the voice-overs are aligned to.
_REFERENCED = frozenset({ReferenceMark.BY_LEVEL, ReferenceMark.OWN, ReferenceMark.OTHER_PLAYER})

Slot = tuple[int, int, int]  # (season, episode, part); part 0 is an episode without parts


class Candidate(NamedTuple):
    """One place a voice-over of an episode can be downloaded from: the form of a link of `voice_dubs.chain`.

    `file` is (source id, row key) of a row that covers several episodes: its video is downloaded once for all of them.
    `source_id` and `player` are what the variant is: the link is only where it shows its video now, and it may change.
    `row_key` with `source_id` is which video it is: the identity a started work is told by, whatever its link.
    `place` is where its source has its rows (release_table.place_of): with the player it names the file of the video.
    `video` is which video it is as its host numbers it: one video shown by two pages is two links of one video."""

    player: str
    studio: str
    url: str
    file: tuple[int, str] | None = None
    source_id: int | None = None
    row_key: str | None = None
    place: tuple[str, ...] = ()
    video: str = ""

    def as_json(self) -> dict[str, Any]:
        found: dict[str, Any] = {"player": self.player, "studio": self.studio, "url": self.url}
        if self.file is not None:
            found["file"] = {"source_id": self.file[0], "row_key": self.file[1]}
        if self.source_id is not None:
            found["source_id"] = self.source_id
        if self.row_key is not None:
            found["row_key"] = self.row_key
        if self.place:
            found["place"] = list(self.place)
        if self.video:
            found["video"] = self.video
        return found


@dataclass(frozen=True)
class PlannedDub:
    """A voice-over of an episode. An empty chain is a voice the episode has no playing row for."""

    season: int
    episode: int
    part: int
    voice_key: str
    voice_label: str
    chain: tuple[Candidate, ...]
    # The variants the user left out, as (source id, player): no work may run from them. A chain recorded before its
    # candidates said which source they came from is told by the links the variants showed.
    left_out: frozenset[tuple[int, str]] = frozenset()
    left_out_links: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Plan:
    """`with_reference`: episodes whose reference is a row of the layout. `undecided`: episodes whose reference row the
    layout does not have (the two were read at different moments): their voice-overs are neither planned nor dropped.
    Every other episode has no reference (none, unfit, or none given)."""

    dubs: tuple[PlannedDub, ...] = ()
    with_reference: frozenset[Slot] = frozenset()
    undecided: frozenset[Slot] = frozenset()


def plan(rows: Iterable[CompositionRow], references: Iterable[Reference], settings: Iterable[EpisodeSettings],
         priority: Sequence[str], players, places: Mapping[int, Sequence[str]] | None = None,
         own: Mapping[Slot, str] | None = None) -> Plan:
    """The voice-overs to plan; `players` is the user's groups of players (`core.voice_names.Groups`), `places` where
    each source (by its id) has its rows. The voice-over the video of an episode carries itself is that of its reference
    row, or the one `own` names for the episode (stage 12: the voice-over of the compilation its video is cut out of, while
    the cut has not failed): it is not planned, and the voice-over of the reference row is planned like any other."""
    by_key: dict[tuple[int, str], CompositionRow] = {}
    by_voice: dict[tuple[int, str], list[CompositionRow]] = {}
    voices: dict[str, None] = {}
    for row in rows:
        by_key[(row.source_id, row.row_key)] = row
        if row.excluded or row.season is None or row.episode_start is None or row.episode_end is None or not row.grouped:
            continue  # an unknown voice-over is no voice-over of the episode until the user puts it into a group
        voices.setdefault(row.voice_label)
        by_voice.setdefault((row.season, row.voice_label), []).append(row)
    levers = {(each.season, each.episode): each.levers for each in settings}
    dubs: list[PlannedDub] = []
    with_reference: set[Slot] = set()
    undecided: set[Slot] = set()
    for ref in sorted(references, key=lambda each: (each.season, each.episode)):
        slot = (ref.season, ref.episode, ref.part or 0)
        if ref.mark not in _REFERENCED or ref.source_id is None or ref.row_key is None:
            continue
        ref_row = by_key.get((ref.source_id, ref.row_key))
        if ref_row is None:
            undecided.add(slot)
            continue
        with_reference.add(slot)
        take_off, orders, denied = _levers(levers.get((ref.season, ref.episode), ()))
        carried = (own or {}).get(slot, ref_row.voice_label)
        for label in voices:
            if label == carried or group_target(label) in take_off:
                continue
            fitting = [row for row in by_voice.get((ref.season, label), ()) if _covers(row, ref.episode, ref_row.part)]
            covering = [row for row in fitting if (label, row.source_id, row.player) not in denied]
            chain = _chain(covering, orders.get(group_target(label), ()), priority, players, places or {})
            kept = {each.url for each in chain}
            left = [row for row in fitting if (label, row.source_id, row.player) in denied and row.link not in kept]
            dubs.append(PlannedDub(ref.season, ref.episode, slot[2], label, label, chain,
                                   frozenset((row.source_id, row.player) for row in left),
                                   frozenset(row.link for row in left if row.link)))
    return Plan(tuple(dubs), frozenset(with_reference), frozenset(undecided))


def _levers(levers: Iterable[EffectiveLever]) -> tuple[set[str], dict[str, list[tuple[int, str]]], set[tuple[str, int, str]]]:
    """(targets taken off, {target: the (source id, player) the user put first, in his order}, (group, source id, player)
    of the variants the user left out) of the levers of one episode."""
    take_off: set[str] = set()
    orders: dict[str, list[tuple[int, str]]] = {}
    denied: set[tuple[str, int, str]] = set()
    for lever in levers:
        if lever.target is None:
            continue
        if lever.lever == VOICE_ALLOW and lever.value is False:
            variant = parse_variant_target(lever.target)
            if variant is not None:
                denied.add(variant)
        elif lever.lever == VOICE_TAKE and lever.value is False:
            take_off.add(lever.target)
        elif lever.lever == VOICE_ORDER and isinstance(lever.value, list):
            orders[lever.target] = [(each.get("source_id"), each.get("player")) for each in lever.value if isinstance(each, dict)]
    return take_off, orders, denied


def _covers(row: CompositionRow, episode: int, part: int | None) -> bool:
    """A row covers the episode when the episode is in its range, and its part is the part of the reference."""
    return row.episode_start <= episode <= row.episode_end and row.part == part


def _chain(rows: Sequence[CompositionRow], preferred: Sequence[tuple[int, str]], priority: Sequence[str],
           players, places: Mapping[int, Sequence[str]]) -> tuple[Candidate, ...]:
    """The playing rows in the order of `core.variant_queue`: the user's order first, then by the rule of voice-overs."""
    playing = [QueueItem(row, place) for place, row in enumerate(rows) if row.state == RowState.PLAYABLE and row.link]
    ordered = ranked(playing, Order(tuple(priority), player_groups=players), VOICE_RANKING,
                     preferred, identity=lambda item: (item.row.source_id, item.row.player))
    chain: dict[str, Candidate] = {}
    for row in (item.row for item in ordered):
        chain.setdefault(row.link, Candidate(row.player, row.voice_label, row.link,
                                             (row.source_id, row.row_key) if row.is_compilation else None, row.source_id,
                                             row.row_key, tuple(places.get(row.source_id, ())), row.video))
    return tuple(chain.values())


def _standing(row: dict[str, Any], order: dict[str, int], key: str) -> tuple[int, int, int, int]:
    """Which of the rows of one voice-over stays when several became it: the one that has its track first, then the one
    whose source stands first in the chain of the voice-over, then the one that has the name of the voice-over already (so
    that nothing is renamed for nothing), then the older one."""
    chain = row["chain"]
    used = chain[row.get("attempt", 0)]["url"] if row.get("attempt", 0) < len(chain) else None
    has_track = row.get("status") in (S.PLACED, S.ALIGNED)
    return (0 if has_track else 1, order.get(used, len(order)), 0 if row["voice_key"] == key else 1, row["id"])


def adopt(dubs: Iterable[PlannedDub], rows: Iterable[dict[str, Any]]) -> dict[tuple[int, int, int, str], str]:
    """The key each planned voice-over is recorded under. The rows of the episode that are that voice-over are the row of
    its own key and every row under another key that holds a link of its chain: the same voice-over called by another name
    (a group was made or renamed, or took the name of a group that was there). One of them stays, whichever name it has —
    by `_standing` — and keeps what it has downloaded; the others are not planned and leave with what they own. A row whose
    key another planned voice-over has is that voice-over."""
    dubs = list(dubs)
    planned = {(each.season, each.episode, each.part, each.voice_key) for each in dubs}
    by_episode: dict[Slot, list[dict[str, Any]]] = {}
    for row in rows:
        by_episode.setdefault((row["season"], row["episode"], row["part"]), []).append(row)
    taken: set[tuple[int, int, int, str]] = set()
    keys: dict[tuple[int, int, int, str], str] = {}
    for dub in dubs:
        slot = (dub.season, dub.episode, dub.part)
        recorded = by_episode.get(slot, ())
        order = {each.url: place for place, each in enumerate(dub.chain)}
        same = min((row for row in recorded
                    if row["voice_key"] == dub.voice_key
                    or ((*slot, row["voice_key"]) not in planned | taken
                        and order.keys() & {each["url"] for each in row["chain"]})),
                   key=lambda row: _standing(row, order, dub.voice_key), default=None)
        key = same["voice_key"] if same is not None else dub.voice_key
        if key != dub.voice_key:
            taken.add((*slot, key))
        keys[(*slot, dub.voice_key)] = key
    return keys


# --- the input of a started work ------------------------------------------------------------------------------------

# Steps in which the work on a voice-over has begun: it holds a download, a file, a job or a track made from one video.
STARTED = frozenset({S.DOWNLOADING, S.READY, S.ALIGNING, S.ALIGNED, S.PLACED, S.REJECTED})


def other_video(chain: Sequence[dict[str, Any]], attempt: int) -> int | None:
    """The next candidate of the chain that is another video than the one at `attempt`: the service judged that video,
    so its links on other pages (the same player, the same video) would be judged the same. None when none is left."""
    judged = chain[attempt]

    def same(each: dict[str, Any]) -> bool:
        return bool(judged.get("video")) and (each.get("player"), each.get("video")) == (judged.get("player"), judged["video"])

    return next((index for index in range(attempt + 1, len(chain)) if not same(chain[index])), None)


def candidate_of(row: dict[str, Any]) -> dict[str, Any]:
    """The candidate of the chain the work of a row is on: the one it began with, the first for a row that has not begun
    (the last one tried for a row that went through them all); empty for a row with no candidate."""
    chain = row.get("chain") or []
    return chain[min(row.get("attempt") or 0, len(chain) - 1)] if chain else {}


@dataclass(frozen=True)
class Videos:
    """The videos the layout has: the keys of its rows and their links. What a started work is compared with.

    A row the configuration took out (`CompositionRow.left_out`) is a video the layout has and gives to no voice-over;
    a row with no number is not counted, as it is no choice of the user."""

    keys: frozenset[tuple[int, str]] = frozenset()
    links: frozenset[str] = frozenset()

    @classmethod
    def of(cls, rows: Iterable[CompositionRow]) -> "Videos":
        known = [row for row in rows if row.decided]
        return cls(frozenset((row.source_id, row.row_key) for row in known),
                   frozenset(row.link for row in known if row.link))


def moved(row: dict[str, Any], chain: Iterable[Candidate], videos: Videos) -> bool:
    """Whether the work of a started row is on a video that the layout now gives to another episode or voice-over.

    A work is valid while its inputs are the inputs of the plan: the reference (told by `ref_uid`) and the video of the
    voice-over (this). The video is the row it was made from, by its key (a chain recorded before keys is told by its
    link). It is not valid when the layout has that video and the plan of this voice-over does not offer it: another
    episode or voice-over took it, or the configuration took it out (a rule, a switched-off player). A video the layout
    does not have at all is no evidence of a change (a page read short, a video VK hid from the listing of its channel): the work
    stands, as it stood before. A row that has not begun has no work: the plan gives it its chain anew."""
    if row.get("status") not in STARTED:
        return False
    active = candidate_of(row)
    if not active:
        return False
    if active.get("row_key") is not None and active.get("source_id") is not None:
        mine = (active["source_id"], active["row_key"])
        return mine in videos.keys and mine not in {(each.source_id, each.row_key) for each in chain}
    link = active.get("url")
    return bool(link) and link in videos.links and link not in {each.url for each in chain}
