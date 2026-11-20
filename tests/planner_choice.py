"""The choice of the planner (calc/plan) for the rows of a layout and the levers of their episodes, read the way the
service reads them (calc/plan/inputs): what the head of a queue a window lists is compared with."""
from calc.plan.inputs import choices_of, online_of
from calc.plan.planner import plan
from calc.plan.tables import Inputs
from calc.voice_names import NO_GROUPS


def goals(rows, settings=(), *, players, sources, groups=NO_GROUPS, order=None):
    online = online_of(rows)
    choices = choices_of(rows, online, [(each.season, each.episode, each.levers) for each in settings], [],
                         players=players, player_groups=groups, sources=sources, voice_order=order or {}, off_episodes=())
    return {goal.slot: goal for goal in plan(Inputs(online, (), choices)).goals}
