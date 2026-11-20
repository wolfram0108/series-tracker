"""The file lists of the releases of the add form: the command `torrents.preview.fetch` does what the gateway used to
do between three modules, and the module is the only one that publishes the outcomes."""
import asyncio
import base64

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.runner import Runner
from core.db import Database
from core.reasons import parse
from modules.catalog import CatalogModule
from modules.torrents import TorrentsModule

MAGNET = "magnet:?xt=urn:btih:" + "ab" * 20


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b"i%de" % value
    if isinstance(value, bytes):
        return b"%d:%s" % (len(value), value)
    if isinstance(value, list):
        return b"l" + b"".join(bencode(each) for each in value) + b"e"
    return b"d" + b"".join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b"e"


def torrent_of(name: bytes, files: list[tuple[list[bytes], int]]) -> str:
    info = {b"name": name, b"piece length": 16384, b"pieces": b"\x00" * 20,
            b"files": [{b"length": size, b"path": path} for path, size in files]}
    return base64.b64encode(bencode({b"announce": b"http://t.test/a", b"info": info})).decode()


class FakeSources(BaseModule):
    """Gives the .torrent behind a link, as `sources.torrent_file.get` does."""
    name = "sources"

    def __init__(self, bus) -> None:
        self.files: dict[str, object] = {}
        self.asked: list[dict] = []
        super().__init__(bus)

    def register(self) -> None:
        self.handle("sources.torrent_file.get", self.on_get)

    async def on_get(self, env):
        self.asked.append(env.payload)
        found = self.files[env.payload["url"]]
        if isinstance(found, Exception):
            raise found
        return {"content_b64": found, "from_cache": False}


class FakeQbt:
    """Just what a preview of a magnet and the start of the module touch."""

    def __init__(self) -> None:
        self.added: list[tuple] = []
        self.deleted: list[str] = []
        self.live: set[str] = set()
        self.files: dict[str, list[dict]] = {}

    async def torrents_info(self, hashes=None, *, category=None):
        return [{"hash": h} for h in self.live if hashes is None or h in hashes]

    async def create_category(self, name, save_path=""):
        pass

    async def default_save_path(self):
        return "/downloads"

    async def add_magnet(self, magnet, save_path, *, paused=True, category=None):
        self.added.append((magnet, save_path, paused, category))
        self.live.add(magnet.split("btih:")[1])

    async def torrent_files(self, torrent_hash):
        return self.files.get(torrent_hash, [])

    async def delete(self, hashes, *, delete_files):
        self.deleted.extend(hashes)
        self.live -= set(hashes)

    async def login(self):
        pass

    async def close(self):
        pass


class Probe(BaseModule):
    name = "probe"



@pytest.fixture
async def system(db_path):
    bus = Bus()
    db = Database(db_path)
    sources, qbt, probe = FakeSources(bus), FakeQbt(), Probe(bus)
    runner = Runner(bus, [sources, CatalogModule(bus, db), TorrentsModule(bus, db, qbt=qbt, pipeline_poll=0.02,
                                                                            monitor_active=0.05, monitor_idle=0.2),
                          probe])
    await runner.start()
    outcomes = bus.subscribe("torrents.preview.*")
    yield bus, sources, qbt, probe, outcomes
    await runner.stop()


async def outcome(outcomes) -> tuple[str, dict]:
    """The next event of the family; the command that asks for the previews is in the family's topics too."""
    while True:
        env = await asyncio.wait_for(outcomes.queue.get(), 5)
        if env.kind == "event":
            return env.topic, env.payload


@pytest.mark.asyncio
async def test_a_file_that_is_no_torrent_fails_with_the_reason_of_the_parse(system):
    _, sources, _, probe, outcomes = system
    sources.files["https://t.test/3.torrent"] = base64.b64encode(b"<html>login</html>").decode()
    probe.send_command("torrents.preview.fetch", {"items": [{"torrent_id": "t3", "link": "https://t.test/3.torrent"}]})
    topic, payload = await outcome(outcomes)
    assert topic == "torrents.preview.failed" and payload["torrent_id"] == "t3"
    assert parse(payload["reason"])["code"] == "preview_torrent_unreadable"


@pytest.mark.asyncio
async def test_a_magnet_gets_its_files_through_qbit_and_leaves_nothing_there(system):
    _, _, qbt, probe, outcomes = system
    ih = "ab" * 20
    qbt.files[ih] = [{"name": "Show/e01.mkv", "size": 7}]
    probe.send_command("torrents.preview.fetch", {"items": [{"torrent_id": "m1", "magnet": MAGNET}]})
    topic, payload = await outcome(outcomes)
    assert topic == "torrents.preview.ready"
    assert payload == {"torrent_id": "m1", "infohash": ih, "name": None, "files": [{"path": "Show/e01.mkv", "size": 7}]}
    assert qbt.added == [(MAGNET, "/downloads", False, "st-naming-preview")]
    assert qbt.deleted == [ih] and qbt.live == set()


@pytest.mark.asyncio
async def test_the_releases_are_answered_in_order_and_each_one_ends_on_its_own(system):
    _, sources, _, probe, outcomes = system
    sources.files["https://t.test/a.torrent"] = torrent_of(b"A", [([b"a.mkv"], 1)])
    sources.files["https://t.test/b.torrent"] = ValueError("нет")
    sources.files["https://t.test/c.torrent"] = torrent_of(b"C", [([b"c.mkv"], 3)])
    probe.send_command("torrents.preview.fetch", {"items": [
        {"torrent_id": "a", "link": "https://t.test/a.torrent"}, {"torrent_id": "b", "link": "https://t.test/b.torrent"},
        {"torrent_id": "c", "link": "https://t.test/c.torrent"}]})
    seen = [await outcome(outcomes) for _ in range(3)]
    assert [(topic.rsplit(".", 1)[1], payload["torrent_id"]) for topic, payload in seen] == [
        ("ready", "a"), ("failed", "b"), ("ready", "c")]
