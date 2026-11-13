"""The order in which the variants of an episode are tried (core/variant_queue.py), and that the choice of the reference
and the voice-over the planner takes (core/plan) follow it: checked over every order the rows of an episode can
come in."""
import itertools

from core.contracts import Reference, ReferenceMark
from core.variant_queue import (NO_SAME, REFERENCE_RANKING, VOICE_RANKING, Candidate, Order, Same, best, by_player,
                                by_resolution, by_source, ranked, ranked_singles, standing, variant_rule)
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
    # A voice-over: the resolution decides, the player after it (the owner's rule, stage 10), then the place of the row.
    return [r.row_key for _, r in sorted(indexed, key=lambda item: (-(item[1].height or 0), player(item[1].player), item[0]))]


def test_the_rule_of_the_reference_is_resolution_then_player_then_source_then_place():
    for rows in itertools.permutations(ROWS):
        candidates = [Candidate(each, place) for place, each in enumerate(rows)]
        assert keys(ranked(candidates, ORDER, REFERENCE_RANKING)) == by_hand(rows, "reference")
        assert best(candidates, ORDER, REFERENCE_RANKING).row.row_key == by_hand(rows, "reference")[0]


def test_the_rule_of_a_voice_over_is_resolution_then_player_then_place():
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
    assert VOICE_RANKING == (by_resolution, by_player)


def test_the_choice_of_the_reference_is_the_head_of_the_queue_in_every_order_of_the_rows():
    for rows in itertools.permutations(ROWS):
        goal = goals(list(rows), players=PRIORITY, sources=SOURCES)[(1, 1)]
        assert goal.video.timeline.row_key == by_hand(rows, "reference")[0]


def test_the_voice_over_the_planner_takes_is_the_head_of_its_queue_in_every_order_of_the_rows():
    # The reference is a video of the voice-over B, so A is a voice-over; the planner takes its first row by the rule.
    ref_row = row(1, 7, "ref", 1, 1, player="vk", height=4320, voice="B")
    voice_a = [each for each in ROWS if each.voice_label == "A"]
    for rows in itertools.permutations(voice_a):
        goal = goals([ref_row, *rows], players=PRIORITY, sources=SOURCES)[(1, 1)]
        (way,) = [each for each in goal.tracks if each.voice == "A"]
        assert way.dub.row_key == by_hand(rows, "voice")[0]


# --- Р-99: variants declared equal --------------------------------------------------------------------------------------

def variant(each):
    return (each.voice_label, each.source_id, each.player)


def test_the_queue_of_single_videos_without_groups_is_the_queue_the_rule_makes_for_every_order_of_the_rows_and_the_users_order():
    for preferred in ((), (variant(ROWS[2]),), (variant(ROWS[3]), variant(ROWS[0]))):
        for rows in itertools.permutations(ROWS):
            candidates = [Candidate(each, place) for place, each in enumerate(rows)]
            want = ranked(candidates, ORDER, REFERENCE_RANKING, preferred, lambda each: variant(each.row))
            assert ranked_singles(candidates, ORDER, preferred) == want
            assert ranked_singles(candidates, ORDER, preferred, NO_SAME) == want


def test_the_groups_of_a_value_are_the_lists_of_two_variants_or_more_a_variant_in_the_first_group_that_names_it():
    one, two, three = ("A", 1, "vk"), ("A", 2, "vk"), ("B", 3, "cvh")
    as_item = lambda each: {"group": each[0], "source_id": each[1], "player": each[2]}
    same = Same.of([[as_item(one), as_item(two)], [as_item(two), as_item(three)], [as_item(three)], "no", [{"group": "A"}], 5])
    assert same.groups == (frozenset({one, two}),)
    assert same.members(one) == frozenset({one, two}) and same.members(three) == frozenset({three})
    assert (same.number_of(two), same.number_of(three)) == (1, None)
    assert Same.of(None) == NO_SAME and Same.of([]) == NO_SAME


def test_a_group_of_equals_stands_where_its_best_member_does_resolution_and_the_place_in_the_layout_still_tell_them_apart():
    # Players vk, cvh, kodik; sources 7, 3, 5. The row of the player kodik joins the group of the row of the player vk.
    vk, cvh, kodik = ROWS[4], ROWS[0], ROWS[1]  # 2160p vk (source 5), 1080p cvh (source 3), 1080p kodik (source 7)
    low_vk = ROWS[2]  # 720p vk (source 5)
    candidates = [Candidate(each, place) for place, each in enumerate((cvh, kodik, low_vk, vk))]
    plain = keys(ranked_singles(candidates, ORDER))
    assert plain == ["e", "a", "b", "c"]
    same = Same((frozenset({variant(low_vk), variant(kodik)}),))
    grouped = keys(ranked_singles(candidates, ORDER, (), same))
    # kodik stands at the place of vk (the better player): ahead of cvh at the same resolution; the 720p row is last: the
    # resolution decides inside the place of the group.
    assert grouped == ["e", "b", "a", "c"]


def test_the_place_a_user_order_gives_a_group_is_the_first_of_its_members_in_that_order():
    rows = [ROWS[0], ROWS[1], ROWS[3]]  # a, b, d: all 1080p
    candidates = [Candidate(each, place) for place, each in enumerate(rows)]
    preferred = (variant(ROWS[3]), variant(ROWS[0]))
    assert keys(ranked_singles(candidates, ORDER, preferred)) == ["d", "a", "b"]
    same = Same((frozenset({variant(ROWS[1]), variant(ROWS[3])}),))
    # b joins d, the first of the order: it stands before a, which the order names after d.
    assert keys(ranked_singles(candidates, ORDER, preferred, same)) == ["b", "d", "a"]  # b and d tie: the layout decides


def test_the_standing_of_a_variant_is_the_least_of_its_groups_and_its_own_when_it_stands_alone():
    rule = variant_rule(ORDER)
    named = lambda each: 0 if each == ("A", 5, "vk") else 9
    same = Same((frozenset({("A", 5, "vk"), ("B", 3, "kodik")}),))
    assert standing(("B", 3, "kodik"), same, named, rule) == standing(("A", 5, "vk"), same, named, rule) == (0, 0, 2)
    assert standing(("B", 3, "kodik"), NO_SAME, named, rule) == (9, 2, 1)
