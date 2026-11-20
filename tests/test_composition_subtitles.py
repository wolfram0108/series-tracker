"""The subtitles in the composition (stage 15, DESIGN.md 7.1.1): rows of their own, apart from the rows of sound; one table on
the series and one on an episode, in the order of core.subtitle_queue, with the variants the level leaves out marked."""
from core.contracts.compilation import compilation_key
from core.contracts import (SUBTITLES_ALLOW, SUBTITLES_ORDER, CompositionRow, EffectiveLever, Level, RowState, RowTrack,
                            SettingRecord, variant_target)
from core.subtitle_queue import ordered
from modules.composition import queues
from modules.composition.project import split_rows
from modules.composition.settings import retarget, validate


def subtitles(name: str, episode: int, source: int = 1, language: str = "rus", kind: str = "full", stale=False):
    return CompositionRow(series_id=1, source_id=source, row_key=f"{source}:{name}:{episode}", season=1,
                          episode_start=episode, episode_end=episode, voice_label=name, voice_name=name,
                          player=f"torrent:{source}", state=RowState.PLAYABLE, track=RowTrack.SUBTITLES,
                          language=language, subtitle_type=kind, stale=stale)


def sound(episode: int) -> CompositionRow:
    return CompositionRow(series_id=1, source_id=1, row_key=f"a:{episode}", season=1, episode_start=episode,
                          episode_end=episode, voice_group="Студия", voice_label="Студия", player="torrent:1",
                          state=RowState.PLAYABLE)


def test_the_rows_of_subtitles_are_apart_from_the_rows_of_sound_and_are_in_the_output_with_no_group():
    rows = [sound(1), subtitles("Полные", 1), sound(2)]
    assert split_rows(rows) == ([rows[0], rows[2]], [rows[1]])
    assert rows[1].in_output and rows[1].voice_group is None


def test_the_order_of_the_subtitles_is_the_users_then_the_sources_then_the_layout():
    found = [(("B", 2, "p"), 1, 0), (("A", 1, "p"), 0, 5), (("C", 1, "p"), 0, 3)]
    assert ordered(found, []) == [("C", 1, "p"), ("A", 1, "p"), ("B", 2, "p")]
    assert ordered(found, [("B", 2, "p"), ("Z", 9, "p")]) == [("B", 2, "p"), ("C", 1, "p"), ("A", 1, "p")]


def test_the_queue_of_the_level_lists_every_variant_with_its_language_type_and_episodes_and_marks_the_left_out():
    lines = [subtitles("Полные", 1), subtitles("Надписи", 1, kind="signs"), subtitles("Полные", 2),
             subtitles("Full", 1, source=2, language="eng")]
    levers = [EffectiveLever(lever=SUBTITLES_ORDER, value=[{"group": "Full", "source_id": 2, "player": "torrent:2"}],
                             level=Level.SERIES),
              EffectiveLever(lever=SUBTITLES_ALLOW, target=variant_target("Надписи", 1, "torrent:1"), value=False,
                             level=Level.SERIES)]
    shown = queues.subtitles(lines, levers, {1: 0, 2: 1})
    assert shown.order_level == Level.SERIES
    assert [(row.group, row.source_id, row.language, row.subtitle_type, row.episodes, row.allowed) for row in shown.rows] == [
        ("Full", 2, "eng", "full", "1", True), ("Полные", 1, "rus", "full", "1–2", True),
        ("Надписи", 1, "rus", "signs", "1", False)]
    episode = queues.subtitles([line for line in lines if line.episode_start == 2], [], {1: 0, 2: 1}, episode=2)
    assert [(row.group, row.episodes) for row in episode.rows] == [("Полные", "2")]


def test_the_queue_tells_how_the_track_of_each_variant_lies_in_the_container_by_the_rule_of_the_planner():
    lines = [subtitles("Надписи", 1, kind="signs"), subtitles("Полные", 1), subtitles("Надписи 2", 1, kind="signs"),
             subtitles("Полные 2", 1)]
    flags = lambda shown: [(row.group, row.default, row.forced) for row in shown.rows]  # noqa: E731
    assert flags(queues.subtitles(lines, [], {1: 0})) == [
        ("Надписи", False, True), ("Полные", True, False), ("Надписи 2", False, False), ("Полные 2", False, False)]
    # A variant the level leaves out lays nothing: the next of its type is the one.
    deny = [EffectiveLever(lever=SUBTITLES_ALLOW, target=variant_target("Полные", 1, "torrent:1"), value=False,
                           level=Level.SEASON)]
    assert flags(queues.subtitles(lines, deny, {1: 0})) == [
        ("Надписи", False, True), ("Полные", False, False), ("Надписи 2", False, False), ("Полные 2", True, False)]


def test_the_levers_of_the_subtitles_are_set_on_the_series_a_season_and_an_episode_not_on_a_compilation():
    value = [{"group": "Полные", "source_id": 1, "player": "torrent:1"}]
    member = compilation_key("Студия", 1, "torrent:1", 1, 1, 2)
    for scope in ({}, {"season": 1}, {"season": 1, "episode": 2}):
        assert validate(SettingRecord(series_id=1, lever=SUBTITLES_ORDER, value=value, **scope)) is None, scope
    assert "bad_level" in validate(SettingRecord(series_id=1, season=1, compilation=member, lever=SUBTITLES_ORDER, value=value))
    target = variant_target("Полные", 1, "torrent:1")
    assert validate(SettingRecord(series_id=1, lever=SUBTITLES_ALLOW, value=True, target=target)) is None
    assert validate(SettingRecord(series_id=1, season=1, lever=SUBTITLES_ALLOW, value=False, target=target)) is None
    assert "bad_level" in validate(SettingRecord(series_id=1, season=1, compilation=member, lever=SUBTITLES_ALLOW, value=False,
                                                 target=target))
    # How a track lies in the container is no lever: the rule of the planner lays it.
    assert validate(SettingRecord(series_id=1, lever="subtitles.default", value=True, target=target)) is not None


def test_a_voice_over_group_renamed_like_a_name_of_subtitles_leaves_the_subtitles_as_they_were():
    record = SettingRecord(series_id=1, lever=SUBTITLES_ORDER, value=[{"group": "Полные", "source_id": 1, "player": "p"}])
    allow = SettingRecord(series_id=1, lever=SUBTITLES_ALLOW, value=False, target=variant_target("Полные", 1, "p"))
    assert sorted(retarget([record, allow], {"Полные": "Другие"}), key=repr) == sorted([record, allow], key=repr)
