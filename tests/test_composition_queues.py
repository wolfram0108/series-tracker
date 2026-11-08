"""The queues a window lists (modules/composition/queues.py) are the queues the core takes: the head of the queue of the
reference is the reference `scan` chooses, and the head of the queue of a group is the row the planner takes for that
voice-over (core/plan), for every order the rows of the layout come in, with and without the levers of the user."""
import itertools

from core.contracts import (REFERENCE_ALLOW, REFERENCE_ORDER, VOICE_ALLOW, VOICE_ORDER, VOICE_TAKE, EffectiveLever,
                            EpisodeSettings, Level, availability, group_target, variant_target)
from core.variant_queue import Order
from core.voice_names import NO_GROUPS
from modules.composition import queues
from modules.scan import reference
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
}


# Six rows of the episode over three groups: every permutation of them (720) is checked.
SHUFFLED = [EPISODE[i] for i in (0, 2, 4, 5, 6, 7)]


def at_eleven(chosen):
    (found,) = [each for each in chosen if each.reference.episode == 11]
    return found


def build(rows, levers):
    return queues.build(availability.lines_of_level(rows, 4, 11), levers, ORDER, GROUPS, 11)


def test_the_head_of_the_queue_of_the_reference_is_the_reference_scan_chooses_in_every_order_of_the_rows():
    for name, levers in LEVERS.items():
        for rows in itertools.permutations(SHUFFLED):
            shown, _ = build(list(rows), levers)
            head = next(each for each in shown.rows if each.allowed)
            chosen = at_eleven(reference.choose(list(rows), [EpisodeSettings(season=4, episode=11, levers=levers)],
                                                PRIORITY, SOURCES))
            assert (head.group, head.source_id, head.player) == (
                chosen.reference.voice_group, chosen.reference.source_id, chosen.reference.player), name


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


def test_a_queue_lists_what_the_level_has_with_the_values_of_its_levers():
    shown, by_group = build(EPISODE, LEVERS["denied"] + [lever(VOICE_TAKE, False, group_target("FumoDub"), Level.EPISODE)])
    assert [(each.group, each.source_id, each.player, each.height, each.allowed) for each in shown.rows] == [
        ("AniStar", 39, "vk", 2160, False), ("AniStar", 33, "cvh", 1080, True), ("AnimeVost", 33, "cvh", 1080, True),
        ("AniStar", 40, "cvh", 1080, True), ("AniStar", 33, "kodik", 720, True), ("ChinaTown", 33, "kodik", 720, True),
        ("AnimeVost", 40, "kodik", 720, True), ("ChinaTown", 40, "kodik", 720, True), ("AniStar", 40, "animakima", 720, True)]
    assert shown.rows[0].allowed_level == Level.SEASON and shown.order_level == Level.DEFAULT
    assert [queue.group for queue in by_group] == ["AniStar", "ChinaTown", "AnimeVost", "FumoDub"]
    fumo = next(queue for queue in by_group if queue.group == "FumoDub")
    assert (fumo.take, fumo.take_level, [each.episodes for each in fumo.rows]) == (False, Level.EPISODE, ["11"])


def test_a_voice_over_in_no_group_has_no_queue_and_stays_a_variant_of_the_reference():
    """An unknown voice-over is taken into no work, so the window lists no queue of it; its single row may still be the
    reference, as scan chooses it."""
    unknown = row(1, 38, "u-vk", 4, 11, player="vk", height=2160, voice="Неизвестная озвучка 1")
    shown, by_group = build(EPISODE + [unknown], [])
    assert "Неизвестная озвучка 1" not in [queue.group for queue in by_group]
    assert ("Неизвестная озвучка 1", 38, "vk") in [(each.group, each.source_id, each.player) for each in shown.rows]


def test_a_season_queue_names_the_episodes_each_variant_plays_in_and_its_best_height():
    rows = [row(1, 38, f"h{n}", 4, n, player="vk", height=2160, voice="HORIZON") for n in range(1, 11)] + \
           [row(1, 39, f"a{n}", 4, n, player="vk", height=1080 if n < 15 else 2160, voice="AniStar") for n in range(11, 19)]
    shown, _ = queues.build(availability.lines_of_level(rows, 4, None), [], ORDER, GROUPS)
    assert [(each.group, each.height, each.episodes) for each in shown.rows] == [
        ("HORIZON", 2160, "1–10"), ("AniStar", 2160, "11–18")]
