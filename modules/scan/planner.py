"""Планировщик покрытия серий — семантика SmartCollector v4.1 («Полнота >
Качество > Одиночные») перенесена 1:1 как чистая функция (решение Р-10).

Алгоритм (подтверждён разбором как осмысленный):
1) полный потенциальный диапазон (сезон, эпизод) по всем кандидатам;
2) базовый план — лучшая одиночка на каждый эпизод (по приоритету
   качества пользователя, затем по разрешению);
3) дыры закрываются жадно недоминируемыми компиляциями (доминирование:
   чужое покрытие дыр строго шире);
4) одиночки, накрытые компиляциями, выбрасываются;
5) апгрейд качества: компиляция вне плана вытесняет одиночки, если она
   не хуже КАЖДОЙ из них и строго лучше хотя бы одной;
6) статусы: in_plan_single / in_plan_compilation / replaced (накрыт
   планом) / redundant (не нужен).

Вход — список кандидатов (словарики media_items), выход — словарь
unique_id -> plan_status. Никакой БД: чистая функция, дифф со старым
кодом — tests/verify_planner_diff.py.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

Episode = tuple[int, int]  # (сезон, эпизод)
SingleSlot = tuple[int, int, int | None]  # (сезон, эпизод, part)


def _episodes(item: dict[str, Any]) -> set[Episode]:
    season = item.get("season")
    if season is None:
        season = 1
    start = item["episode_start"]
    end = item.get("episode_end") or start
    return {(season, ep) for ep in range(start, end + 1)}


def _part(item: dict[str, Any]) -> int | None:
    p = item.get("part")
    if p is None:
        return None
    try:
        return int(p)
    except (ValueError, TypeError):
        return None


def _single_slot(item: dict[str, Any]) -> SingleSlot:
    season = item.get("season")
    if season is None:
        season = 1
    return (season, item["episode_start"], _part(item))


def _is_compilation(item: dict[str, Any]) -> bool:
    return bool(item.get("episode_end")) and \
        item["episode_end"] > item["episode_start"]


def _quality_key(item: dict[str, Any], priority: list[int]) -> tuple:
    """Меньше — лучше: (позиция в пользовательском приоритете, -разрешение)."""
    resolution = item.get("resolution") or 0
    index = priority.index(resolution) if resolution in priority else float("inf")
    return (index, -resolution)


def _cover_gaps(singles: list[dict], compilations: list[dict],
                to_cover: set[Episode]) -> list[dict]:
    """Шаги 2–4: базовые одиночки + жадное покрытие дыр компиляциями."""
    plan_compilations: list[dict] = []
    covered_by_compilations: set[Episode] = set()
    covered_by_singles = {_episodes(s).pop() for s in singles}
    gaps = to_cover - covered_by_singles

    while gaps:
        candidates = [c for c in compilations
                      if not _episodes(c).isdisjoint(gaps)]
        if not candidates:
            break
        non_dominated = []
        for cand in candidates:
            cand_gaps = _episodes(cand) & gaps
            dominated = any(
                cand is not other
                and cand_gaps.issubset(other_gaps := _episodes(other) & gaps)
                and len(other_gaps) > len(cand_gaps)
                for other in candidates)
            if not dominated:
                non_dominated.append(cand)
        pool = non_dominated or candidates
        best = max(pool, key=lambda c: (
            len(_episodes(c) & gaps),
            -len(_episodes(c) & covered_by_singles),
            -(c["episode_end"] - c["episode_start"])))
        plan_compilations.append(best)
        covered_by_compilations |= _episodes(best)
        gaps -= _episodes(best)

    kept_singles = [s for s in singles
                    if _episodes(s).pop() not in covered_by_compilations]
    return plan_compilations + kept_singles


def build_plan(candidates: list[dict[str, Any]],
               quality_priority: list[int] | None = None,
               ignored_seasons: set[int] | None = None) -> dict[str, str]:
    """Кандидаты -> {unique_id: plan_status}."""
    priority = quality_priority or []
    ignored = {int(s) for s in (ignored_seasons or ())}
    # Игнорированные пользователем кандидаты в плане не участвуют: они не
    # должны выигрывать слот эпизода и вытеснять валидную альтернативу.
    # Иначе игнор «битого»/ненужного варианта (напр. 2160p-компиляции,
    # мис-распознанной как одиночка) не освобождает место настоящему файлу,
    # уже лежащему на диске (тот остаётся `replaced` → не усыновляется).
    # Игнор-сезоны (VU11): эпизоды этих сезонов в план не попадают — иначе
    # пометка «сезон не нужен» в UI не влияла бы на загрузку (был лишь
    # визуальный гасёж). Сезон None (не извлечён) не трогаем.
    # Развёрнутый диапазон (episode_end < episode_start) — мусор из
    # неоднозначного заголовка источника (напр. VK: «... 201-110 серия»);
    # это не одиночка (для неё _episodes() даст пустое множество и уронит
    # build_plan) и не компиляция — из планирования исключаем.
    candidates = [c for c in candidates
                  if not c.get("is_ignored_by_user")
                  and c.get("season") not in ignored
                  and not (c.get("episode_end") is not None
                           and c["episode_end"] < c["episode_start"])]
    full_range: set[Episode] = set()
    for item in candidates:
        full_range |= _episodes(item)
    if not full_range:
        return {}

    # Авторазрешение Part 1: если для серии (season, ep) есть элемент
    # с явно указанной частью, то одиночные элементы без части считаются Part 1.
    parts_by_ep: set[Episode] = set()
    for item in candidates:
        if not _is_compilation(item) and _part(item) is not None:
            season = item.get("season") or 1
            parts_by_ep.add((season, item["episode_start"]))

    for item in candidates:
        if not _is_compilation(item) and _part(item) is None:
            season = item.get("season") or 1
            if (season, item["episode_start"]) in parts_by_ep:
                item["part"] = 1

    singles_by_slot: dict[SingleSlot, list[dict]] = defaultdict(list)
    compilations: list[dict] = []
    for item in candidates:
        if _is_compilation(item):
            compilations.append(item)
        else:
            singles_by_slot[_single_slot(item)].append(item)
    # Фикс Г (находка 22, согласован): лучшая одиночка = МИНИМАЛЬНЫЙ ключ
    # качества («меньше = лучше», как и в этапе апгрейда). Оригинал брал
    # max — инверсия: худшее разрешение / последний в списке приоритетов.
    base_singles = [min(group, key=lambda i: _quality_key(i, priority))
                    for group in singles_by_slot.values()]

    plan = _cover_gaps(base_singles, compilations, full_range)

    # Шаг 5: апгрейд качества необязательными компиляциями
    final_plan = list(plan)
    def rebuild_map() -> dict[Episode, list[dict]]:
        m: dict[Episode, list[dict]] = defaultdict(list)
        for i in final_plan:
            if not _is_compilation(i):
                m[_episodes(i).pop()].append(i)
        return m
    plan_map = rebuild_map()

    for comp in compilations:
        if comp in final_plan:
            continue
        comp_q = _quality_key(comp, priority)
        replaceable, valid, strictly_better = [], True, False
        for ep_key in sorted(_episodes(comp)):
            singles_for_ep = plan_map.get(ep_key, [])
            if not singles_for_ep:
                continue
            for single in singles_for_ep:
                single_q = _quality_key(single, priority)
                if comp_q > single_q:      # компиляция хуже этой одиночки
                    valid = False
                    break
                if comp_q < single_q:      # строго лучше
                    strictly_better = True
                replaceable.append(single)
            if not valid:
                break
        if valid and replaceable and strictly_better:
            final_plan = [i for i in final_plan if i not in replaceable]
            final_plan.append(comp)
            plan_map = rebuild_map()

    # Шаг 6: статусы
    plan_ids = {i["unique_id"] for i in final_plan}
    covered: set[Episode] = set()
    for item in final_plan:
        covered |= _episodes(item)

    statuses: dict[str, str] = {}
    for item in candidates:
        uid = item["unique_id"]
        if uid in plan_ids:
            statuses[uid] = ("in_plan_compilation" if _is_compilation(item)
                             else "in_plan_single")
        else:
            statuses[uid] = ("replaced"
                             if not _episodes(item).isdisjoint(covered)
                             else "redundant")
    return statuses
