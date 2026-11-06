"""Stage 12: the compilation each episode stands in (modules/composition/compilations.py) and the level of a compilation in
the inheritance of levers (modules/composition/settings.py). Invariant К-1: an episode stands in at most one compilation,
one that covers it, is switched on and is higher than its best single video."""
import random

import pytest

from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, Absence, CompositionRow, Level, RowState, SettingRecord,
                            compilation_key)
from modules.composition import compilations, settings


def row(source_id, start, end=None, height=1080, player="vk", group="ПВА ШОУ", state=RowState.PLAYABLE, season=1, key=None):
    end = start if end is None else end
    return CompositionRow(series_id=1, source_id=source_id, row_key=key or f"{source_id}:{start}-{end}:{height}",
                          season=season, episode_start=start, episode_end=end, voice_label=group, player=player,
                          heights=[height], state=state)


def key(source_id, start, end, player="vk", group="ПВА ШОУ", season=1):
    return compilation_key(group, source_id, player, season, start, end)


def rec(lever, value, season=1, episode=None, compilation=None):
    return SettingRecord(series_id=1, season=season, episode=episode, compilation=compilation, lever=lever, value=value)


ORDER = {1: 0, 2: 1, 3: 2}


def members(rows, records=()):
    return {slot: ref.key for slot, ref in compilations.resolve(rows, list(records), ORDER).members.items()}


def singles(first, last, height=1080, source_id=2):
    return [row(source_id, episode, height=height) for episode in range(first, last + 1)]


def test_an_episode_stands_in_the_shortest_compilation_higher_than_its_single_video():
    rows = singles(1, 20) + [row(1, 1, 20, 2160), row(1, 11, 13, 2160)]
    found = members(rows)
    assert found[(1, 12)] == key(1, 11, 13)
    assert found[(1, 1)] == found[(1, 20)] == key(1, 1, 20)


def test_a_compilation_no_higher_than_the_single_video_takes_nothing():
    rows = singles(1, 4, height=1080) + [row(1, 1, 4, 1080)]
    view = compilations.resolve(rows, [], ORDER).views[0]
    assert members(rows) == {}
    assert {each.absence for each in view.episodes} == {Absence.NOT_BETTER}


def test_an_episode_with_its_own_single_video_at_2160_is_not_taken():
    rows = singles(1, 4) + [row(2, 3, height=2160, key="own")] + [row(1, 1, 4, 2160)]
    assert (1, 3) not in members(rows) and (1, 2) in members(rows)


def test_an_episode_without_a_single_video_has_nothing_to_be_cut_by():
    rows = singles(1, 2) + [row(1, 1, 3, 2160)]
    view = compilations.resolve(rows, [], ORDER).views[0]
    assert (1, 3) not in members(rows)
    assert view.episodes[2].absence == Absence.NO_SINGLE


def test_on_equal_cover_the_higher_then_the_earlier_source_wins():
    rows = singles(1, 4, height=720) + [row(2, 1, 4, 1080, player="cvh"), row(1, 1, 4, 2160)]
    assert members(rows)[(1, 1)] == key(1, 1, 4)
    rows = singles(1, 4, height=720) + [row(3, 1, 4, 2160), row(1, 1, 4, 2160, player="vkx")]
    assert members(rows)[(1, 1)] == key(1, 1, 4, player="vkx")


def test_the_users_word_puts_an_episode_into_another_compilation_and_out_of_the_first():
    rows = singles(1, 20) + [row(1, 1, 20, 2160), row(1, 11, 13, 2160)]
    word = rec(COMPILATION_MEMBER, key(1, 1, 20), episode=12)
    outcome = compilations.resolve(rows, [word], ORDER)
    assert outcome.members[(1, 12)].key == key(1, 1, 20)
    short = next(each for each in outcome.views if each.ref.key == key(1, 11, 13))
    twelve = next(each for each in short.episodes if each.episode == 12)
    assert twelve.absence == Absence.ELSEWHERE and twelve.elsewhere == key(1, 1, 20)


def test_the_users_none_takes_the_episode_out_of_every_compilation():
    rows = singles(1, 4) + [row(1, 1, 4, 2160)]
    outcome = compilations.resolve(rows, [rec(COMPILATION_MEMBER, "", episode=2)], ORDER)
    assert (1, 2) not in outcome.members
    assert outcome.views[0].episodes[1].absence == Absence.OWN and outcome.views[0].episodes[1].own


def test_a_word_for_a_compilation_that_no_longer_fits_gives_the_episode_back_to_the_rule():
    rows = singles(1, 4) + [row(1, 1, 4, 2160), row(3, 1, 4, 1080, player="cvh")]
    outcome = compilations.resolve(rows, [rec(COMPILATION_MEMBER, key(3, 1, 4, player="cvh"), episode=2)], ORDER)
    assert outcome.members[(1, 2)].key == key(1, 1, 4)


def test_a_compilation_switched_off_takes_nothing_and_its_episodes_go_to_the_next():
    rows = singles(1, 20) + [row(1, 1, 20, 2160), row(1, 11, 13, 2160)]
    off = rec(COMPILATION_TAKE, False, compilation=key(1, 11, 13))
    outcome = compilations.resolve(rows, [off], ORDER)
    assert outcome.members[(1, 12)].key == key(1, 1, 20)
    short = next(each for each in outcome.views if each.ref.key == key(1, 11, 13))
    assert not short.take and {each.absence for each in short.episodes} == {Absence.OFF}


def test_the_user_may_put_an_episode_only_where_the_compilation_takes_it():
    rows = singles(1, 4) + [row(1, 1, 4, 2160), row(3, 1, 4, 1080, player="cvh")]
    assert compilations.fits(rows, [], 1, 2, key(1, 1, 4), ORDER)
    assert not compilations.fits(rows, [], 1, 2, key(3, 1, 4, player="cvh"), ORDER)
    assert not compilations.fits(rows, [], 1, 9, key(1, 1, 4), ORDER)


def test_the_compilations_of_a_season_are_numbered_by_their_start():
    rows = singles(1, 30) + [row(1, 21, 30, 2160), row(1, 1, 10, 2160), row(1, 11, 20, 2160)]
    views = compilations.resolve(rows, [], ORDER).views
    assert [(each.index, each.ref.start) for each in views] == [(0, 1), (1, 11), (2, 21)]


def test_rows_with_one_key_are_one_compilation_with_the_best_video():
    rows = singles(1, 4) + [row(1, 1, 4, 1440, key="a"), row(1, 1, 4, 2160, key="b")]
    views = compilations.resolve(rows, [], ORDER).views
    assert len(views) == 1 and views[0].ref.row_key == "b" and views[0].ref.height == 2160


@pytest.mark.parametrize("seed", range(40))
def test_every_episode_stands_in_at_most_one_compilation_that_takes_it(seed):
    """К-1 over random layouts, words and switches."""
    rng = random.Random(seed)
    rows = [row(rng.choice([2, 3]), episode, height=rng.choice([480, 720, 1080, 2160]))
            for episode in range(1, 41) if rng.random() < 0.8]
    for _ in range(rng.randint(1, 6)):
        start = rng.randint(1, 35)
        rows.append(row(rng.choice([1, 2, 3]), start, start + rng.randint(1, 15), rng.choice([720, 1080, 1440, 2160]),
                        player=rng.choice(["vk", "cvh"])))
    refs = compilations.compilations(rows)
    records = []
    for ref in refs:
        if rng.random() < 0.2:
            records.append(rec(COMPILATION_TAKE, False, compilation=ref.key))
    for _ in range(rng.randint(0, 5)):
        records.append(rec(COMPILATION_MEMBER, rng.choice([""] + [ref.key for ref in refs]), episode=rng.randint(1, 40)))
    records = settings.canonical({(each.episode, each.compilation, each.lever): each for each in records}.values())
    outcome = compilations.resolve(rows, records, ORDER)
    best = {}
    for each in rows:
        if not each.is_compilation:
            best[each.episode_start] = max(best.get(each.episode_start, 0), each.height)
    off = {each.compilation for each in records if each.lever == COMPILATION_TAKE}
    for (season, episode), ref in outcome.members.items():
        assert ref.start <= episode <= ref.end and ref.key not in off
        assert episode in best and (ref.height or 0) > best[episode]
    marked = {}
    for view in outcome.views:
        for each in view.episodes:
            if each.member:
                assert (view.season, each.episode) not in marked
                marked[(view.season, each.episode)] = view.ref.key
    assert marked == {slot: ref.key for slot, ref in outcome.members.items()}


def test_an_episode_inherits_the_levers_of_its_compilation_between_its_own_and_its_seasons():
    order = [{"group": "ПВА ШОУ", "source_id": 1, "player": "vk"}]
    other = [{"group": "ruchime", "source_id": 2, "player": "vk"}]
    comp = key(1, 1, 4)
    records = [rec("reference.order", other), rec("reference.order", order, compilation=comp)]
    ref = compilations.resolve(singles(1, 4) + [row(1, 1, 4, 2160)], records, ORDER).members[(1, 2)]
    by_episode = {(each.season, each.episode): each for each in
                  settings.episode_settings(records, [(1, 2), (1, 9)], {(1, 2): ref})}
    chosen = next(x for x in by_episode[(1, 2)].levers if x.lever == "reference.order")
    assert (chosen.value, chosen.level) == (order, Level.COMPILATION) and by_episode[(1, 2)].compilation.key == comp
    outside = next(x for x in by_episode[(1, 9)].levers if x.lever == "reference.order")
    assert (outside.value, outside.level) == (other, Level.SEASON)


def test_a_renamed_group_takes_its_compilation_keys_along_and_a_gone_source_takes_its_levels():
    comp = key(1, 1, 4)
    records = [rec("reference.order", [], compilation=comp), rec(COMPILATION_MEMBER, comp, episode=2)]
    moved = settings.retarget(records, {"ПВА ШОУ": "PVA"})
    assert {each.compilation for each in moved} == {key(1, 1, 4, group="PVA"), None}
    assert next(each.value for each in moved if each.lever == COMPILATION_MEMBER) == key(1, 1, 4, group="PVA")
    assert settings.without_source(records, 1) == []


def test_the_levers_of_compilations_are_set_only_on_their_level():
    assert settings.validate(rec(COMPILATION_MEMBER, "", episode=None)) is not None
    assert settings.validate(rec(COMPILATION_TAKE, False)) is not None
    assert settings.validate(rec(COMPILATION_TAKE, False, compilation=key(1, 1, 4))) is None
    assert settings.validate(rec(COMPILATION_TAKE, False, season=2, compilation=key(1, 1, 4))) is not None
    assert settings.validate(rec(COMPILATION_MEMBER, "nonsense", episode=3)) is not None
