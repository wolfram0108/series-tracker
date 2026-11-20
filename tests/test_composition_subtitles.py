"""The subtitles in the composition (stage 15, DESIGN.md 7.1.1): rows of their own, apart from the rows of sound; one table on
the series and one on an episode, in the order of core.subtitle_queue, with the variants the level leaves out marked."""
from core.contracts import (SUBTITLES_ALLOW, SUBTITLES_ORDER, CompositionRow, EffectiveLever, Level, RowState, RowTrack,
                            variant_target)
from modules.composition import queues


def subtitles(name: str, episode: int, source: int = 1, language: str = "rus", kind: str = "full", stale=False):
    return CompositionRow(series_id=1, source_id=source, row_key=f"{source}:{name}:{episode}", season=1,
                          episode_start=episode, episode_end=episode, voice_label=name, voice_name=name,
                          player=f"torrent:{source}", state=RowState.PLAYABLE, track=RowTrack.SUBTITLES,
                          language=language, subtitle_type=kind, stale=stale)


def sound(episode: int) -> CompositionRow:
    return CompositionRow(series_id=1, source_id=1, row_key=f"a:{episode}", season=1, episode_start=episode,
                          episode_end=episode, voice_group="Студия", voice_label="Студия", player="torrent:1",
                          state=RowState.PLAYABLE)


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
