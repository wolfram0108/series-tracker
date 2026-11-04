"""The container of an episode made at the end of a sync (modules/downloads/packing.py, ЭТАП_11), told by one rule: a track
goes in while it is aligned to a video of the identity of the winner of the slot, from wherever it is (a file voices placed,
the container of the winner, a container the winner displaced), in the order of the season; the old container lives until
the new one is in place. Every placing of the two voice-overs of a slot is tried against that rule told on its own."""
import itertools
import os
import sqlite3

import pytest

from core import BaseModule, Bus, Runner
from core.container import AudioIn
from core.db import Database
from modules.catalog import CatalogModule
from modules.downloads import DownloadsModule
from packing_support import FakePacker
from test_downloads_module import FakeDownloader, FakeNeighbours, Probe, _add_item, _items, env_paths  # noqa: F401

OWN, DUB = "Ruchime", "ПВА ШОУ"
SAME, OTHER = "7:vk:win", "7:vk:elsewhere"


class FakeVoices(BaseModule):
    name = "voices"

    def __init__(self, bus):
        self.tracks: list[dict] = []
        self.packed: list[dict] = []
        super().__init__(bus)

    def register(self):
        self.handle("voices.tracks.get", self.on_tracks)
        self.handle("voices.tracks.packed", self.on_packed)

    async def on_tracks(self, env):
        return {"tracks": self.tracks}

    async def on_packed(self, env):
        self.packed.append(dict(env.payload))


@pytest.fixture
async def rig(env_paths):  # noqa: F811
    db_path, media_dir = env_paths
    bus = Bus()
    db = Database(db_path)
    fake, voices, packer = FakeNeighbours(bus), FakeVoices(bus), FakePacker()
    runner = Runner(bus, [fake, voices, CatalogModule(bus, db), DownloadsModule(bus, db, downloader=FakeDownloader(),
                                                                                 packer=packer), Probe(bus)])
    await runner.start()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [[OWN, DUB], [DUB, OWN]])
@pytest.mark.parametrize("placed", ["none", "same", "other"])
@pytest.mark.parametrize("displaced", ["none", "same", "other"])
async def test_a_track_goes_into_the_container_while_it_is_aligned_to_the_video_of_the_winner(rig, displaced, placed, order):
    fake, voices, packer, probe, db_path, media_dir = rig
    put(media_dir, "e01 2160p.mp4", b"https://v/win")
    _add_item(db_path, "win", 1, status="completed", filename="e01 2160p.mp4")
    place(db_path, "win", season=1, source_id=7, row_key="vk:win")
    if displaced != "none":
        old = put(media_dir, "e01 1080p.mkv", b"https://v/old")
        identity = SAME if displaced == "same" else OTHER
        await packer.build(old + ".tmp", old, identity, [AudioIn(OWN, old, 0, identity, own=True),
                                                         AudioIn(DUB, old, 1, identity)])
        os.replace(old + ".tmp", old)
        _add_item(db_path, "old", 1, plan="replaced", status="completed", filename="e01 1080p.mkv", superseded_by="win")
        place(db_path, "old", season=1, source_id=7, row_key="vk:win" if displaced == "same" else "vk:elsewhere")
    # The voice-over has one row at voices: its track is a file it placed, or it is packed in the old container already.
    sidecar = None
    if placed != "none":
        sidecar = put(media_dir, f"e01 2160p.{DUB}.ru.flac", b"track")
        voices.tracks = [{"dub_id": 5, "season": 1, "episode": 1, "part": 0, "label": DUB, "path": sidecar,
                          "aligned_to": SAME if placed == "same" else OTHER, "ref_uid": "win"}]
    elif displaced != "none":
        voices.tracks = [{"dub_id": 5, "season": 1, "episode": 1, "part": 0, "label": DUB, "path": None,
                          "aligned_to": SAME if displaced == "same" else OTHER, "ref_uid": "old"}]
    fake.references = [{"season": 1, "episode": 1, "part": None, "mark": "by_level", "source_id": 7, "row_key": "vk:win",
                        "player": "vk", "voice_group": OWN, "height": 2160, "reason": None}]
    fake.orders = [{"season": 1, "voices": [{"label": label, "episodes": "1"} for label in order]}]

    reply = await probe.request("downloads.pack", {"series_id": 2}, timeout=10)

    # The rule, told on its own: the dub is in while voices has it aligned to the winner's video and a copy of it is there.
    dub_in = placed == "same" or (placed == "none" and displaced == "same")
    expected = [label for label in order if label == OWN or (label == DUB and dub_in)]
    name, identity, tracks = packer.builds[-1]
    assert reply["packed"] == 1 and identity == SAME and [label for label, _, _ in tracks] == expected
    if dub_in:
        source = dict((label, path) for label, path, _ in tracks)[DUB]
        assert source == (os.path.basename(sidecar) if placed == "same" else "e01 1080p.mkv")  # the fresh one wins
    left = sorted(os.listdir(media_dir))
    assert "e01 2160p.mkv" in left and "e01 2160p.mp4" not in left and "e01 1080p.mkv" not in left
    if sidecar:
        assert (os.path.basename(sidecar) in left) is (placed == "other")  # a track of another video is not the winner's
    items = _items(db_path)
    assert items["win"]["final_filename"] == "e01 2160p.mkv" and items["win"]["status"] == "completed"
    if displaced != "none":
        assert items["old"]["final_filename"] is None
    told = [dub for each in voices.packed for dub in each["dub_ids"]]
    assert told == ([5] if dub_in else [])


@pytest.mark.asyncio
async def test_a_container_that_is_what_the_plan_asks_is_left_alone_and_one_that_is_not_is_rebuilt_from_itself(rig):
    fake, voices, packer, probe, db_path, media_dir = rig
    path = put(media_dir, "e01.mkv", b"https://v/win")
    await packer.build(path + ".tmp", path, SAME, [AudioIn(OWN, path, 0, SAME, own=True), AudioIn(DUB, path, 1, SAME)])
    os.replace(path + ".tmp", path)
    _add_item(db_path, "win", 1, status="completed", filename="e01.mkv")
    place(db_path, "win", season=1, source_id=7, row_key="vk:win")
    voices.tracks = [{"dub_id": 5, "season": 1, "episode": 1, "part": 0, "label": DUB, "path": None,
                      "aligned_to": SAME, "ref_uid": "win"}]
    fake.references = [{"season": 1, "episode": 1, "part": None, "mark": "by_level", "source_id": 7, "row_key": "vk:win",
                        "player": "vk", "voice_group": OWN, "height": 2160, "reason": None}]
    for order, expected in [([OWN, DUB], {"unchanged": 1}), ([DUB, OWN], {"packed": 1}), ([DUB, OWN], {"unchanged": 1})]:
        fake.orders = [{"season": 1, "voices": [{"label": label, "episodes": "1"} for label in order]}]
        reply = await probe.request("downloads.pack", {"series_id": 2}, timeout=10)
        assert {key: value for key, value in reply.items() if value} == expected
    assert [label for label, _, _ in packer.builds[-1][2]] == [DUB, OWN]
    assert sorted(os.listdir(media_dir)) == ["e01.mkv"]
    voices.tracks = []  # the voice-over left the plan: the next packing takes its track out
    await probe.request("downloads.pack", {"series_id": 2}, timeout=10)
    assert [label for label, _, _ in packer.builds[-1][2]] == [OWN]
