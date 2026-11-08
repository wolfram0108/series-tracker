"""Why the video of an episode is the one the plan takes (core/plan/marks.py): the reference the window shows is told by the
plan itself, from the rows of the layout, the levers of the episode, the failures and the episodes switched off."""
from core.contracts import (REFERENCE_ORDER, EffectiveLever, EpisodeSettings, Level, ReferenceMark, RowState)
from core.plan.inputs import choices_of, online_of
from core.plan.marks import references_of
from core.plan.planner import plan
from core.plan.tables import Inputs, RowId
from core.reasons import parse
from core.voice_names import NO_GROUPS
from scan_support import row

PRIORITY = ("vk", "cvh", "kodik")
SOURCES = (1, 2, 3)


def told(rows, settings=(), *, failed=frozenset(), off=()):
    online = online_of(rows)
    choices = choices_of(rows, online, [(each.season, each.episode, each.levers) for each in settings], [],
                         players=PRIORITY, player_groups=NO_GROUPS, sources=SOURCES, voice_order=(), off_episodes=off)
    inputs = Inputs(online, (), choices, frozenset(failed))
    return {(each.season, each.episode): each for each in references_of(inputs, plan(inputs))}


def order(level: Level, *variants):
    return [EpisodeSettings(season=1, episode=1, levers=[EffectiveLever(
        lever=REFERENCE_ORDER, value=[{"group": g, "source_id": s, "player": p} for g, s, p in variants], level=level)])]


def test_the_head_of_the_rule_is_the_reference_by_the_levels_above():
    found = told([row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A"),
                  row(1, 2, "b", 1, 1, player="cvh", height=720, voice="B")])[(1, 1)]
    assert (found.mark, found.source_id, found.row_key, found.player, found.voice_group, found.height) == \
        (ReferenceMark.BY_LEVEL, 1, "a", "vk", "A", 1080)


def test_the_order_of_the_episode_itself_is_its_own():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A"),
            row(1, 2, "b", 1, 1, player="cvh", height=720, voice="B")]
    found = told(rows, order(Level.EPISODE, ("B", 2, "cvh")))[(1, 1)]
    assert (found.mark, found.row_key) == (ReferenceMark.OWN, "b")
    assert told(rows, order(Level.SEASON, ("B", 2, "cvh")))[(1, 1)].mark is ReferenceMark.BY_LEVEL


def test_a_variant_the_user_put_first_that_the_episode_has_not_gives_the_next_one_with_its_reason():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A")]
    found = told(rows, order(Level.SEASON, ("B", 2, "cvh")))[(1, 1)]
    assert (found.mark, found.row_key) == (ReferenceMark.OTHER_PLAYER, "a")
    assert parse(found.reason)["code"] == "reference_other_player"


def test_a_video_whose_download_failed_gives_the_next_one_with_its_reason():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A"),
            row(1, 2, "b", 1, 1, player="cvh", height=720, voice="B")]
    found = told(rows, failed={("download", RowId(1, "a", ""))})[(1, 1)]
    assert (found.mark, found.row_key) == (ReferenceMark.OTHER_PLAYER, "b")
    assert parse(found.reason)["code"] == "reference_download_failed"


def test_an_episode_a_compilation_alone_covers_is_unfit_and_one_with_nothing_is_none():
    found = told([row(1, 4, "comp", 1, 1, 3, player="vk", height=2160, voice="A"),
                  row(1, 1, "gone", 1, 5, player="vk", height=720, voice="A", state=RowState.ABSENT)])
    assert found[(1, 2)].mark is ReferenceMark.UNFIT and parse(found[(1, 2)].reason)["code"] == "reference_unfit"
    assert found[(1, 5)].mark is ReferenceMark.NONE and parse(found[(1, 5)].reason)["code"] == "reference_none"


def test_a_row_whose_player_has_no_video_is_no_reference():
    found = told([row(1, 1, "a", 1, 1, player="vk", height=2160, voice="A", state=RowState.NOT_OBTAINED),
                  row(1, 2, "b", 1, 1, player="cvh", height=720, voice="B")])[(1, 1)]
    assert found.row_key == "b"


def test_an_episode_switched_off_is_off():
    found = told([row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A")], off={(1, 1)})[(1, 1)]
    assert found.mark is ReferenceMark.OFF
