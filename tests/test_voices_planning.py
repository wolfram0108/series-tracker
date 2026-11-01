"""The plan of voice-overs: which voices an episode gets and where each comes from (M-15, M-16, M-17, T-17)."""
from core.contracts import (CompositionRow, EffectiveLever, EpisodeSettings, Level, Reference, ReferenceMark,
                            RowState, group_target)
from modules.voices.identity import NO_GROUPS, Groups
from modules.voices.planning import plan, rank
from modules.voices.settings import DEFAULT_PRIORITY

PRIORITY = ("cvh", "kodik")


def row(source_id, player, voice, episode, *, end=None, season=1, part=None, state=RowState.PLAYABLE, link=None,
        excluded=False):
    key = f"{player}:{voice}:{episode}" + (f"-{end}" if end else "") + (f":p{part}" if part else "")
    return CompositionRow(
        series_id=7, source_id=source_id, row_key=key, season=season, episode_start=episode,
        episode_end=end or episode, part=part, voice_label=voice, player=player, state=state, excluded=excluded,
        link=f"https://site/{key}" if link is None else link)


def reference_of(rows, voice, episode, *, season=1, mark=ReferenceMark.BY_LEVEL):
    """The reference is the single row of that voice at that episode."""
    found = next(each for each in rows if each.voice_label == voice and each.episode_start == episode
                 and each.season == season and not each.is_compilation)
    return Reference(season=season, episode=episode, mark=mark, source_id=found.source_id, row_key=found.row_key,
                     player=found.player)


def planned(rows, references, settings=(), priority=PRIORITY, players=NO_GROUPS):
    return plan(rows, references, settings, priority, players)


def chains(result):
    return {(dub.season, dub.episode, dub.voice_key): [(each.player, each.url.rsplit("/", 1)[1]) for each in dub.chain]
            for dub in result.dubs}


def lever(name, target, value):
    return EffectiveLever(lever=name, target=group_target(target), value=value, level=Level.SERIES)


def test_every_voice_but_the_one_in_the_reference_is_planned_for_an_episode_with_a_reference():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1), row(1, "kodik", "B", 1)]
    result = planned(rows, [reference_of(rows, "Ref", 1)])
    assert sorted(chains(result)) == [(1, 1, "A"), (1, 1, "B")]
    assert result.with_reference == {(1, 1)} and result.undecided == frozenset()


def test_the_voice_of_the_reference_is_planned_for_no_episode_it_is_the_reference_of():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "Ref", 1), row(1, "cvh", "Ref", 2), row(1, "kodik", "A", 2),
            row(1, "kodik", "A", 1), row(1, "kodik", "Ref", 2)]
    references = [reference_of(rows, "Ref", 1), reference_of(rows, "A", 2)]
    # Episode 2's reference is the voice A: Ref is planned there, A is not.
    assert sorted(chains(planned(rows, references))) == [(1, 1, "A"), (1, 2, "Ref")]


def test_a_voice_the_episode_has_no_row_for_is_planned_with_an_empty_chain_and_says_so():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 2), row(1, "kodik", "A", 1)]
    references = [reference_of(rows, "Ref", 1), reference_of(rows, "Ref", 2)]
    assert chains(planned(rows, references)) == {(1, 1, "A"): [("kodik", "kodik:A:1")], (1, 2, "A"): []}


def test_the_player_higher_in_the_order_of_the_settings_comes_first_then_the_order_of_sources():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1), row(2, "cvh", "A", 1), row(3, "vibix", "A", 1),
            row(4, "kodik", "A", 1, link="https://site/other-kodik")]
    chain = chains(planned(rows, [reference_of(rows, "Ref", 1)]))[(1, 1, "A")]
    # The unknown player is last; the two kodik rows keep the order of their sources.
    assert [link for _, link in chain] == ["cvh:A:1", "kodik:A:1", "other-kodik", "vibix:A:1"]


def test_a_player_that_is_not_playing_is_not_a_candidate_and_the_next_player_is_tried():
    """M-16: the main player cannot play, so the next of the order is the main candidate; none plays: no candidate."""
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "A", 1, state=RowState.ABSENT), row(1, "kodik", "A", 1),
            row(1, "cvh", "B", 1, state=RowState.NOT_OBTAINED), row(1, "kodik", "B", 1, link="")]
    result = chains(planned(rows, [reference_of(rows, "Ref", 1)]))
    assert result[(1, 1, "A")] == [("kodik", "kodik:A:1")] and result[(1, 1, "B")] == []


def test_the_player_of_the_lever_goes_first_when_it_has_a_row_and_is_ignored_when_it_has_none():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "A", 1), row(2, "kodik", "A", 1)]
    references = [reference_of(rows, "Ref", 1)]
    pick = EpisodeSettings(season=1, episode=1, levers=[lever("voice.player", "A", {"source_id": 2, "player": "kodik"})])
    assert [each[0] for each in chains(planned(rows, references, [pick]))[(1, 1, "A")]] == ["kodik", "cvh"]
    none = EpisodeSettings(season=1, episode=1, levers=[lever("voice.player", "A", {"source_id": 9, "player": "vk"})])
    assert [each[0] for each in chains(planned(rows, references, [none]))[(1, 1, "A")]] == ["cvh", "kodik"]
    auto = EpisodeSettings(season=1, episode=1, levers=[lever("voice.player", "A", None)])
    assert [each[0] for each in chains(planned(rows, references, [auto]))[(1, 1, "A")]] == ["cvh", "kodik"]


def test_a_voice_the_lever_turns_off_is_not_planned_for_that_episode_alone():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 2), row(1, "kodik", "A", 1), row(1, "kodik", "A", 2),
            row(1, "kodik", "B", 1), row(1, "kodik", "B", 2)]
    references = [reference_of(rows, "Ref", 1), reference_of(rows, "Ref", 2)]
    off = EpisodeSettings(season=1, episode=1, levers=[lever("voice.take", "A", False), lever("voice.take", "B", True)])
    assert sorted(chains(planned(rows, references, [off]))) == [(1, 1, "B"), (1, 2, "A"), (1, 2, "B")]


def test_a_reference_that_is_none_or_unfit_or_not_given_plans_no_voice_over_of_its_episode():
    rows = [row(1, "cvh", "Ref", episode) for episode in (1, 2, 3, 4)] + [
        row(1, "kodik", "A", episode) for episode in (1, 2, 3, 4)]
    references = [reference_of(rows, "Ref", 1),
                  Reference(season=1, episode=2, mark=ReferenceMark.NONE),
                  Reference(season=1, episode=3, mark=ReferenceMark.UNFIT)]
    result = planned(rows, references)
    assert sorted(chains(result)) == [(1, 1, "A")] and result.with_reference == {(1, 1)}


def test_every_mark_that_has_a_reference_plans_the_voice_overs():
    rows = [row(1, "cvh", "Ref", episode) for episode in (1, 2, 3)] + [row(1, "kodik", "A", episode) for episode in (1, 2, 3)]
    references = [reference_of(rows, "Ref", 1, mark=ReferenceMark.BY_LEVEL),
                  reference_of(rows, "Ref", 2, mark=ReferenceMark.OWN),
                  reference_of(rows, "Ref", 3, mark=ReferenceMark.OTHER_PLAYER)]
    assert sorted(chains(planned(rows, references))) == [(1, 1, "A"), (1, 2, "A"), (1, 3, "A")]


def test_a_reference_whose_row_the_layout_does_not_have_leaves_its_episode_undecided():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1)]
    gone = Reference(season=1, episode=1, mark=ReferenceMark.BY_LEVEL, source_id=1, row_key="not-in-the-layout")
    result = planned(rows, [gone])
    assert result.dubs == () and result.undecided == {(1, 1)} and result.with_reference == frozenset()


def test_the_part_of_a_voice_row_must_be_the_part_of_the_reference_and_none_matches_none():
    rows = [row(1, "cvh", "Ref", 1, part=2), row(1, "kodik", "A", 1, part=1), row(1, "kodik", "A", 1, part=2),
            row(1, "kodik", "B", 1), row(1, "kodik", "C", 1, part=2)]
    chain = chains(planned(rows, [reference_of(rows, "Ref", 1)]))
    assert chain[(1, 1, "A")] == [("kodik", "kodik:A:1:p2")] and chain[(1, 1, "C")] == [("kodik", "kodik:C:1:p2")]
    assert chain[(1, 1, "B")] == []  # a whole episode cannot stand for the second part
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1), row(1, "kodik", "A", 1, part=1)]
    assert chains(planned(rows, [reference_of(rows, "Ref", 1)]))[(1, 1, "A")] == [("kodik", "kodik:A:1")]


def test_only_the_rows_of_the_season_of_the_reference_cover_it():
    rows = [row(1, "cvh", "Ref", 1, season=2), row(1, "kodik", "A", 1, season=1), row(1, "kodik", "A", 1, season=2)]
    references = [reference_of(rows, "Ref", 1, season=2)]
    assert chains(planned(rows, references)) == {(2, 1, "A"): [("kodik", "kodik:A:1")]}


def test_voices_the_layout_could_not_group_are_voices_of_their_own():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "Неизвестная озвучка 1", 1),
            row(1, "vibix", "Неизвестная озвучка 2", 1), row(2, "kodik", "Group", 1)]
    result = chains(planned(rows, [reference_of(rows, "Ref", 1)]))
    assert sorted(result) == [(1, 1, "Group"), (1, 1, "Неизвестная озвучка 1"), (1, 1, "Неизвестная озвучка 2")]
    # An unknown voice is addressed by the lever through its label as a group is through its name.
    off = EpisodeSettings(season=1, episode=1, levers=[lever("voice.take", "Неизвестная озвучка 2", False)])
    assert sorted(chains(planned(rows, [reference_of(rows, "Ref", 1)], [off]))) == [
        (1, 1, "Group"), (1, 1, "Неизвестная озвучка 1")]


def test_a_compilation_is_not_taken_noted_and_does_not_make_the_voice_fail():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 2), row(1, "cvh", "Ref", 3),
            row(1, "kodik", "A", 1, end=3), row(1, "kodik", "B", 1), row(1, "kodik", "B", 2, end=3)]
    references = [reference_of(rows, "Ref", number) for number in (1, 2, 3)]
    result = planned(rows, references)
    # A has only the compilation: no voice-over at all for now. B has episode 1 alone and 2-3 in a compilation.
    assert sorted(chains(result)) == [(1, 1, "B")]
    assert {(each.voice_label, each.episode) for each in result.compilations} == {
        ("A", 1), ("A", 2), ("A", 3), ("B", 2), ("B", 3)}


def test_a_single_row_is_taken_where_a_compilation_covers_the_episode_too():
    rows = [row(1, "cvh", "Ref", 2), row(1, "kodik", "A", 2), row(2, "vibix", "A", 1, end=3)]
    result = planned(rows, [reference_of(rows, "Ref", 2)])
    assert chains(result) == {(1, 2, "A"): [("kodik", "kodik:A:2")]} and len(result.compilations) == 1


def test_a_reference_is_never_a_compilation_row_of_the_plan_but_its_row_is_found_by_its_key():
    rows = [row(1, "cvh", "Ref", 1, end=2), row(1, "kodik", "A", 1)]
    pointed = Reference(season=1, episode=1, mark=ReferenceMark.BY_LEVEL, source_id=1, row_key=rows[0].row_key)
    # The plan does not judge the reference: that is `scan`'s (a compilation is `unfit` there); it follows the mark.
    assert sorted(chains(planned(rows, [pointed]))) == [(1, 1, "A")]


def test_rows_the_layout_excluded_or_gave_no_number_make_no_voice():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1, excluded=True)]
    assert planned(rows, [reference_of(rows, "Ref", 1)]).dubs == ()


def test_nothing_to_plan_is_an_empty_plan():
    assert planned([], []).dubs == () and planned([row(1, "cvh", "Ref", 1)], []).with_reference == frozenset()


def test_the_same_link_is_offered_once():
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1, link="https://site/same"),
            row(2, "kodik", "A", 1, link="https://site/same")]
    assert chains(planned(rows, [reference_of(rows, "Ref", 1)]))[(1, 1, "A")] == [("kodik", "same")]


def test_players_the_user_called_the_same_take_one_place_in_the_order_and_an_unknown_player_comes_last():
    assert rank("vk", ["alloha", "cvh", "kodik"], NO_GROUPS) == 3
    players = Groups.of({"cvh": ["vk"]})
    assert rank("vk", ["alloha", "cvh", "kodik"], players) == 1 == rank("CVH", ["alloha", "cvh", "kodik"], players)
    assert rank("anything", DEFAULT_PRIORITY, NO_GROUPS) == len(DEFAULT_PRIORITY)
    rows = [row(1, "cvh", "Ref", 1), row(1, "kodik", "A", 1), row(1, "vk", "A", 1)]
    # Kodik is first in this order, and vk is called the same as kodik: they keep the order of the sources.
    chain = chains(planned(rows, [reference_of(rows, "Ref", 1)], priority=("kodik", "cvh"),
                           players=Groups.of({"kodik": ["vk"]})))[(1, 1, "A")]
    assert [each[0] for each in chain] == ["kodik", "vk"]
