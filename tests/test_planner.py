"""Синтетические тесты планировщика (углы, которых нет в живых данных).
Дифф с оригиналом на реальных данных — tests/verify_planner_diff.py (10/10).
"""
from modules.scan.planner import build_plan


def _item(uid, start, end=None, season=None, resolution=None):
    return {"unique_id": uid, "episode_start": start, "episode_end": end,
            "season": season, "resolution": resolution}


def test_singles_only():
    plan = build_plan([_item("a", 1), _item("b", 2)])
    assert plan == {"a": "in_plan_single", "b": "in_plan_single"}


def test_best_single_by_resolution():
    plan = build_plan([_item("lo", 1, resolution=480),
                       _item("hi", 1, resolution=1080)])
    assert plan == {"hi": "in_plan_single", "lo": "replaced"}


def test_ignored_candidate_excluded_from_plan():
    """Игнорированный кандидат не участвует в плане: он не выигрывает слот
    и не вытесняет валидную альтернативу (живой кейс «Сильнейший городской
    мастер» — 2160p-«ep1», мис-распознанный из компиляции и помеченный
    игнором, не должен держать настоящий 1080p в replaced)."""
    hi = {**_item("hi", 1, resolution=2160), "is_ignored_by_user": 1}
    lo = {**_item("lo", 1, resolution=1080), "is_ignored_by_user": 0}
    plan = build_plan([hi, lo])
    # игнорированный 2160p не в плане; настоящий 1080p — победитель → усыновится
    assert plan == {"lo": "in_plan_single"}


def test_all_candidates_ignored_yields_empty_plan():
    ig = {**_item("x", 1, resolution=1080), "is_ignored_by_user": 1}
    assert build_plan([ig]) == {}


def test_user_priority_beats_resolution():
    # пользователь предпочитает 480 (например, меньше места)
    plan = build_plan([_item("lo", 1, resolution=480),
                       _item("hi", 1, resolution=1080)],
                      quality_priority=[480, 1080])
    assert plan == {"lo": "in_plan_single", "hi": "replaced"}


def test_compilation_closes_gap_and_swallows_singles():
    plan = build_plan([_item("s1", 1), _item("s2", 2),
                       _item("comp", 1, 4)])  # серии 3-4 есть только в комп.
    assert plan == {"comp": "in_plan_compilation",
                    "s1": "replaced", "s2": "replaced"}


def test_dominated_compilation_not_chosen():
    plan = build_plan([_item("small", 1, 2), _item("big", 1, 4)])
    assert plan["big"] == "in_plan_compilation"
    assert plan["small"] == "replaced"


def test_quality_upgrade_by_compilation():
    # одиночки 480p полностью покрывают, но компиляция 1080p строго лучше
    plan = build_plan([_item("s1", 1, resolution=480),
                       _item("s2", 2, resolution=480),
                       _item("comp", 1, 2, resolution=1080)])
    assert plan == {"comp": "in_plan_compilation",
                    "s1": "replaced", "s2": "replaced"}


def test_no_upgrade_if_worse_than_any_single():
    # компиляция хуже одной из одиночек — апгрейд запрещён;
    # её эпизоды покрыты планом, поэтому статус 'replaced' (семантика
    # оригинала: 'redundant' — только для непокрытых планом)
    plan = build_plan([_item("s1", 1, resolution=1080),
                       _item("s2", 2, resolution=480),
                       _item("comp", 1, 2, resolution=720)])
    assert plan["s1"] == "in_plan_single"
    assert plan["comp"] == "replaced"


def test_seasons_are_separate_dimensions():
    # серия 1 второго сезона ≠ серия 1 первого
    plan = build_plan([_item("s1e1", 1, season=1),
                       _item("s2e1", 1, season=2)])
    assert plan == {"s1e1": "in_plan_single", "s2e1": "in_plan_single"}


def test_empty_candidates():
    assert build_plan([]) == {}
