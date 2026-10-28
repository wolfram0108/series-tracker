"""Синтетические тесты планировщика (углы, которых нет в живых данных).
Дифф с оригиналом на реальных данных — tests/verify_planner_diff.py (10/10).
"""
from modules.scan.planner import build_plan, plan_with_supersession


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


def test_reversed_range_candidate_excluded_not_crash():
    """Живой кейс VK «... 201-110 серия»: episode_end < episode_start — не
    одиночка (иначе пустой набор эпизодов уронит build_plan) и не
    компиляция; кандидат просто выпадает из плана."""
    garbage = _item("g", 201, end=110)
    normal = _item("n", 1)
    plan = build_plan([garbage, normal])
    assert plan == {"n": "in_plan_single"}


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


def test_vu9_quality_priority_change_reselects():
    """VU9: смена приоритета качества меняет выбор; не выбранное качество →
    `replaced` (файл остаётся до замены, не удаляется)."""
    items = [_item("hi", 1, resolution=1080), _item("lo", 1, resolution=480)]
    assert build_plan(items)["hi"] == "in_plan_single"   # дефолт — большее
    assert build_plan(items, quality_priority=[480, 1080]) == {
        "lo": "in_plan_single", "hi": "replaced"}        # приоритет 480 → 1080 уступает


def test_vu11_ignored_seasons_excluded_from_plan():
    """VU11: эпизоды игнор-сезонов в план НЕ попадают. Раньше ignored_seasons
    только писался в БД, но нигде не применялся — эпизоды планировались и
    качались, пометка «сезон не нужен» была лишь визуальной."""
    items = [_item("s1e1", 1, season=1), _item("s2e1", 1, season=2),
             _item("s2e2", 2, season=2)]
    assert set(build_plan(items)) == {"s1e1", "s2e1", "s2e2"}   # без игнора — все
    plan = build_plan(items, ignored_seasons={2})              # игнор сезона 2
    assert plan == {"s1e1": "in_plan_single"}                  # только сезон 1
    # сезон без номера (None) игнор не затрагивает
    assert build_plan([_item("x", 5, season=None)],
                      ignored_seasons={2}) == {"x": "in_plan_single"}


def test_parts_both_included_in_plan():
    """Обе части одной серии (Part 1 и Part 2) должны входить в план."""
    p1 = {**_item("ep8_p1", 8, season=1, resolution=1080), "part": 1}
    p2 = {**_item("ep8_p2", 8, season=1, resolution=1080), "part": 2}
    plan = build_plan([p1, p2])
    assert plan == {"ep8_p1": "in_plan_single", "ep8_p2": "in_plan_single"}


def test_part_auto_resolved_when_partner_has_part():
    """Если у серии есть элемент с частью 2, а у второго часть не указана (None),
    второй элемент автоматически считается Part 1 и оба входят в план."""
    p_unspecified = _item("ep8_auto", 8, season=1, resolution=1080)
    p2 = {**_item("ep8_p2", 8, season=1, resolution=1080), "part": 2}
    plan = build_plan([p_unspecified, p2])
    assert plan == {"ep8_auto": "in_plan_single", "ep8_p2": "in_plan_single"}
    assert p_unspecified.get("part") == 1


def test_part_quality_competition_per_part():
    """Качество соревнуется отдельно внутри каждой части серии."""
    p1_hi = {**_item("p1_hi", 8, season=1, resolution=1080), "part": 1}
    p1_lo = {**_item("p1_lo", 8, season=1, resolution=720), "part": 1}
    p2_hi = {**_item("p2_hi", 8, season=1, resolution=1080), "part": 2}
    p2_lo = {**_item("p2_lo", 8, season=1, resolution=720), "part": 2}
    plan = build_plan([p1_hi, p1_lo, p2_hi, p2_lo])
    assert plan == {
        "p1_hi": "in_plan_single",
        "p1_lo": "replaced",
        "p2_hi": "in_plan_single",
        "p2_lo": "replaced",
    }



def test_supersession_points_every_loser_at_the_slot_winner():
    items = [_item("r360", 1, resolution=360), _item("r1080", 1, resolution=1080),
             _item("r2160", 1, resolution=2160), _item("other", 2, resolution=360)]
    plan, superseded = plan_with_supersession(items)
    assert plan["r2160"] == "in_plan_single"
    assert superseded == {"r360": "r2160", "r1080": "r2160"}


def test_supersession_follows_user_quality_priority():
    items = [_item("hi", 1, resolution=2160), _item("mid", 1, resolution=1080)]
    _, superseded = plan_with_supersession(items, quality_priority=[1080, 2160])
    assert superseded == {"hi": "mid"}


def test_single_covered_by_compilation_is_not_superseded():
    items = [_item("s1", 1, resolution=480), _item("s2", 2, resolution=480),
             _item("comp", 1, end=2, resolution=1080)]
    plan, superseded = plan_with_supersession(items)
    assert plan["s1"] == plan["s2"] == "replaced"
    assert superseded == {}


def test_supersession_pairs_unspecified_part_with_part_one():
    unspecified = _item("auto", 8, season=1, resolution=480)
    part_one = {**_item("p1", 8, season=1, resolution=1080), "part": 1}
    part_two = {**_item("p2", 8, season=1, resolution=480), "part": 2}
    _, superseded = plan_with_supersession([unspecified, part_one, part_two])
    assert superseded == {"auto": "p1"}


def test_empty_input_has_no_supersession():
    assert plan_with_supersession([]) == ({}, {})
