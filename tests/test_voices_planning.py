"""The plan of voice-overs: which voices an episode gets and where each comes from (M-15, M-16, M-17, T-17)."""
from core.contracts import (CompositionRow, EffectiveLever, EpisodeSettings, Level, Reference, ReferenceMark,
                            RowState, group_target, variant_target)
from core.voice_names import NO_GROUPS, Groups
from modules.voices.planning import STARTED, Candidate, PlannedDub, Videos, adopt, moved, plan, rank
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
    assert result.with_reference == {(1, 1, 0)} and result.undecided == frozenset()


def test_the_parts_of_one_episode_are_planned_apart_each_for_the_reference_of_its_own_part():
    rows = [row(1, "cvh", "Ref", 1, part=1), row(1, "cvh", "Ref", 1, part=2),
            row(1, "kodik", "A", 1, part=1), row(1, "kodik", "A", 1, part=2)]
    refs = [Reference(season=1, episode=1, part=part, mark=ReferenceMark.BY_LEVEL, source_id=1,
                      row_key=f"cvh:Ref:1:p{part}", player="cvh") for part in (1, 2)]
    result = planned(rows, refs)
    assert [(dub.part, dub.voice_key, dub.chain[0].url.rsplit("/", 1)[1]) for dub in result.dubs] == [
        (1, "A", "kodik:A:1:p1"), (2, "A", "kodik:A:1:p2")]
    assert result.with_reference == {(1, 1, 1), (1, 1, 2)}


def test_a_variant_the_user_left_out_is_not_a_candidate_of_its_voice_and_the_others_are():
    rows = [row(1, "cvh", "Ref", 1), row(1, "vk", "A", 1), row(2, "cvh", "A", 1), row(3, "kodik", "A", 1)]
    leave_out = lambda source_id, player, voice="A": EpisodeSettings(season=1, episode=1, levers=[  # noqa: E731
        EffectiveLever(lever="variant.allow", target=variant_target(voice, source_id, player), value=False,
                       level=Level.SERIES)])
    ref = reference_of(rows, "Ref", 1)
    assert [each[0] for each in chains(planned(rows, [ref]))[(1, 1, "A")]] == ["cvh", "kodik", "vk"]
    assert [each[0] for each in chains(planned(rows, [ref], [leave_out(2, "cvh")]))[(1, 1, "A")]] == ["kodik", "vk"]
    # A variant of another voice-over is not this one's.
    assert [each[0] for each in chains(planned(rows, [ref], [leave_out(2, "cvh", "B")]))[(1, 1, "A")]] == [
        "cvh", "kodik", "vk"]


def test_a_voice_over_whose_variants_are_all_left_out_is_planned_with_no_source():
    rows = [row(1, "cvh", "Ref", 1), row(1, "vk", "A", 1)]
    settings = [EpisodeSettings(season=1, episode=1, levers=[EffectiveLever(
        lever="variant.allow", target=variant_target("A", 1, "vk"), value=False, level=Level.EPISODE)])]
    assert chains(planned(rows, [reference_of(rows, "Ref", 1)], settings))[(1, 1, "A")] == []


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
    assert sorted(chains(result)) == [(1, 1, "A")] and result.with_reference == {(1, 1, 0)}


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
    assert result.dubs == () and result.undecided == {(1, 1, 0)} and result.with_reference == frozenset()


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


def test_a_compilation_row_is_a_candidate_of_every_episode_it_covers_and_marks_its_file():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 2), row(1, "cvh", "Ref", 3),
            row(1, "kodik", "A", 1, end=3), row(1, "kodik", "B", 1), row(1, "kodik", "B", 2, end=3)]
    references = [reference_of(rows, "Ref", number) for number in (1, 2, 3)]
    result = planned(rows, references)
    assert chains(result) == {
        (1, 1, "A"): [("kodik", "kodik:A:1-3")], (1, 2, "A"): [("kodik", "kodik:A:1-3")],
        (1, 3, "A"): [("kodik", "kodik:A:1-3")], (1, 1, "B"): [("kodik", "kodik:B:1")],
        (1, 2, "B"): [("kodik", "kodik:B:2-3")], (1, 3, "B"): [("kodik", "kodik:B:2-3")]}
    chain = {(dub.episode, dub.voice_key): dub.chain for dub in result.dubs}
    assert chain[(2, "A")][0].file == (1, "kodik:A:1-3") and chain[(1, "B")][0].file is None
    assert chain[(2, "A")][0].as_json()["file"] == {"source_id": 1, "row_key": "kodik:A:1-3"}
    assert "file" not in chain[(1, "B")][0].as_json()


def test_a_compilation_serves_only_the_episodes_that_have_a_reference_and_in_the_part_of_it():
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 3, part=2), row(1, "kodik", "A", 1, end=3),
            row(1, "kodik", "B", 1, end=3, part=2)]
    references = [reference_of(rows, "Ref", 1), reference_of(rows, "Ref", 3)]
    result = chains(planned(rows, references))
    # Episode 2 has no reference, so nothing is planned for it; the reference of episode 3 is a second part.
    assert result == {(1, 1, "A"): [("kodik", "kodik:A:1-3")], (1, 1, "B"): [], (1, 3, "A"): [],
                      (1, 3, "B"): [("kodik", "kodik:B:1-3:p2")]}


def test_the_order_of_the_chain_is_the_same_for_a_compilation_and_a_single_row():
    rows = [row(1, "cvh", "Ref", 2), row(1, "kodik", "A", 2), row(2, "cvh", "A", 1, end=3)]
    chain = chains(planned(rows, [reference_of(rows, "Ref", 2)]))[(1, 2, "A")]
    assert chain == [("cvh", "cvh:A:1-3"), ("kodik", "kodik:A:2")]
    pick = EpisodeSettings(season=1, episode=2, levers=[lever("voice.player", "A", {"source_id": 1, "player": "kodik"})])
    assert chains(planned(rows, [reference_of(rows, "Ref", 2)], [pick]))[(1, 2, "A")] == [
        ("kodik", "kodik:A:2"), ("cvh", "cvh:A:1-3")]


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


def recorded(id, episode, key, *urls, season=1, part=0, status="pending", attempt=0):
    return {"id": id, "season": season, "episode": episode, "part": part, "voice_key": key,
            "chain": [{"url": url} for url in urls], "status": status, "attempt": attempt}


def dub_of(episode, label, *urls, season=1, part=0):
    return PlannedDub(season, episode, part, label, label, tuple(Candidate("kodik", label, url) for url in urls))


def test_a_planned_voice_over_that_has_the_link_of_a_recorded_row_under_another_name_is_that_row():
    rows = [recorded(1, 1, "Неизвестная озвучка 2", "https://site/b1"), recorded(2, 1, "Неизвестная озвучка 3", "https://site/c1")]
    keys = adopt([dub_of(1, "Group", "https://site/b1")], rows)
    assert keys == {(1, 1, 0, "Group"): "Неизвестная озвучка 2"}


def test_two_voice_overs_with_different_links_stay_different_whatever_their_names():
    rows = [recorded(1, 1, "Неизвестная озвучка 2", "https://site/b1")]
    keys = adopt([dub_of(1, "Group", "https://site/other"), dub_of(1, "Неизвестная озвучка 3", "https://site/c1")], rows)
    assert keys == {(1, 1, 0, "Group"): "Group", (1, 1, 0, "Неизвестная озвучка 3"): "Неизвестная озвучка 3"}


def test_a_row_is_adopted_by_one_voice_over_and_not_by_one_that_has_a_row_of_its_own_key():
    rows = [recorded(1, 1, "Old", "https://site/x"), recorded(2, 1, "New", "https://site/x")]
    keys = adopt([dub_of(1, "New", "https://site/x"), dub_of(1, "Other", "https://site/x")], rows)
    assert keys == {(1, 1, 0, "New"): "New", (1, 1, 0, "Other"): "Old"}
    twice = adopt([dub_of(1, "A", "https://site/x"), dub_of(1, "B", "https://site/x")], [recorded(1, 1, "Old", "https://site/x")])
    assert twice == {(1, 1, 0, "A"): "Old", (1, 1, 0, "B"): "B"}


def test_a_row_of_another_episode_is_not_adopted_and_a_voice_over_with_no_link_adopts_nothing():
    rows = [recorded(1, 2, "Old", "https://site/x")]
    assert adopt([dub_of(1, "New", "https://site/x"), dub_of(2, "Empty")], rows) == {
        (1, 1, 0, "New"): "New", (1, 2, 0, "Empty"): "Empty"}


def test_two_voice_overs_that_became_one_leave_the_row_whose_source_comes_first_in_the_chain_of_the_voice_over():
    rows = [recorded(1, 1, "A", "https://site/a", status="placed"), recorded(2, 1, "B", "https://site/b", status="placed")]
    assert adopt([dub_of(1, "C", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "C"): "B"}
    assert adopt([dub_of(1, "C", "https://site/a", "https://site/b")], rows) == {(1, 1, 0, "C"): "A"}


def test_a_row_that_has_its_track_stays_over_one_that_has_not_whatever_the_order_of_the_chain():
    rows = [recorded(1, 1, "A", "https://site/a", status="placed"), recorded(2, 1, "B", "https://site/b", status="downloading")]
    assert adopt([dub_of(1, "C", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "C"): "A"}


def test_the_row_that_works_from_a_later_source_of_its_chain_is_ranked_by_the_source_it_works_from():
    rows = [recorded(1, 1, "A", "https://site/z", "https://site/a", status="placed", attempt=1),
            recorded(2, 1, "B", "https://site/b", status="placed")]
    assert adopt([dub_of(1, "C", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "C"): "B"}


def test_a_row_of_the_name_whose_source_the_voice_over_no_longer_offers_gives_way_to_a_row_whose_source_it_does():
    rows = [recorded(1, 1, "Old", "https://site/x"), recorded(2, 1, "New", "https://site/y")]
    assert adopt([dub_of(1, "New", "https://site/x")], rows) == {(1, 1, 0, "New"): "Old"}


def test_a_row_that_has_its_track_stays_over_the_row_that_already_has_the_name_the_voice_over_now_carries():
    """A group made of two voice-overs takes the name of one of them: the rows are judged the same whichever name it is."""
    rows = [recorded(1, 1, "A", "https://site/a", status="placed"), recorded(2, 1, "B", "https://site/b", status="downloading")]
    assert adopt([dub_of(1, "B", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "B"): "A"}
    assert adopt([dub_of(1, "A", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "A"): "A"}


def test_the_row_with_the_name_stays_when_it_has_its_track_and_the_other_has_not():
    rows = [recorded(1, 1, "A", "https://site/a", status="downloading"), recorded(2, 1, "B", "https://site/b", status="placed")]
    assert adopt([dub_of(1, "B", "https://site/a", "https://site/b")], rows) == {(1, 1, 0, "B"): "B"}


def test_two_rows_with_tracks_stay_by_the_order_of_the_chain_whichever_of_them_has_the_name():
    rows = [recorded(1, 1, "A", "https://site/a", status="placed"), recorded(2, 1, "B", "https://site/b", status="placed")]
    assert adopt([dub_of(1, "B", "https://site/a", "https://site/b")], rows) == {(1, 1, 0, "B"): "A"}
    assert adopt([dub_of(1, "B", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "B"): "B"}
    assert adopt([dub_of(1, "A", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "A"): "B"}
    assert adopt([dub_of(1, "A", "https://site/a", "https://site/b")], rows) == {(1, 1, 0, "A"): "A"}


def test_the_row_with_the_name_is_judged_with_the_others_even_when_its_own_link_left_the_chain():
    rows = [recorded(1, 1, "A", "https://site/a", status="placed"), recorded(2, 1, "B", "https://site/other", status="downloading")]
    assert adopt([dub_of(1, "B", "https://site/b", "https://site/a")], rows) == {(1, 1, 0, "B"): "A"}


def test_a_variant_the_user_left_out_is_named_by_its_source_and_player_not_by_the_link_it_has_now():
    """The link of a row may change after the work began; the work that runs from the variant is found by what the variant is."""
    rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "A", 1, link="https://site/new-link"), row(1, "kodik", "A", 1)]
    leave_out = EffectiveLever(lever="variant.allow", target=variant_target("A", 1, "cvh"), value=False, level=Level.EPISODE)
    found = planned(rows, [reference_of(rows, "Ref", 1)], settings=[EpisodeSettings(season=1, episode=1, levers=[leave_out])])
    [dub] = [each for each in found.dubs if each.voice_key == "A"]
    assert dub.left_out == frozenset({(1, "cvh")}) and [each.player for each in dub.chain] == ["kodik"]
    assert [each.source_id for each in dub.chain] == [1]  # a link of the chain says which source it was found in


def test_a_voice_over_names_the_source_and_the_player_its_work_is_on_the_first_one_before_it_begins_and_the_last_after_all():
    from modules.voices.planning import candidate_of

    chain = [{"player": "kodik", "source_id": 29, "url": "u1"}, {"player": "cvh", "source_id": 29, "url": "u2"}]
    assert candidate_of({"chain": chain, "attempt": 0})["player"] == "kodik"
    assert candidate_of({"chain": chain, "attempt": 1})["player"] == "cvh"
    assert candidate_of({"chain": chain, "attempt": 5})["player"] == "cvh"
    assert candidate_of({"chain": [], "attempt": 0}) == {} and candidate_of({}) == {}


def test_a_started_work_stands_exactly_while_the_layout_does_not_give_its_video_to_another_episode_or_voice_over():
    """Every placing of two videos (each: not in the layout, excluded, or a row of the voice-over X or Y at episode 1 or 2),
    for a recorded work of the voice-over X at episode 1 or 2, on either video, in any step, with a chain that knows the
    keys of its videos or only their links. The work stands unless the layout has its video and gives it to another episode
    or another voice-over; that is told by the layout alone, whatever the plan and the chain are."""
    def video(key, placing):
        if placing is None:
            return []
        voice, episode, excluded = placing
        return [CompositionRow(series_id=7, source_id=1, row_key=key, season=1, episode_start=episode, episode_end=episode,
                               voice_label=voice, player="kodik", state=RowState.PLAYABLE, excluded=excluded,
                               link=f"https://site/{key}")]

    placings = [None] + [(voice, episode, excluded) for voice in "XY" for episode in (1, 2) for excluded in (False, True)]
    checked = 0
    for first in placings:
        for second in placings:
            rows = [row(1, "cvh", "Ref", 1), row(1, "cvh", "Ref", 2), *video("a", first), *video("b", second)]
            references = [reference_of(rows, "Ref", 1), reference_of(rows, "Ref", 2)]
            result = planned(rows, references)
            videos = Videos.of(rows)
            for dub in result.dubs:
                if dub.voice_key != "X":
                    continue
                for key, placing in (("a", first), ("b", second)):
                    for status in STARTED | {"pending", "failed"}:
                        for keyed in (True, False):
                            candidate = Candidate("kodik", "X", f"https://site/{key}", None, 1, key if keyed else None)
                            recorded = {"status": status, "attempt": 0, "chain": [candidate.as_json()]}
                            here = placing is not None and not placing[2] and placing[0] == "X" and placing[1] == dub.episode
                            gone = placing is None or placing[2]
                            expected = status in STARTED and not here and not gone
                            assert moved(recorded, dub.chain, videos) is expected, (first, second, dub.episode, key, status, keyed)
                            checked += 1
    assert checked > 1000  # the space is not empty: every case that plans X is checked
