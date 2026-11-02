"""A video that covers several episodes and is labelled by the last one is found from the gap in the numbering together
with a length that is a whole number of episodes (modules/sources/sites/compilations.py)."""
import itertools

import pytest

from core.contracts import RowState, RowVoice, TableRow
from modules.sources.sites.compilations import TOLERANCE, covers, label_compilations

UNIT = 437.0


def row(number, length, *, voice="AniMy", state=RowState.PLAYABLE, group="1 сезон", episode=None, key=None):
    return TableRow(key=key or f"cvh:{voice}:{number}", player="cvh", group=group, text=f"{number} серия",
                    voice=RowVoice(name=voice, kind="Многоголосый"), state=state, duration_s=length,
                    site_fields={"episode": str(number if episode is None else episode)})


def singles(first, last, unit=UNIT, voice="AniMy"):
    return [row(number, unit, voice=voice) for number in range(first, last + 1)]


def labelled(rows):
    return {each.key: each.text for each in label_compilations(rows) if "covers" in each.site_fields}


def test_a_video_of_two_episodes_named_by_the_last_has_a_gap_before_it_and_twice_the_length():
    rows = [*singles(100, 108), row(110, 855), row(112, 853), *singles(113, 116)]
    assert labelled(rows) == {"cvh:AniMy:110": "109-110 серия", "cvh:AniMy:112": "111-112 серия"}


def test_a_video_of_four_episodes_covers_the_three_numbers_missing_before_it():
    rows = [*singles(120, 127), row(131, 1700), *singles(132, 134)]
    assert labelled(rows) == {"cvh:AniMy:131": "128-131 серия"}


def test_a_gap_alone_is_an_episode_the_studio_did_not_publish():
    rows = [*singles(220, 226), *singles(234, 238)]
    assert labelled(rows) == {}


def test_a_long_episode_without_a_gap_is_left_as_listed():
    rows = [*singles(260, 269), row(270, 837), *singles(271, 275)]  # the table does not guess
    assert labelled(rows) == {}


def test_the_length_must_fit_the_gap():
    rows = [*singles(250, 256), row(258, 1245), *singles(259, 263)]  # three episodes of length, one number missing
    assert labelled(rows) == {}
    rows = [*singles(250, 256), row(258, 700), *singles(259, 263)]  # one number missing, not two episodes long
    assert labelled(rows) == {}


def test_the_tolerance_is_a_fraction_of_an_episode():
    inside = [*singles(100, 106), row(108, 2 * UNIT * (1 + TOLERANCE / 2)), *singles(109, 112)]
    outside = [*singles(100, 106), row(108, 2 * UNIT * (1 + 2 * TOLERANCE)), *singles(109, 112)]
    assert labelled(inside) and not labelled(outside)


def test_the_unit_is_read_from_the_neighbours_because_the_cut_changes_along_the_series():
    early = singles(1, 40, unit=770.0)  # the early episodes are longer, and none of them is a compilation
    late = [*singles(41, 100), row(102, 2 * UNIT), *singles(103, 106)]
    result = labelled(early + late)
    assert result == {"cvh:AniMy:102": "101-102 серия"}


def test_a_stretch_where_every_video_is_a_compilation_tells_no_unit_and_claims_nothing():
    rows = [row(number, 855) for number in range(110, 140, 2)]
    assert labelled(rows) == {}


def test_too_few_lengths_near_a_video_tell_no_unit():
    assert labelled([row(5, UNIT), row(7, 2 * UNIT)]) == {}


def test_voices_do_not_borrow_each_others_numbers_or_lengths():
    rows = [*singles(100, 108), row(110, 855), *singles(100, 112, unit=1123.0, voice="AniStar")]
    assert labelled(rows) == {"cvh:AniMy:110": "109-110 серия"}  # the other studio has 109 and 110 in single episodes


def test_a_row_that_is_not_playable_or_has_no_length_or_whole_number_is_left_alone():
    rows = [*singles(100, 108), row(110, 855, state=RowState.ABSENT), row(112, None), row(114, 855, episode="114.5"),
            row(116, 855, episode="")]
    assert labelled(rows) == {}


def test_a_number_that_is_listed_but_not_playable_still_counts_as_listed():
    rows = [*singles(100, 108), row(109, None, state=RowState.NOT_OBTAINED), row(110, 855), *singles(111, 114)]
    assert labelled(rows) == {}  # the studio lists 109: nothing is missing before 110


def test_the_listed_text_is_kept_and_the_range_is_recorded():
    rows = [*singles(100, 108), row(110, 855), *singles(111, 114)]
    each = next(row_ for row_ in label_compilations(rows) if row_.key == "cvh:AniMy:110")
    assert (each.text, each.site_fields["listed_as"], each.site_fields["covers"]) == ("109-110 серия", "110 серия", [109, 110])
    assert each.duration_s == 855


def test_the_input_rows_are_not_changed():
    rows = [*singles(100, 108), row(110, 855), *singles(111, 114)]
    before = [each.model_dump() for each in rows]
    label_compilations(rows)
    assert [each.model_dump() for each in rows] == before


NOISE = (0.97, 1.0, 1.02, 0.99, 1.03)


@pytest.mark.parametrize("pattern", itertools.product("SXC", repeat=7))
def test_over_every_pattern_of_published_skipped_and_joined_episodes_exactly_the_joined_ones_are_found(pattern):
    """S: an episode published on its own; X: a number nobody published; C: a video that stands for the X numbers before
    it and itself (a C with no X before it is a published episode)."""
    rows, truth, number, noise = list(singles(1, 6)), {}, 7, itertools.cycle(NOISE)
    skipped = 0
    for each in pattern:
        if each == "X":
            skipped += 1
        elif each == "S" or (each == "C" and not skipped):
            rows.append(row(number, UNIT * next(noise)))
            skipped = 0
        else:
            rows.append(row(number, (skipped + 1) * UNIT * next(noise)))
            truth[f"cvh:AniMy:{number}"] = f"{number - skipped}-{number} серия"
            skipped = 0
        number += 1
    assert labelled(rows) == truth
