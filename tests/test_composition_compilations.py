"""Stage 12: the compilation each episode stands in (calc/layout/compilations.py) and the level of a compilation in
the inheritance of levers (calc/layout/settings.py). Invariant К-1: an episode stands in at most one compilation,
one that covers it, is switched on and is higher than its best single video."""


from core.contracts import (COMPILATION_MEMBER, COMPILATION_TAKE, Absence, CompositionRow, RowState, SettingRecord, compilation_key)
from calc.layout import compilations, settings


def row(source_id, start, end=None, height=1080, player="vk", group="ПВА ШОУ", state=RowState.PLAYABLE, season=1, key=None):
    end = start if end is None else end
    return CompositionRow(series_id=1, source_id=source_id, row_key=key or f"{source_id}:{start}-{end}:{height}",
                          season=season, episode_start=start, episode_end=end, voice_group=group, voice_label=group, player=player,
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


def test_the_users_none_takes_the_episode_out_of_every_compilation():
    rows = singles(1, 4) + [row(1, 1, 4, 2160)]
    outcome = compilations.resolve(rows, [rec(COMPILATION_MEMBER, "", episode=2)], ORDER)
    assert (1, 2) not in outcome.members
    assert outcome.views[0].episodes[1].absence == Absence.OWN and outcome.views[0].episodes[1].own


def test_the_user_may_put_an_episode_only_where_the_compilation_takes_it():
    rows = singles(1, 4) + [row(1, 1, 4, 2160), row(3, 1, 4, 1080, player="cvh")]
    assert compilations.fits(rows, [], 1, 2, key(1, 1, 4), ORDER)
    assert not compilations.fits(rows, [], 1, 2, key(3, 1, 4, player="cvh"), ORDER)
    assert not compilations.fits(rows, [], 1, 9, key(1, 1, 4), ORDER)


def test_a_renamed_group_takes_its_compilation_keys_along_and_a_gone_source_takes_its_levels():
    comp = key(1, 1, 4)
    records = [rec("reference.order", [], compilation=comp), rec(COMPILATION_MEMBER, comp, episode=2)]
    moved = settings.retarget(records, {"ПВА ШОУ": "PVA"}).records
    assert {each.compilation for each in moved} == {key(1, 1, 4, group="PVA"), None}
    assert next(each.value for each in moved if each.lever == COMPILATION_MEMBER) == key(1, 1, 4, group="PVA")
    assert settings.without_source(records, 1) == []


def test_the_levers_of_compilations_are_set_only_on_their_level():
    assert settings.validate(rec(COMPILATION_MEMBER, "", episode=None)) is not None
    assert settings.validate(rec(COMPILATION_TAKE, False)) is not None
    assert settings.validate(rec(COMPILATION_TAKE, False, compilation=key(1, 1, 4))) is None
    assert settings.validate(rec(COMPILATION_TAKE, False, season=2, compilation=key(1, 1, 4))) is not None
    assert settings.validate(rec(COMPILATION_MEMBER, "nonsense", episode=3)) is not None
