"""A recorded failure makes the series an error only when an episode lacks what the attempt was for
(modules/planner/view.covered): another source that gave the voice-over leaves nothing lacking."""
from modules.planner.view import covered


def asset(episode, video_group, *groups, off=False):
    return {"episode": list(episode), "off": off, "video": {"group": video_group},
            "tracks": [{"group": group} for group in groups]}


def failure(voice, *episodes):
    return {"kind": "align", "episodes": [list(each) for each in episodes], "voice": voice}


def test_a_voice_over_another_source_gave_covers_its_failure():
    assert covered(failure("ПВА ШОУ", (1, 330)), [asset((1, 330), "CO4K", "ruchime", "ПВА ШОУ")])


def test_the_own_sound_of_the_video_covers_a_failure_of_its_voice_over():
    assert covered(failure("AniStar", (1, 1)), [asset((1, 1), "AniStar")])


def test_an_episode_without_the_voice_over_or_without_a_visible_file_is_not_covered():
    assert not covered(failure("ПВА ШОУ", (1, 330)), [asset((1, 330), "CO4K", "ruchime")])
    assert not covered(failure("ПВА ШОУ", (1, 330)), [asset((1, 330), "CO4K", "ПВА ШОУ", off=True)])
    assert not covered(failure("ПВА ШОУ", (1, 330)), [])


def test_a_compilation_is_covered_only_when_every_episode_it_holds_has_the_voice_over():
    assets = [asset((1, 1), "Ref", "G"), asset((1, 2), "Ref")]
    assert not covered(failure("G", (1, 1), (1, 2)), assets)
    assert covered(failure("G", (1, 1), (1, 2)), [*assets[:1], asset((1, 2), "Ref", "G")])


def test_a_video_row_with_no_voice_over_is_covered_by_a_file_of_the_episode_and_a_row_offline_by_nothing_lacking():
    assert covered(failure(None, (1, 1)), [asset((1, 1), "Ref")])
    assert not covered(failure(None, (1, 1)), [])
    assert covered(failure("G"), [])
