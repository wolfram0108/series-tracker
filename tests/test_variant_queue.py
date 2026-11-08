"""The order in which the variants of an episode are tried (core/variant_queue.py), and that the choice of the reference
(scan) and the voice-over the planner takes (core/plan) follow it: checked over every order the rows of an episode can
come in."""
import itertools

from core.contracts import Reference, ReferenceMark
from core.variant_queue import (REFERENCE_RANKING, VOICE_RANKING, Candidate, Order, best, by_player, by_resolution,
                                by_source, ranked)
from modules.scan import reference
from planner_choice import goals
from scan_support import row

PRIORITY = ("vk", "cvh", "kodik")
SOURCES = (7, 3, 5)
ORDER = Order(PRIORITY, {source_id: place for place, source_id in enumerate(SOURCES)})

# One episode, six variants: equal resolutions, players and sources, so that every key of the rule decides somewhere.
ROWS = [
    row(1, 3, "a", 1, 1, player="cvh", height=1080, voice="A"),
    row(1, 7, "b", 1, 1, player="kodik", height=1080, voice="B"),
    row(1, 5, "c", 1, 1, player="vk", height=720, voice="A"),
    row(1, 7, "d", 1, 1, player="cvh", height=1080, voice="B"),
    row(1, 5, "e", 1, 1, player="vk", height=2160, voice="A"),
    row(1, 3, "f", 1, 1, player="unknown", height=1080, voice="A"),
]


def keys(candidates):
    return [each.row.row_key for each in candidates]


def by_hand(rows, rule):
    """The rule written out once more, as the plan states it, to compare the code with."""
    place = {source_id: index for index, source_id in enumerate(SOURCES)}
    player = lambda name: PRIORITY.index(name) if name in PRIORITY else len(PRIORITY)
    indexed = list(enumerate(rows))
    if rule == "reference":
        return [r.row_key for _, r in sorted(indexed, key=lambda item: (
            -(item[1].height or 0), player(item[1].player), place.get(item[1].source_id, len(place)), item[0]))]
    return [r.row_key for _, r in sorted(indexed, key=lambda item: (player(item[1].player), item[0]))]


def test_the_rule_of_the_reference_is_resolution_then_player_then_source_then_place():
    for rows in itertools.permutations(ROWS):
        candidates = [Candidate(each, place) for place, each in enumerate(rows)]
        assert keys(ranked(candidates, ORDER, REFERENCE_RANKING)) == by_hand(rows, "reference")
        assert best(candidates, ORDER, REFERENCE_RANKING).row.row_key == by_hand(rows, "reference")[0]


def test_the_rule_of_a_voice_over_is_player_then_place():
    for rows in itertools.permutations(ROWS):
        candidates = [Candidate(each, place) for place, each in enumerate(rows)]
        assert keys(ranked(candidates, ORDER, VOICE_RANKING)) == by_hand(rows, "voice")


def test_the_named_variants_go_first_in_their_order_and_the_rest_by_the_rule():
    identity = lambda candidate: (candidate.row.source_id, candidate.row.player)
    for rows in itertools.permutations(ROWS):
        candidates = [Candidate(each, place) for place, each in enumerate(rows)]
        named = [(7, "kodik"), (3, "nothing"), (3, "cvh")]
        got = keys(ranked(candidates, ORDER, REFERENCE_RANKING, named, identity))
        assert got[:2] == ["b", "a"]
        assert got[2:] == [key for key in by_hand(rows, "reference") if key not in ("a", "b")]


def test_a_variant_named_twice_keeps_its_first_place():
    candidates = [Candidate(each, place) for place, each in enumerate(ROWS)]
    identity = lambda candidate: candidate.row.row_key
    assert keys(ranked(candidates, ORDER, VOICE_RANKING, ["d", "a", "d"], identity))[:2] == ["d", "a"]


def test_the_keys_are_one_list_each_criterion_named_once():
    assert REFERENCE_RANKING == (by_resolution, by_player, by_source)
    assert VOICE_RANKING == (by_player,)


def test_the_choice_of_the_reference_is_the_head_of_the_queue_in_every_order_of_the_rows():
    for rows in itertools.permutations(ROWS):
        chosen = reference.choose(list(rows), (), PRIORITY, SOURCES)
        assert [each.reference.row_key for each in chosen] == [by_hand(rows, "reference")[0]]


def test_the_voice_over_the_planner_takes_is_the_head_of_its_queue_in_every_order_of_the_rows():
    # The reference is a video of the voice-over B, so A is a voice-over; the planner takes its first row by the rule.
    ref_row = row(1, 7, "ref", 1, 1, player="vk", height=4320, voice="B")
    voice_a = [each for each in ROWS if each.voice_label == "A"]
    for rows in itertools.permutations(voice_a):
        goal = goals([ref_row, *rows], players=PRIORITY, sources=SOURCES)[(1, 1)]
        (way,) = [each for each in goal.tracks if each.voice == "A"]
        assert way.dub.row_key == by_hand(rows, "voice")[0]
