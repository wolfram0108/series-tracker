"""The queues a window lists (calc/layout/queues.py) are the queues the core takes: the head of the queue of the
reference is the reference the planner takes, and the head of the queue of a group is the row the planner takes for that
voice-over (calc/plan), for every order the rows of the layout come in, with and without the levers of the user."""
import itertools

from core.contracts import (REFERENCE_ALLOW, REFERENCE_ORDER, REFERENCE_SAME, VOICE_ALLOW, VOICE_ORDER, EffectiveLever, EpisodeSettings,
                            Level, availability, group_target, variant_target)
from calc.variant_queue import Order
from calc.layout import queues
from planner_choice import goals
from scan_support import row

PRIORITY = ("alloha", "vk", "cvh", "allvideo", "vibix", "kodik")
SOURCES = (33, 38, 39, 40)
ORDER = Order(PRIORITY, {source_id: place for place, source_id in enumerate(SOURCES)})
GROUPS = ["HORIZON", "AniStar", "ChinaTown", "AnimeVost", "FumoDub"]

# Episode 11 of a season of four sources, as a layout has it: the reference and every voice-over have several variants.
EPISODE = [
    row(1, 33, "a-cvh", 4, 11, player="cvh", height=1080, voice="AniStar"),
    row(1, 33, "a-kodik", 4, 11, player="kodik", height=720, voice="AniStar"),
    row(1, 39, "a-vk", 4, 11, player="vk", height=2160, voice="AniStar"),
    row(1, 40, "a-anim", 4, 11, player="animakima", height=720, voice="AniStar"),
    row(1, 40, "a-cvh4", 4, 11, player="cvh", height=1080, voice="AniStar"),
    row(1, 33, "v-cvh", 4, 11, player="cvh", height=1080, voice="AnimeVost"),
    row(1, 40, "v-kodik", 4, 11, player="kodik", height=720, voice="AnimeVost"),
    row(1, 33, "c-kodik", 4, 11, player="kodik", height=720, voice="ChinaTown"),
    row(1, 40, "c-kodik4", 4, 11, player="kodik", height=720, voice="ChinaTown"),
    row(1, 33, "f-cvh", 4, 9, 12, player="cvh", height=720, voice="FumoDub"),  # a compilation: a voice-over, no reference
]


def lever(name, value, target=None, level=Level.SEASON):
    return EffectiveLever(lever=name, target=target, value=value, level=level)


LEVERS = {
    "none": [],
    "order": [lever(REFERENCE_ORDER, [{"group": "AnimeVost", "source_id": 40, "player": "kodik"},
                                      {"group": "AniStar", "source_id": 33, "player": "kodik"}]),
              lever(VOICE_ORDER, [{"source_id": 40, "player": "kodik"}], group_target("AnimeVost"))],
    "denied": [lever(REFERENCE_ALLOW, False, variant_target("AniStar", 39, "vk")),
               lever(VOICE_ALLOW, False, variant_target("ChinaTown", 33, "kodik"))],
    # Variants declared equal (Р-99): they stand where the best of them does, and the queue still is the planner's.
    "same": [lever(REFERENCE_SAME, [[{"group": "AniStar", "source_id": 40, "player": "cvh"},
                                     {"group": "AnimeVost", "source_id": 33, "player": "cvh"},
                                     {"group": "ChinaTown", "source_id": 33, "player": "kodik"}]])],
    "same and order": [lever(REFERENCE_ORDER, [{"group": "ChinaTown", "source_id": 40, "player": "kodik"},
                                               {"group": "AnimeVost", "source_id": 33, "player": "cvh"}]),
                       lever(REFERENCE_SAME, [[{"group": "AnimeVost", "source_id": 33, "player": "cvh"},
                                               {"group": "ChinaTown", "source_id": 33, "player": "kodik"},
                                               {"group": "AniStar", "source_id": 33, "player": "kodik"}]])],
}


# Six rows of the episode over three groups: every permutation of them (720) is checked.
SHUFFLED = [EPISODE[i] for i in (0, 2, 4, 5, 6, 7)]


def build(rows, levers):
    return queues.build(availability.lines_of_level(rows, 4, 11), levers, ORDER, GROUPS, 11)


def test_the_head_of_the_queue_of_a_group_is_the_voice_over_the_planner_takes_in_every_order_of_the_rows():
    for name, levers in LEVERS.items():
        for rows in itertools.permutations(SHUFFLED):
            rows = list(rows)
            settings = [EpisodeSettings(season=4, episode=11, levers=levers)]
            goal = goals(rows, settings, players=PRIORITY, sources=SOURCES)[(4, 11)]
            _, by_group = build(rows, levers)
            shown = {queue.group: [(each.source_id, each.player) for each in queue.rows if each.allowed] for queue in by_group}
            for way in goal.tracks:
                assert shown[way.voice][0] == (way.dub.source_id, way.row.player), (name, way.voice)
