"""The tracks of a card of the packing (modules/planner/packing.py): the container's tracks in its order as numbers of the
season, where each comes from, the tracks of the file before it does not hold, and why a container is built when no track
came or went."""
from types import SimpleNamespace as N

from modules.planner.packing import tracks_of

ORDER = ("Ref", "A", "B", "C")


def way(voice, kind="reuse"):
    return N(voice=voice, kind=kind, snapshot=N(group=voice))


def goal(ways, own_at=0, video_id="v1"):
    return N(tracks=tuple(ways), own_at=own_at,
             video=N(snapshot=N(group="Ref"), container=N(video=N(id=video_id))))


def before(*labels, video_id="v1"):
    return N(tracks=[N(snapshot=N(group=label)) for label in labels], video=N(id=video_id))


def kinds(tracks):
    return [(track.number, track.label, track.kind) for track in tracks]


def test_a_track_made_in_the_sync_is_new_one_the_file_held_is_kept_and_one_it_loses_is_gone():
    tracks, reason = tracks_of(goal([way("A"), way("C", "download")]), before("A", "B"), ORDER)
    assert kinds(tracks) == [(1, "Ref", "own"), (2, "A", "kept"), (4, "C", "new"), (3, "B", "gone")] and reason == ""


def test_a_container_with_the_same_tracks_is_built_for_the_order_the_video_or_the_names():
    assert tracks_of(goal([way("B"), way("A")]), before("A", "B"), ORDER)[1] == "order"
    assert tracks_of(goal([way("A"), way("B")], video_id="v2"), before("A", "B"), ORDER)[1] == "video"
    assert tracks_of(goal([way("A"), way("B")]), before("A", "B"), ORDER)[1] == "names"


def test_a_voice_over_the_order_of_the_season_has_not_has_no_number():
    tracks, _ = tracks_of(goal([way("Z", "download")], own_at=1), None, ORDER)
    assert kinds(tracks) == [(None, "Z", "new"), (1, "Ref", "own")]
