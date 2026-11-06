"""The container of an episode made at the end of a sync (modules/downloads/packing.py, ЭТАП_11), told by one rule: a track
goes in while it is aligned to a video of the identity of the winner of the slot, from wherever it is (a file voices placed,
the container of the winner, a container the winner displaced), known by the key of its voice-over and named by its group
now, in the order of the season; the old container lives until the new one is in place, a file the series does not own is
never replaced, and a publication a stop cut short is finished from its record."""
import json
import os
import sqlite3

import pytest

from core import BaseModule, Bus, Runner
from core.container import AudioIn
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from modules.downloads.packing import file_identity
from packing_support import FakePacker
from test_downloads_module import FakeDownloader, FakeNeighbours, Probe, _add_item, _items, env_paths  # noqa: F401

OWN, DUB = "Ruchime", "ПВА ШОУ"
SAME, OTHER = "7:vk:win", "7:vk:elsewhere"
KEY = "pva"


class FakeVoices(BaseModule):
    name = "voices"

    def __init__(self, bus):
        self.tracks: list[dict] = []
        self.packed: list[dict] = []
        self.lose_answer = 0  # the record is written, then the answer is lost (a stop right after it)
        super().__init__(bus)

    def register(self):
        self.handle("voices.tracks.get", self.on_tracks)
        self.handle("voices.tracks.packed", self.on_packed)

    async def on_tracks(self, env):
        return {"tracks": [dict(each) for each in self.tracks]}

    async def on_packed(self, env):
        self.packed.append(dict(env.payload))
        for track in self.tracks:  # as voices records it: the track lives in the container, aligned to its video
            if track["dub_id"] in env.payload["dub_ids"]:
                track.update(path=None, ref_path=env.payload["container"], aligned_to=env.payload["aligned_to"])
        if self.lose_answer:
            self.lose_answer -= 1
            raise RuntimeError("остановка после записи")


def dub(*, path=None, aligned_to=SAME, ref_uid="win", ref_path="", label=DUB, key=KEY, status="placed", dub_id=5):
    """The record of one voice-over of episode 1 as voices.tracks.get gives it."""
    return {"dub_id": dub_id, "season": 1, "episode": 1, "part": 0, "key": key, "label": label, "status": status,
            "path": path, "aligned_to": aligned_to, "ref_uid": ref_uid, "ref_path": ref_path}


@pytest.fixture
async def rig(env_paths):  # noqa: F811
    db_path, media_dir = env_paths
    bus = Bus()
    db = Database(db_path)
    fake, voices, packer = FakeNeighbours(bus), FakeVoices(bus), FakePacker()
    runner = Runner(bus, [fake, voices, CatalogModule(bus, db), DownloadsModule(bus, db, downloader=FakeDownloader(),
                                                                                 packer=packer), Probe(bus)])
    await runner.start()
    fake.references = [{"season": 1, "episode": 1, "part": None, "mark": "by_level", "source_id": 7, "row_key": "vk:win",
                        "player": "vk", "voice_group": OWN, "height": 2160, "reason": None}]
    yield fake, voices, packer, runner.modules[-1], db_path, media_dir
    await runner.stop()


def put(media_dir, name, data=b"video") -> str:
    path = os.path.join(media_dir, name)
    with open(path, "wb") as out:
        out.write(data)
    return path


def place(db_path, uid, **columns):
    sets = ", ".join(f"{name}=?" for name in columns)
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE media_items SET {sets} WHERE unique_id=?", (*columns.values(), uid))
        conn.commit()


def order(fake, labels):
    fake.orders = [{"season": 1, "voices": [{"label": label, "episodes": "1"} for label in labels]}]


async def made(packer, path, identity, tracks):
    """A container the packer built before, as the file of a row."""
    await packer.build(path + ".tmp", path, identity, tracks)
    os.replace(path + ".tmp", path)
    return path


def winner(db_path, filename):
    _add_item(db_path, "win", 1, status="completed", filename=filename)
    place(db_path, "win", season=1, source_id=7, row_key="vk:win")


async def pack(probe):
    return await probe.request("downloads.pack", {"series_id": 2}, timeout=10)


def outcome(reply):
    """The counts of a packing that are not zero (the slots that failed are looked at apart)."""
    return {key: value for key, value in reply.items() if value and key != "failed_slots"}


@pytest.mark.asyncio
@pytest.mark.parametrize("seq", [[OWN, DUB], [DUB, OWN]])
@pytest.mark.parametrize("placed", ["none", "same", "other", "legacy"])
@pytest.mark.parametrize("displaced", ["none", "same", "same_unkeyed", "other"])
async def test_a_track_goes_into_the_container_while_it_is_aligned_to_the_video_of_the_winner(rig, displaced, placed, seq):
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01 2160p.mp4", b"https://v/win")
    winner(db_path, "e01 2160p.mp4")
    if displaced != "none":
        old = put(media_dir, "e01 1080p.mkv", b"https://v/old")
        identity = OTHER if displaced == "other" else SAME
        key = "" if displaced == "same_unkeyed" else KEY  # a container made before the keys knows its tracks by name
        await made(packer, old, identity, [AudioIn(OWN, old, 0, identity, own=True), AudioIn(DUB, old, 1, identity, key=key)])
        _add_item(db_path, "old", 1, plan="replaced", status="completed", filename="e01 1080p.mkv", superseded_by="win")
        place(db_path, "old", season=1, source_id=7, row_key="vk:elsewhere" if displaced == "other" else "vk:win")
    # The voice-over has one row at voices: its track is a file it placed, or it is packed in the old container already.
    sidecar = None
    if placed != "none":
        sidecar = put(media_dir, f"e01 2160p.{DUB}.ru.flac", b"track")
        # A legacy track was aligned before the identity was recorded: its reference is the winner itself.
        voices.tracks = [dub(path=sidecar, aligned_to={"same": SAME, "other": OTHER, "legacy": ""}[placed],
                             ref_path=os.path.join(media_dir, "e01 2160p.mp4"))]
    elif displaced != "none":
        voices.tracks = [dub(aligned_to=OTHER if displaced == "other" else SAME, ref_uid="old",
                             ref_path=os.path.join(media_dir, "e01 1080p.mkv"))]
    order(fake, seq)

    reply = await pack(probe)

    # The rule, told on its own: the dub is in while a copy of it synchronous with the winner's video is there — the file
    # voices placed for that video (the fresh one wins), or the track of a container of the same video, whatever the last
    # work of the voice-over was (the plan holds it).
    fresh = placed in ("same", "legacy")
    dub_in = fresh or displaced in ("same", "same_unkeyed")
    expected = [label for label in seq if label == OWN or (label == DUB and dub_in)]
    name, identity, tracks = packer.builds[-1]
    assert reply["packed"] == 1 and identity == SAME and [label for label, _, _ in tracks] == expected
    assert packer.keys[-1] == [KEY if label == DUB else "" for label in expected]  # every track built carries its key
    if dub_in:
        source = dict((label, path) for label, path, _ in tracks)[DUB]
        assert source == (os.path.basename(sidecar) if fresh else "e01 1080p.mkv")
    left = sorted(os.listdir(media_dir))
    assert "e01 2160p.mkv" in left and "e01 2160p.mp4" not in left and "e01 1080p.mkv" not in left
    if sidecar:
        assert (os.path.basename(sidecar) in left) is (placed == "other")  # a track of another video is not the winner's
    items = _items(db_path)
    assert items["win"]["final_filename"] == "e01 2160p.mkv" and items["win"]["status"] == "completed"
    if displaced != "none":
        assert items["old"]["final_filename"] is None
    # Voices is told of a voice-over whose own record is synchronous with the video: one aligned to another video is not.
    told = [(each_id, each["aligned_to"], each["container"]) for each in voices.packed for each_id in each["dub_ids"]]
    recorded = fresh or (placed == "none" and dub_in)
    assert told == ([(5, SAME, os.path.join(media_dir, "e01 2160p.mkv"))] if recorded else [])


@pytest.mark.asyncio
async def test_a_container_that_is_what_the_plan_asks_is_left_alone_and_one_that_is_not_is_rebuilt_from_itself(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME, key=KEY)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(ref_path=path)]
    for seq, expected in [([OWN, DUB], {"unchanged": 1}), ([DUB, OWN], {"packed": 1}), ([DUB, OWN], {"unchanged": 1})]:
        order(fake, seq)
        assert outcome(await pack(probe)) == expected
    assert [label for label, _, _ in packer.builds[-1][2]] == [DUB, OWN]
    assert sorted(os.listdir(media_dir)) == ["e01.mkv"]
    assert [each["dub_ids"] for each in voices.packed] == [[5]]  # told once: by the rebuild, not by the slots left alone
    voices.tracks = []  # the voice-over left the plan: the next packing takes its track out
    await pack(probe)
    assert [label for label, _, _ in packer.builds[-1][2]] == [OWN]


@pytest.mark.asyncio
async def test_a_renamed_group_keeps_its_track_under_the_new_name(rig):
    """The name of a group is no key: a track packed as «ПВА ШОУ» whose voice-over is now named otherwise stays, takes the
    new name, and the stream is the same one (Н-1)."""
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME, key=KEY)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(label="PVA Show", ref_path=path)]
    order(fake, [OWN, "PVA Show"])
    assert outcome(await pack(probe)) == {"packed": 1}
    assert packer.builds[-1][2] == [(OWN, "e01.mkv", True), ("PVA Show", "e01.mkv", False)]
    assert packer.keys[-1] == ["", KEY]
    assert outcome(await pack(probe)) == {"unchanged": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["aligning", "pending"])
async def test_a_track_whose_voice_over_is_worked_on_again_stays_in_the_container(rig, status):
    """Only a voice-over that left the plan leaves the container: one the plan holds at another step keeps its track."""
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME, key=KEY)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(status=status, ref_path=path)]
    order(fake, [DUB, OWN])
    assert outcome(await pack(probe)) == {"packed": 1}
    assert [(label, key) for (label, _, _), key in zip(packer.builds[-1][2], packer.keys[-1])] == [(DUB, KEY), (OWN, "")]


@pytest.mark.asyncio
async def test_a_track_of_an_old_container_that_no_voice_over_claims_is_kept(rig):
    """A container made before the keys: a track its voice-over is named otherwise now cannot be told by name; it is kept
    as it is, not dropped, and the track that can be told gets its key."""
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME),
                                    AudioIn("Old name", path, 2, SAME)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(ref_path=path), dub(label="New name", key="other", dub_id=6, ref_path=path)]
    order(fake, [OWN, DUB, "New name"])
    assert outcome(await pack(probe)) == {"packed": 1}
    assert [label for label, _, _ in packer.builds[-1][2]] == [OWN, DUB, "Old name"]
    assert packer.keys[-1] == ["", KEY, ""]


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["another_series", "another_row", "nobody"])
async def test_a_file_under_the_name_of_the_container_that_the_slot_does_not_own_is_not_replaced(rig, holder):
    """A shared folder: the name the container takes is a file of another series, of another row, or of nobody known; it is
    not replaced, the slot fails, and nothing of the winner moves (Н-2)."""
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    put(media_dir, "e01.mkv", b"SERIES THREE VIDEO")
    if holder == "another_series":
        _add_item(db_path, "other", 1, status="completed", filename="e01.mkv", series_id=3)
    elif holder == "another_row":
        _add_item(db_path, "sibling", 2, status="completed", filename="e01.mkv")
    order(fake, [OWN])
    assert outcome(await pack(probe)) == {"failed": 1}
    with open(os.path.join(media_dir, "e01.mkv"), "rb") as found:
        assert found.read() == b"SERIES THREE VIDEO"
    assert sorted(os.listdir(media_dir)) == ["e01.mkv", "e01.mp4"] and packer.builds == []
    assert _items(db_path)["win"]["final_filename"] == "e01.mp4"


@pytest.mark.asyncio
async def test_a_publication_stopped_after_its_replace_is_finished_at_the_next_packing(rig):
    """The container is in place and voices recorded its tracks, then the answer is lost: the old video and the placed
    track are not forgotten — the record of the publication finishes them at the next packing (Н-7)."""
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    sidecar = put(media_dir, f"e01.{DUB}.ru.flac", b"track")
    voices.tracks = [dub(path=sidecar, ref_path=os.path.join(media_dir, "e01.mp4"))]
    order(fake, [OWN, DUB])
    voices.lose_answer = 1
    with pytest.raises(Exception):
        await pack(probe)
    assert {"e01.mkv", "e01.mp4", os.path.basename(sidecar)} <= set(os.listdir(media_dir))  # cut short before the cleanup
    assert outcome(await pack(probe)) == {"unchanged": 1}
    assert sorted(os.listdir(media_dir)) == ["e01.mkv"]
    assert _items(db_path)["win"]["final_filename"] == "e01.mkv"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pack_publications").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_publication_stopped_before_its_replace_leaves_the_old_files_and_names_and_its_build_goes(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    order(fake, [OWN])
    staging = put(media_dir, ".st-pack.win.mkv", b"half")
    with sqlite3.connect(db_path) as conn:
        conn.execute("INSERT INTO pack_publications (series_id, unique_id, name, staging, identity, signature, losers, "
                     "leftovers, dub_ids, state, created_at) VALUES (2, 'win', 'e01.mkv', ?, ?, ?, '[]', ?, '[]', "
                     "'building', '2026-11-05')", (staging, SAME, '[["Ruchime", "", true]]',
                                                   f'["{os.path.join(media_dir, "e01.mp4")}"]'))
        conn.commit()
    assert outcome(await pack(probe)) == {"packed": 1}  # the interrupted one is dropped, the slot is packed anew
    assert sorted(os.listdir(media_dir)) == ["e01.mkv"]
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pack_publications").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_slot_whose_planned_video_is_not_downloaded_yet_waits_and_keeps_its_file(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01 1080p.mp4", b"https://v/old")
    _add_item(db_path, "old", 1, plan="replaced", status="completed", filename="e01 1080p.mp4", superseded_by="win")
    place(db_path, "old", season=1, source_id=7, row_key="vk:old")
    _add_item(db_path, "win", 1, status="pending")
    place(db_path, "win", season=1, source_id=7, row_key="vk:win")
    order(fake, [OWN])
    assert outcome(await pack(probe)) == {"waiting": 1}
    assert os.listdir(media_dir) == ["e01 1080p.mp4"]


@pytest.mark.asyncio
async def test_a_container_left_alone_brings_the_record_of_its_tracks_into_step_with_itself(rig):
    """A track packed before its identity was recorded (or whose record drifted) is recorded as the container holds it, so
    that a later change of the resolution of the same video keeps it (ЭТАП_11, п. 5); a record in step is not told."""
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME, key=KEY)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(aligned_to="", ref_path=path)]
    order(fake, [OWN, DUB])
    for _ in range(2):
        assert outcome(await pack(probe)) == {"unchanged": 1}
    assert [(each["dub_ids"], each["aligned_to"]) for each in voices.packed] == [([5], SAME)]
    assert len(packer.builds) == 1  # only the build of the fixture: nothing was rebuilt


# --- a publication cut short, and what acts on the series before the next packing (N-9, N-10) ----------------------------

STOPS = ("in_build", "after_replace", "in_finish")
BETWEEN = ("nothing", "rename")


def open_publications(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM pack_publications").fetchone()[0]


async def cut_short(rig, stop: str, monkeypatch) -> None:
    """A packing that a stop cuts at the point named, with an old container of the same video in another resolution
    under the name the winner is to take (it reads like the new one but for its number)."""
    from modules.downloads.repository import DownloadsRepository

    fake, voices, packer, probe, db_path, media_dir = rig
    old = put(media_dir, "e01.mkv", b"OLD 1080p")
    await made(packer, old, SAME, [AudioIn(OWN, old, 0, SAME, own=True)])
    _add_item(db_path, "old", 1, plan="replaced", status="completed", filename="e01.mkv", superseded_by="win")
    place(db_path, "old", season=1, source_id=7, row_key="vk:win", resolution=1080)
    put(media_dir, "e01.mp4", b"NEW 2160p")
    winner(db_path, "e01.mp4")
    place(db_path, "win", resolution=2160)
    sidecar = put(media_dir, f"e01.{DUB}.ru.flac", b"track")
    voices.tracks = [dub(path=sidecar, ref_path=os.path.join(media_dir, "e01.mp4"))]
    order(fake, [OWN, DUB])
    if stop == "in_build":
        build = packer.build

        async def stopped(*args, **kwargs):
            raise RuntimeError("остановка во время сборки")
        packer.build = stopped
    elif stop == "after_replace":
        placed = DownloadsRepository.publication_placed

        async def stopped(self, publication_id):
            monkeypatch.setattr(DownloadsRepository, "publication_placed", placed)
            raise RuntimeError("остановка между заменой и отметкой")
        monkeypatch.setattr(DownloadsRepository, "publication_placed", stopped)
    else:
        voices.lose_answer = 1
    with pytest.raises(Exception):
        await pack(probe)
    if stop == "in_build":
        packer.build = build


@pytest.mark.asyncio
@pytest.mark.parametrize("between", BETWEEN)
@pytest.mark.parametrize("stop", STOPS)
async def test_a_publication_cut_short_anywhere_loses_no_video_and_leaves_no_name_without_its_file(rig, stop, between,
                                                                                                  monkeypatch):
    fake, voices, packer, probe, db_path, media_dir = rig
    await cut_short(rig, stop, monkeypatch)
    if between == "rename":
        current = _items(db_path)["win"]["final_filename"]
        names = {"win": "renamed" + os.path.splitext(current)[1]}
        assert (await probe.request("downloads.items.rename", {"series_id": 2, "names": names}, timeout=5))["renamed"] == 1

    reply = await pack(probe)

    assert reply["failed"] == 0
    name = _items(db_path)["win"]["final_filename"]
    assert os.path.isfile(os.path.join(media_dir, name)), (name, os.listdir(media_dir))
    assert open(os.path.join(media_dir, name), "rb").read() == b"NEW 2160p"  # the new video is the one kept
    assert not [each for each in os.listdir(media_dir) if each.startswith(".st-pack.")]
    assert not os.path.exists(os.path.join(media_dir, "e01.mp4")) or name == "e01.mp4"
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pack_publications").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_a_placed_publication_whose_container_is_not_under_its_name_is_closed_in_the_log_and_nothing_is_done_by_it(
        rig, caplog):
    """K-12. Put there, and gone or replaced from outside: the record does not know the files any more, so it removes
    nothing and names nothing; it is closed with the reason in the log, and the packing of the series goes on."""
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    sidecar = put(media_dir, f"e01.{DUB}.ru.flac", b"track")
    voices.tracks = [dub(path=sidecar, ref_path=os.path.join(media_dir, "e01.mp4"))]
    order(fake, [OWN, DUB])
    voices.lose_answer = 1
    with pytest.raises(Exception):
        await pack(probe)
    os.replace(os.path.join(media_dir, "e01.mkv"), os.path.join(media_dir, "moved away.mkv"))
    put(media_dir, "e01.mkv", b"someone else's")

    with caplog.at_level("ERROR"):
        reply = await pack(probe)

    assert any("не доведена" in record.getMessage() for record in caplog.records)
    assert open(os.path.join(media_dir, "moved away.mkv"), "rb").read() == b"https://v/win"
    assert os.path.exists(sidecar)  # nothing the record names is removed by it
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pack_publications").fetchone()[0] == 0
    assert "failed" in reply  # the packing of the series went on


@pytest.mark.asyncio
async def test_a_record_names_its_files_inside_the_folder_of_the_series(rig, monkeypatch):
    """Its staging name and leftovers are names inside the folder; each leftover with the identity of the file it is, so that
    a name alone never removes a file (Н-15)."""
    fake, voices, packer, probe, db_path, media_dir = rig
    await cut_short(rig, "in_finish", monkeypatch)
    with sqlite3.connect(db_path) as conn:
        staging, leftovers, number = conn.execute("SELECT staging, leftovers, publication FROM pack_publications").fetchone()
    assert not os.path.isabs(staging) and staging.startswith(".st-pack.") and number in staging
    written = json.loads(leftovers)
    assert written and all(not os.path.isabs(each["name"]) for each in written)
    assert all(each["file"] == list(file_identity(os.path.join(media_dir, each["name"]))) for each in written)


# --- the old tracks without a key (N-13) ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_old_track_is_known_by_the_key_of_its_voice_over_after_its_group_is_renamed_and_follows_the_order(rig):
    """The name an old track carries is the name its group had: the key of the voice-over. Renamed and put first, it takes
    the new name, its key and its place."""
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME)])
    winner(db_path, "e01.mkv")
    voices.tracks = [dub(label="New group", key=DUB, ref_path=path)]
    order(fake, ["New group", OWN])

    assert outcome(await pack(probe)) == {"packed": 1}
    assert (await packer.contents(path)).signature == [("New group", DUB, False), (OWN, "", True)]


@pytest.mark.asyncio
async def test_an_old_track_two_voice_overs_answer_to_is_not_decided_and_the_slot_is_not_built(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv")
    await made(packer, path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME)])
    winner(db_path, "e01.mkv")
    # One voice-over was named so first (its key), another is named so now.
    voices.tracks = [dub(label="Renamed", key=DUB, ref_path=path, dub_id=5),
                     dub(label=DUB, key="other", ref_path=path, dub_id=6)]
    order(fake, [OWN, DUB, "Renamed"])

    assert outcome(await pack(probe)) == {"failed": 1}
    assert (await packer.contents(path)).signature == [(OWN, "", True), (DUB, "", False)]  # left as it was


# --- the queue of the packing in the window of agents ----------------------------------------------------------------------

def pictures(sub) -> list[list[dict]]:
    found = []
    while not sub.queue.empty():
        found.append(sub.queue.get_nowait().payload["tasks"])
    return found


@pytest.mark.asyncio
async def test_the_packing_of_a_series_is_seen_in_the_queue_from_its_wait_to_its_end(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    sub = probe.bus.subscribe("downloads.pack.queue.changed")
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    voices.tracks = [dub(path=put(media_dir, f"e01.{DUB}.ru.flac", b"track"), ref_path=os.path.join(media_dir, "e01.mp4"))]
    order(fake, [OWN, DUB])

    assert outcome(await pack(probe)) == {"packed": 1}

    seen = pictures(sub)
    statuses = [[(task["episode"], task["status"]) for task in tasks] for tasks in seen]
    assert statuses[0] == [(None, "queued")]  # the series waits for its turn
    assert [(1, "building")] in statuses and [(1, "packed")] in statuses
    built = next(task for tasks in seen for task in tasks if task["status"] == "packed")
    assert built["name"] == "e01.mkv" and built["tracks"] == [OWN, DUB] and built["series_name"]
    assert seen[-1] == []  # what ended well leaves the queue with the packing


@pytest.mark.asyncio
async def test_a_packing_takes_the_episodes_it_is_asked_for_and_leaves_the_others_and_their_failures_alone(rig):
    """Each episode is a process of its own: the run asks for the episodes whose work ended (`only`) or for every one but
    those whose work goes on (`skip`), and a packing of one episode neither builds another nor hides its failure."""
    fake, voices, packer, probe, db_path, media_dir = rig
    sub = probe.bus.subscribe("downloads.pack.queue.changed")
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    fake.references.append({**fake.references[0], "episode": 2, "row_key": "vk:two"})  # its video is not downloaded yet
    order(fake, [OWN])

    async def broken(*args, **kwargs):
        return "ffmpeg_failed: test"
    build = packer.build
    packer.build = broken
    failed = await probe.request("downloads.pack", {"series_id": 2, "only": [[1, 1, 0]]}, timeout=10)
    assert outcome(failed) == {"failed": 1} and failed["failed_slots"] == [[1, 1, 0]]  # the run asks for it again
    packer.build = build

    assert outcome(await probe.request("downloads.pack", {"series_id": 2, "only": [[1, 2, 0]]}, timeout=10)) == {"waiting": 1}
    assert [(task["episode"], task["status"]) for task in pictures(sub)[-1]] == [(1, "failed")]  # the failure of 1 stays
    assert outcome(await probe.request("downloads.pack", {"series_id": 2, "skip": [[1, 2, 0]]}, timeout=10)) == {"packed": 1}
    assert pictures(sub)[-1] == []
    assert outcome(await probe.request("downloads.pack", {"series_id": 2, "skip": [[1, 1, 0], [1, 2, 0]]}, timeout=10)) == {}


@pytest.mark.asyncio
async def test_a_failed_slot_stays_in_the_queue_until_the_next_packing_and_a_slot_without_a_file_waits(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    sub = probe.bus.subscribe("downloads.pack.queue.changed")
    put(media_dir, "e01.mp4", b"https://v/win")
    winner(db_path, "e01.mp4")
    # Episode 2 is in the plan and has no row with a file: its video is not downloaded, it waits (it is counted).
    fake.references.append({**fake.references[0], "episode": 2, "row_key": "vk:two"})
    order(fake, [OWN])

    async def broken(*args, **kwargs):
        return "ffmpeg_failed: test"
    build = packer.build
    packer.build = broken
    assert outcome(await pack(probe)) == {"failed": 1, "waiting": 1}
    assert [(task["episode"], task["status"]) for task in pictures(sub)[-1]] == [(1, "failed")]

    packer.build = build
    assert outcome(await pack(probe)) == {"packed": 1, "waiting": 1}
    assert pictures(sub)[-1] == []


@pytest.mark.asyncio
async def test_a_slot_whose_packing_a_stop_cut_short_is_seen_until_its_record_is_finished(rig, monkeypatch):
    fake, voices, packer, probe, db_path, media_dir = rig
    sub = probe.bus.subscribe("downloads.pack.queue.changed")
    await cut_short(rig, "in_finish", monkeypatch)
    assert [(task["episode"], task["status"]) for task in pictures(sub)[-1]] == [(1, "interrupted")]
    before = sorted(os.listdir(media_dir))

    # Н-16: a look at the series outside a run (the disk check, a recount of the plan) changes no file: the record waits.
    await probe.request("downloads.fs.sync", {"series_id": 2}, timeout=5)
    probe.publish_event("scan.plan.updated", {"series_id": 2})
    await probe.bus.settled()
    assert sorted(os.listdir(media_dir)) == before and open_publications(db_path) == 1

    await pack(probe)  # a step of a run finishes it
    assert pictures(sub)[-1] == [] and open_publications(db_path) == 0


# --- the folder of a series moves while a publication is owed (Н-15) ----------------------------------------------------------

async def _owed_publication(rig):
    """A publication cut short after its replace: the container is in place, the source video and the track are owed."""
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01.mp4", b"new video")
    winner(db_path, "e01.mp4")
    voices.tracks = [dub(path=put(media_dir, "dub.flac", b"audio"))]
    order(fake, [OWN, DUB])
    voices.lose_answer = 1
    with pytest.raises(Exception):
        await pack(probe)
    assert open_publications(db_path) == 1 and os.path.isfile(os.path.join(media_dir, "e01.mp4"))


async def _library(probe, db_path):
    from modules.library import LibraryModule
    from test_downloads_module import _items as items_of

    class Media(BaseModule):
        name = "review_media"

        def register(self):
            self.handle("scan.media.list", self.items)

        async def items(self, env):
            return list(items_of(db_path).values())

    media, library = Media(probe.bus), LibraryModule(probe.bus, Database(db_path))
    await media.start()
    await library.start()
    return media, library


@pytest.mark.asyncio
async def test_a_leftover_is_removed_only_when_the_file_under_its_name_is_the_one_the_journal_wrote_down(rig, tmp_path):
    """The folder moved with the journal still owed: under the name of a leftover in the new folder lies another file. It is
    not the publication's, so it stays; the journal says so in the log."""
    fake, voices, packer, probe, db_path, media_dir = rig
    await _owed_publication(rig)
    media, library = await _library(probe, db_path)
    destination = tmp_path / "destination"
    destination.mkdir()
    stranger = destination / "e01.mp4"
    stranger.write_bytes(b"A FILE NO ROW HOLDS")
    try:
        await probe.request("library.relocation.plan", {"series_id": 2, "new_path": str(destination)}, timeout=5)
        assert (await probe.request("library.relocation.apply", {"series_id": 2}, timeout=5))["moved"]
        await pack(probe)
        assert stranger.read_bytes() == b"A FILE NO ROW HOLDS"
    finally:
        await library.stop()
        await media.stop()


@pytest.mark.asyncio
async def test_the_first_step_of_a_sync_finishes_an_owed_publication_in_its_folder_before_the_folder_moves(rig, tmp_path):
    fake, voices, packer, probe, db_path, media_dir = rig
    await _owed_publication(rig)
    media, library = await _library(probe, db_path)
    destination = tmp_path / "destination"
    destination.mkdir()
    stranger = destination / "e01.mp4"
    stranger.write_bytes(b"A FILE NO ROW HOLDS")
    try:
        assert (await probe.request("downloads.publications.finish", {"series_id": 2}, timeout=5)) == {"owed": False}
        # The leftovers went in the folder they were written down in: nothing of the publication is left to move.
        assert sorted(os.listdir(media_dir)) == ["e01.mkv"] and open_publications(db_path) == 0
        await probe.request("library.relocation.plan", {"series_id": 2, "new_path": str(destination)}, timeout=5)
        assert (await probe.request("library.relocation.apply", {"series_id": 2}, timeout=5))["moved"]
        assert sorted(os.listdir(destination)) == ["e01.mkv", "e01.mp4"] and stranger.read_bytes() == b"A FILE NO ROW HOLDS"
    finally:
        await library.stop()
        await media.stop()


@pytest.mark.asyncio
async def test_a_deleted_series_leaves_the_queue_of_the_packing_its_failed_slots_too(rig):
    """Н-21: a slot failed, the series is deleted, a new window connects: the queue it is given has nothing of the series."""
    fake, voices, packer, probe, db_path, media_dir = rig
    sub = probe.bus.subscribe("downloads.pack.queue.changed")
    put(media_dir, "e01.mp4")
    put(media_dir, "e01.mkv", b"a file no row holds")
    winner(db_path, "e01.mp4")
    order(fake, [OWN])
    assert (await pack(probe))["failed"] == 1
    assert [task["series_id"] for task in pictures(sub)[-1]] == [2]

    await probe.request("catalog.series.delete", {"series_id": 2}, timeout=5)
    await probe.bus.settled()  # the owners have heard series.deleted
    await probe.request("gateway.sse.clients", {"count": 1}, timeout=5)
    await probe.bus.settled()

    assert pictures(sub)[-1] == []
