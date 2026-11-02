"""What the user may do with an object that is not ready: one table (core/contracts/availability.py), the same for every
module that refuses and for the window that draws."""
import pytest

from core.contracts import Action, NotAllowed, allowed, availability, refusal, require, variant_state
from core.contracts import CompositionRow, RowState
from core.reasons import parse

STATES = {
    Action.SYNC: ["idle", "scanning"],
    Action.CANCEL_DOWNLOAD: ["pending", "downloading", "processing", "error"],
    Action.CHOOSE_REFERENCE: ["referable", "playable", "absent"],
    Action.PREFER_VARIANT: ["referable", "playable", "absent"],
}
EXPECTED = {
    (Action.SYNC, "idle"), (Action.CANCEL_DOWNLOAD, "pending"), (Action.CANCEL_DOWNLOAD, "downloading"),
    (Action.CANCEL_DOWNLOAD, "processing"), (Action.CHOOSE_REFERENCE, "referable"), (Action.PREFER_VARIANT, "referable"),
    (Action.PREFER_VARIANT, "playable"),
}


def line(state=RowState.PLAYABLE, start=1, end=1):
    return CompositionRow(series_id=1, source_id=1, row_key=f"{state}{start}{end}", season=1, episode_start=start, episode_end=end,
                          voice_label="V", voice_name="V", player="vk", state=state, link="https://x")


def test_every_action_is_allowed_in_exactly_the_states_the_table_names():
    for action, states in STATES.items():
        for state in states:
            assert allowed(action, state) == ((action, state) in EXPECTED), (action, state)
    assert availability.table() == {str(action): sorted(state for state in STATES[action] if (action, state) in EXPECTED)
                                    for action in Action}


def test_a_refused_action_says_the_action_and_the_state_by_a_code_and_an_allowed_one_says_nothing():
    for action, states in STATES.items():
        for state in states:
            if (action, state) in EXPECTED:
                require(action, state)
            else:
                with pytest.raises(NotAllowed) as caught:
                    require(action, state)
                assert parse(str(caught.value)) == {"code": "not_allowed", "params": {"action": str(action), "state": state}}
                assert str(caught.value) == refusal(action, state)


def test_a_variant_is_referable_when_one_episode_of_it_plays_playable_when_only_compilations_do_and_absent_otherwise():
    assert variant_state([line(), line(RowState.ABSENT)]) == "referable"
    assert variant_state([line(start=1, end=3)]) == "playable"
    assert variant_state([line(start=1, end=3), line()]) == "referable"
    assert variant_state([line(RowState.ABSENT)]) == "absent"
    assert variant_state([]) == "absent"
