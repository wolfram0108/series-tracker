"""Why the video of an episode is the one the plan takes (calc/plan/marks.py): the reference the window shows is told by the
plan itself, from the rows of the layout, the levers of the episode, the failures and the episodes switched off."""
from core.contracts import (REFERENCE_ORDER, EffectiveLever, EpisodeSettings, Level, ReferenceMark)
from calc.plan.inputs import choices_of, online_of
from calc.plan.marks import references_of
from calc.plan.planner import plan
from calc.plan.tables import Inputs
from core.reasons import parse
from calc.voice_names import NO_GROUPS
from scan_support import row

PRIORITY = ("vk", "cvh", "kodik")
SOURCES = (1, 2, 3)


def told(rows, settings=(), *, failed=frozenset(), off=()):
    online = online_of(rows)
    choices = choices_of(rows, online, [(each.season, each.episode, each.levers) for each in settings], [],
                         players=PRIORITY, player_groups=NO_GROUPS, sources=SOURCES, voice_order={}, off_episodes=off)
    inputs = Inputs(online, (), choices, frozenset(failed))
    return {(each.season, each.episode): each for each in references_of(inputs, plan(inputs))}


def order(level: Level, *variants, lever: str = REFERENCE_ORDER):
    return [EpisodeSettings(season=1, episode=1, levers=[EffectiveLever(
        lever=lever, value=[{"group": g, "source_id": s, "player": p} for g, s, p in variants], level=level)])]


def test_a_variant_the_user_put_first_that_the_episode_has_not_gives_the_next_one_with_its_reason():
    rows = [row(1, 1, "a", 1, 1, player="vk", height=1080, voice="A")]
    found = told(rows, order(Level.SEASON, ("B", 2, "cvh")))[(1, 1)]
    assert (found.mark, found.row_key) == (ReferenceMark.OTHER_PLAYER, "a")
    assert parse(found.reason)["code"] == "reference_other_player"
