"""The reading of the releases of a torrent source (stage 15, module unpack).

The layout (modules/unpack/layout.py) on synthetic releases of the kinds of the corpus of the owner's releases: every file
in one folder with suffixes, sounds in folders by studio, seasons in folders of their own, two videos of one episode; a
placement by hand before a rule; the rows of a release being updated not obtained, of a first download none. Then the
module on the bus with the real rules and real small files made by ffmpeg and read by ffprobe: the preset of the editor
made at the start, the table of the page, the files of the excluded episodes not downloaded."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest

from core import BaseModule, Bus
from core.contracts.torrent import RELEASE_CHANGED_TOPIC, ReleaseFile, ReleaseState, ReleaseView, track_link
from core.contracts import RowTrack
from core.db import Database
from modules.rules import RulesModule
from modules.settings import SettingsModule
from modules.unpack import UnpackModule
from modules.unpack.layout import Inputs, compose, item_of
from schema_db import migrated

URL = "https://kinozal.tv/details.php?id=7"


def release(paths: list[str], state: ReleaseState = ReleaseState.READY, folder: str | None = "/staging/k") -> ReleaseView:
    return ReleaseView(url=URL, release="page", title="Тайтл · 1-12", state=state, folder=folder, infohash="h",
                       files=[ReleaseFile(path=p, size=10, progress=1) for p in paths])


def streams(*specs) -> dict:
    """(kind, index, language, title) of each stream of a file; a video stream is 1080 high."""
    return {"streams": [{"index": i, "kind": k, "codec": "aac" if k == "audio" else "h264", "channels": 2,
                         "height": 1080 if k == "video" else None, "language": lang, "title": title, "filename": "",
                         "mimetype": "", "default": False, "forced": False, "picture": False}
                        for k, i, lang, title in specs], "attachments": [], "chapters": 0, "reason": None}


EXCLUDE = "exclude"


def numbered(paths: dict[str, tuple[int, int] | str | None]) -> dict:
    """The results of the tab «Файлы» as the preset would give them: season and episode by item, `EXCLUDE` for a file a
    rule excludes, None for a file no rule gave a number."""
    out = {}
    for path, value in paths.items():
        if value == EXCLUDE:
            out[item_of("page", path)] = {"excluded": True}
        elif value is not None:
            out[item_of("page", path)] = {"season": value[0], "episode": value[1]}
    return out


def test_every_track_of_a_placed_video_and_of_a_sound_beside_it_is_a_row_with_that_video():
    r = release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka", "Show/Show - 01.rus.ass"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show - 01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")),
                                      "Show/Show - 01.rus.mka": streams(("audio", 0, "rus", "")),
                                      "Show/Show - 01.rus.ass": streams(("subtitle", 0, "rus", "Надписи"))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, rows = compose(inputs)
    # The link names the n-th track of its kind in its file (`-map <file>:a:<n>`, `:s:<n>`), not the index of the stream; a
    # row of subtitles says so (DESIGN.md 7.1.1) and is no voice-over.
    assert [(row.key, row.voice.name, row.voice.kind, row.track, row.link) for row in rows] == [
        ("page/Show/Show - 01.mkv#1", "Original", "audio", RowTrack.SOUND, track_link("page", "Show/Show - 01.mkv", 0)),
        ("page/Show/Show - 01.rus.mka#0", "", "audio", RowTrack.SOUND,
         track_link("page", "Show/Show - 01.mkv", 0, file="Show/Show - 01.rus.mka")),
        ("page/Show/Show - 01.rus.ass#0", "Надписи", "subtitles", RowTrack.SUBTITLES,
         track_link("page", "Show/Show - 01.mkv", 0, file="Show/Show - 01.rus.ass"))]
    assert rows[2].language == "rus" and rows[2].subtitle_type == ""
    assert all(row.heights == [1080] for row in rows)
    assert all(row.text == "S01E01 | Show/Show - 01.mkv" and row.player == "torrent:page" for row in rows)
    assert {row.timeline for row in rows} == {"page/Show/Show - 01.mkv"}  # one video, one timeline, whatever file a track is in
    assert view["unplaced"] == {"files": 0, "voices": 1, "subtitles": 1, "streams": 1}


def test_a_folder_a_rule_gives_names_the_track_and_the_users_folder_goes_before_it():
    r = release(["Show/E01.mkv", "Show/Rus Audio/JAM/E01.mka", "Show/Rus Audio/Studio/E01.mka"])
    inputs = Inputs(releases=[r], folders=[{"kind": "voice", "name": "Студия", "language": "rus", "subtitle_type": ""}],
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", "")),
                                      "Show/Rus Audio/JAM/E01.mka": streams(("audio", 0, "", "")),
                                      "Show/Rus Audio/Studio/E01.mka": streams(("audio", 0, "", ""))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files}),
                             "voices": {item_of("page", "Show/Rus Audio/JAM/E01.mka"): {"voice_folder": "JAM",
                                                                                         "events": [{"rule": "студии"}]},
                                        item_of("page", "Show/Rus Audio/Studio/E01.mka"): {"voice_folder": "Studio"}}},
                    manual={"voices": {item_of("page", "Show/Rus Audio/Studio/E01.mka"): {
                        "folder": "Студия", "excluded": False}}})
    view, rows = compose(inputs)
    assert [(row.voice.name, row.site_fields["language"]) for row in rows] == [("JAM", ""), ("Студия", "rus")]
    voices = {entry["item"]: entry for entry in view["tabs"]["voices"]}
    assert voices[item_of("page", "Show/Rus Audio/JAM/E01.mka")]["by"] == "студии"
    assert voices[item_of("page", "Show/Rus Audio/Studio/E01.mka")]["by"] == "manual"
    assert [(f["name"], f["items"], f["manual"]) for f in view["folders"]] == [("JAM", 1, False), ("Студия", 1, True)]


def test_seasons_in_folders_of_their_own_place_their_files_and_a_track_of_another_season_is_not_taken():
    r = release(["Show/Season 1/E01.mkv", "Show/Season 2/E01.mkv", "Show/Season 2/E01.mka"])
    inputs = Inputs(releases=[r], folders=[], manual={},
                    streams={"page": {p.path: streams(("video", 0, "", ""), ("audio", 1, "jpn", "")) if p.path.endswith("mkv")
                                      else streams(("audio", 0, "rus", "")) for p in r.files}},
                    results={"files": numbered({"Show/Season 1/E01.mkv": (1, 1), "Show/Season 2/E01.mkv": (2, 1),
                                                "Show/Season 2/E01.mka": (2, 1)})})
    _view, rows = compose(inputs)
    assert [(row.text.split(" | ")[0], row.key) for row in rows] == [
        ("S01E01", "page/Show/Season 1/E01.mkv#1"), ("S02E01", "page/Show/Season 2/E01.mkv#1"),
        ("S02E01", "page/Show/Season 2/E01.mka#0")]


def test_a_sound_of_an_episode_with_two_videos_waits_for_the_user():
    r = release(["Show/E01.mkv", "Show/E01 v2.mkv", "Show/E01.mka"])
    inputs = Inputs(releases=[r], folders=[], manual={},
                    streams={"page": {p.path: streams(("video", 0, "", "")) if p.path.endswith("mkv")
                                      else streams(("audio", 0, "rus", "")) for p in r.files}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, rows = compose(inputs)
    assert rows == [] and view["unplaced"]["voices"] == 1


def test_a_placement_by_hand_goes_before_the_rule_and_does_not_reach_other_files():
    r = release(["Show/a.mkv", "Show/b.mkv", "Show/extra.mkv"])
    inputs = Inputs(releases=[r], folders=[],
                    streams={"page": {p.path: streams(("video", 0, "", ""), ("audio", 1, "", "")) for p in r.files}},
                    results={"files": numbered({"Show/a.mkv": (1, 1), "Show/b.mkv": (1, 2), "Show/extra.mkv": None})},
                    manual={"files": {item_of("page", "Show/a.mkv"): {"season": 1, "episode": 5, "episode_last": None,
                                                                      "excluded": False},
                                      item_of("page", "Show/b.mkv"): {"season": None, "episode": None, "excluded": True}}})
    view, rows = compose(inputs)
    assert [row.text for row in rows] == ["S01E05 | Show/a.mkv"]
    files = {entry["path"]: entry for entry in view["tabs"]["files"]}
    assert files["Show/b.mkv"]["excluded"] and files["Show/extra.mkv"]["place"] is None
    assert view["unplaced"]["files"] == 1


def test_a_release_being_updated_gives_its_rows_not_obtained_and_a_first_download_none():
    paths = ["Show/E01.mkv"]
    probed = {"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", ""))}}
    results = {"files": numbered({"Show/E01.mkv": (1, 1)})}
    _v, updating = compose(Inputs([release(paths, ReleaseState.UPDATING)], probed, results, {}))
    _v, first = compose(Inputs([release(paths, ReleaseState.DOWNLOADING)], probed, results, {}))
    assert [(row.state.value, json.loads(row.reason)["code"]) for row in updating] == [("not_obtained", "torrent_updating")]
    assert first == []


# --- the module on the bus -------------------------------------------------------------------------------------------

def _file(path: str, *, video: bool, audios: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y"]
    if video:
        args += ["-f", "lavfi", "-i", "color=c=blue:s=64x36:d=1"]
    for _ in audios:
        args += ["-f", "lavfi", "-i", "sine=f=440:d=1"]
    for i in range(len(audios) + (1 if video else 0)):
        args += ["-map", str(i)]
    for i, lang in enumerate(audios):
        args += [f"-metadata:s:a:{i}", f"language={lang}"]
    subprocess.run(args + ["-c:a", "aac", "-shortest", path], check=True)


class Torrents(BaseModule):
    name = "torrents"

    def __init__(self, bus) -> None:
        super().__init__(bus)
        self.releases: list[ReleaseView] = []
        self.skips: list[dict] = []

    def register(self) -> None:
        self.handle("torrents.releases.get", self.on_releases)
        self.handle("torrents.release.priorities", self.on_priorities)
        for topic in ("catalog.series.list", "sources.site.resolve"):
            self.handle(topic, getattr(self, "on_" + topic.split(".")[-1]))

    async def on_releases(self, env):
        return {"releases": [r.model_dump(mode="json") for r in self.releases]}

    async def on_priorities(self, env):
        self.skips.append(env.payload)

    async def on_list(self, env):
        return [{"id": 1, "sources": [{"url": URL, "source_type": "torrent"}], "ignored_seasons": "[2]",
                 "ignored_episodes": "{}"}]

    async def on_resolve(self, env):
        # The page written with a mirror of the tracker is the page of its own domain.
        return {"canonical_url": env.payload["url"].replace("kinozal.me", "kinozal.tv")}


@pytest.fixture
async def bus_stand(tmp_path):
    bus = Bus()
    db = Database(str(migrated(tmp_path / "u.db")))
    modules = [SettingsModule(bus, db), RulesModule(bus, db), Torrents(bus), UnpackModule(bus, db)]
    for module in modules:
        await module.start()
    yield modules[2], modules[3], bus, tmp_path
    for module in reversed(modules):
        await module.stop()


async def test_the_module_makes_its_preset_reads_a_ready_release_and_answers_the_table_of_the_page(bus_stand):
    torrents, unpack, bus, root = bus_stand
    folder = str(root / "staging" / "kinozal-1")
    _file(f"{folder}/Show/Show - 01.mkv", video=True, audios=["jpn", "rus"])
    _file(f"{folder}/Show/Show - 02.mkv", video=True, audios=["jpn"])
    ready = release(["Show/Show - 01.mkv", "Show/Show - 02.mkv", "Show/readme.nfo"], folder=folder)
    torrents.releases = [ready]
    changed = bus.subscribe("unpack.table.changed")
    progress = bus.subscribe("unpack.read.progress")

    assert unpack._files_profile is not None  # the preset is made at the start, with its rules
    unpack.publish_event(RELEASE_CHANGED_TOPIC, ready.model_dump(mode="json"))
    assert (await asyncio.wait_for(changed.queue.get(), 10)).payload == {"url": URL}
    # The read of the version told file by file (the nfo is no file to read), and the editor holds no read any more.
    told = []
    while not progress.queue.empty():
        told.append((lambda each: (each["done"], each["total"]))(progress.queue.get_nowait().payload))
    assert told == [(0, 2), (1, 2), (2, 2)]
    table = await torrents.request("unpack.table.get", {"url": URL})
    assert table["names"] == ["Тайтл · 1-12"]
    assert [(row["key"], row["text"], row["site_fields"]["language"]) for row in table["rows"]] == [
        ("page/Show/Show - 01.mkv#1", "S01E01 | Show/Show - 01.mkv", "jpn"),
        ("page/Show/Show - 01.mkv#2", "S01E01 | Show/Show - 01.mkv", "rus"),
        ("page/Show/Show - 02.mkv#1", "S01E02 | Show/Show - 02.mkv", "jpn")]
    assert all(row["heights"] == [36] for row in table["rows"])

    editor = await torrents.request("unpack.editor.get", {"url": URL})
    files = {entry["path"]: entry for entry in editor["view"]["tabs"]["files"]}
    assert files["Show/readme.nfo"]["excluded"] and files["Show/Show - 02.mkv"]["by"] == "серия после тире"
    assert set(editor["profiles"]) == {"files", "voices", "subtitles", "streams"}
    assert [each["reading"] for each in editor["view"]["releases"]] == [None]


async def test_files_dropped_on_a_season_take_its_episodes_from_the_one_given_in_the_order_sent(bus_stand):
    torrents, unpack, bus, root = bus_stand
    ova = ["Bonus/OVA 1.mkv", "Bonus/OVA 2.mkv", "Bonus/OVA 3.mkv"]
    torrents.releases = [release(ova)]
    await torrents.request("unpack.assign", {"url": URL, "tab": "files", "items": [item_of("page", p) for p in ova],
                                             "season": 0, "episode": 4, "episode_last": 9, "in_order": True})
    editor = await torrents.request("unpack.editor.get", {"url": URL})
    assert [(e["path"], e["place"], e["by"]) for e in editor["view"]["tabs"]["files"]] == [
        (path, {"season": 0, "episode": 4 + n, "last": None}, "manual") for n, path in enumerate(ova)]


async def test_a_page_written_with_a_mirror_is_the_page_of_the_trackers_own_domain_in_the_editor(bus_stand):
    torrents, unpack, bus, root = bus_stand
    torrents.releases = [release(["Show/Show - 01.mkv"])]
    mirror = URL.replace("kinozal.tv", "kinozal.me")
    await torrents.request("unpack.assign", {"url": mirror, "tab": "files", "items": [item_of("page", "Show/Show - 01.mkv")],
                                             "season": 2, "episode": 7})
    await torrents.request("unpack.folder.set", {"url": mirror, "kind": "voice", "name": "Студия"})
    by_mirror = await torrents.request("unpack.editor.get", {"url": mirror})
    by_page = await torrents.request("unpack.editor.get", {"url": URL})
    assert by_mirror["url"] == by_page["url"] == URL and by_mirror["profiles"] == by_page["profiles"]
    assert by_page["view"]["tabs"]["files"][0]["place"] == {"season": 2, "episode": 7, "last": None}
    assert [f["name"] for f in by_page["view"]["folders"]] == ["Студия"]


async def test_the_files_of_a_season_every_series_with_the_page_excludes_are_not_downloaded(bus_stand):
    torrents, unpack, bus, root = bus_stand
    downloading = release(["Show/Season 1/Show - 01.mkv", "Show/Season 2/Show - 01.mkv"], state=ReleaseState.DOWNLOADING)
    torrents.releases = [downloading]
    unpack.publish_event(RELEASE_CHANGED_TOPIC, downloading.model_dump(mode="json"))

    async def told():
        while not torrents.skips:
            await asyncio.sleep(0.01)
    await asyncio.wait_for(told(), 10)
    assert torrents.skips == [{"url": URL, "release": "page", "skip": ["Show/Season 2/Show - 01.mkv"]}]


def test_the_fonts_of_subtitles_are_those_beside_them_else_of_the_nearest_folder_above_that_has_any():
    from core.contracts.torrent import fonts_of
    paths = ["Show/sub/Cafe/E01.ass", "Show/sub/Cafe/fonts/a.ttf", "Show/sub/Shift/E01.ass", "Show/Fonts/b.otf",
             "Show/Fonts/c.TTF", "Show/E01.mkv"]
    assert fonts_of(paths, "Show/sub/Cafe/E01.ass") == ["Show/sub/Cafe/fonts/a.ttf"]
    # Nothing in its folder: the nearest folder above that has fonts gives them (`sub` has those of Cafe below it).
    assert fonts_of(paths, "Show/sub/Shift/E01.ass") == ["Show/sub/Cafe/fonts/a.ttf"]
    assert fonts_of(["Show/Subs/E01.ass", "Show/Fonts/b.otf"], "Show/Subs/E01.ass") == ["Show/Fonts/b.otf"]
    assert fonts_of(["E01.ass"], "E01.ass") == []


def test_a_folder_of_subtitles_names_the_fonts_its_files_go_with_and_a_folder_of_a_voice_over_none():
    paths = ["Show/E01.mkv", "Show/E02.mkv", "Show/sub/Cafe/E01.ass", "Show/sub/Cafe/E02.ass", "Show/sub/Cafe/fonts/a.ttf",
             "Show/sub/Shift/E01.ass", "Show/Fonts/b.otf", "Show/Audio/JAM/E01.mka"]
    r = release(paths)
    places = {"Show/E01.mkv": (1, 1), "Show/E02.mkv": (1, 2), "Show/sub/Cafe/E01.ass": (1, 1), "Show/sub/Cafe/E02.ass": (1, 2),
              "Show/sub/Shift/E01.ass": (1, 1), "Show/Audio/JAM/E01.mka": (1, 1)}
    inputs = Inputs(releases=[r], manual={}, folders=[], streams={},
                    results={"files": numbered(places),
                             "subtitles": {item_of("page", p): {"subtitle_folder": p.split("/")[2]}
                                           for p in paths if p.endswith(".ass")},
                             "voices": {item_of("page", "Show/Audio/JAM/E01.mka"): {"voice_folder": "JAM"}}})
    view, _rows = compose(inputs)
    assert [(f["kind"], f["name"], f["fonts"]) for f in view["folders"]] == [
        ("subtitles", "Cafe", ["Show/sub/Cafe/fonts/a.ttf"]), ("subtitles", "Shift", ["Show/sub/Cafe/fonts/a.ttf"]),
        ("voice", "JAM", [])]


def test_the_editor_tells_the_file_episode_and_stream_of_every_track_and_what_every_video_holds():
    r = release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show - 01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")),
                                      "Show/Show - 01.rus.mka": streams(("audio", 0, "rus", ""))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, _rows = compose(inputs)
    stream = view["tabs"]["streams"][0]
    assert (stream["path"], stream["place"], stream["stream"]["index"], stream["stream"]["language"]) == (
        "Show/Show - 01.mkv", {"season": 1, "episode": 1, "last": None}, 1, "jpn")
    voice = view["tabs"]["voices"][0]
    assert (voice["path"], voice["place"]["episode"]) == ("Show/Show - 01.rus.mka", 1)
    assert [(each["path"], [s["kind"] for s in each["streams"]]) for each in view["containers"]] == [
        ("Show/Show - 01.mkv", ["video", "audio"])]
