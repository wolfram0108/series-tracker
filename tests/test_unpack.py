"""The reading of the releases of a torrent source (stage 15, module unpack).

The layout (calc/unpack/layout.py) on synthetic releases of the kinds of the corpus of the owner's releases: every file
in one folder with suffixes, sounds in folders by studio, seasons in folders of their own, two videos of one episode; a
placement by hand before a rule; the rows of a release being updated not obtained, of a first download none. Then the
module on the bus with the real rules and real small files made by ffmpeg and read by ffprobe: the preset of the editor
made at the start, the table of the page. The editor places and never leaves out: a red item (no episode, no folder, no
language) is no row, and every file of a release is downloaded — nothing tells `torrents` otherwise."""
from __future__ import annotations

import json
import subprocess

import pytest

from core.bus import Bus
from core.module import BaseModule
from core.ffprobe import subtitle_events
from core.envelope import Envelope
from core.contracts import RowTrack, SeriesListReply, SourcesReply
from series_factory import series_card
from core.contracts.torrent import ReleaseFile, ReleaseState, ReleaseView
from core.db import Database
from modules.rules import RulesModule
from modules.settings import SettingsModule
from modules.unpack import UnpackModule
from calc.unpack.layout import Inputs, compose, item_of, lines
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


def numbered(paths: dict[str, tuple[int, int] | None]) -> dict:
    """The results of the tab «Файлы» as its rules would give them: season and episode by item, None for a file no rule
    gave a number."""
    out = {}
    for path, value in paths.items():
        if value is not None:
            out[item_of("page", path)] = {"season": value[0], "episode": value[1]}
    return out


def test_a_sound_of_an_episode_with_two_videos_waits_for_the_user():
    r = release(["Show/E01.mkv", "Show/E01 v2.mkv", "Show/E01.mka"])
    inputs = Inputs(releases=[r], folders=[], manual={},
                    streams={"page": {p.path: streams(("video", 0, "", "")) if p.path.endswith("mkv")
                                      else streams(("audio", 0, "rus", "")) for p in r.files}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, rows = compose(inputs)
    assert rows == [] and view["unplaced"]["voices"] == 1


def test_a_subtitles_track_with_no_events_is_no_row_and_the_others_keep_their_numbers():
    r = release(["Show/E01.mkv"])
    held = streams(("video", 0, "", ""), ("subtitle", 1, "rus", "Надписи"), ("subtitle", 2, "rus", "Полные"),
                   ("subtitle", 3, "rus", "Не считана"))
    held["streams"][1]["events"], held["streams"][2]["events"] = 0, 641  # the third was never counted: it is kept
    inputs = Inputs(releases=[r], folders=[], manual={}, streams={"page": {"Show/E01.mkv": held}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1)})})
    view, rows = compose(inputs)
    shown = [(row.voice.name, row.link.rsplit("#", 1)[1]) for row in rows if row.track == RowTrack.SUBTITLES]
    assert shown == [("Полные", "1"), ("Не считана", "2")]
    # The editor does not lay the empty track out either: not in the tab «Суб. конт.», not among the streams of the file.
    assert [e["stream"]["title"] for e in view["tabs"]["subtitle_streams"]] == ["Полные", "Не считана"]
    assert [s["title"] for s in view["containers"][0]["streams"] if s["kind"] == "subtitle"] == ["Полные", "Не считана"]
    assert sorted(lines(inputs)["subtitle_streams"]) == [item_of("page", "Show/E01.mkv", 2), item_of("page", "Show/E01.mkv", 3)]


ASS_HEAD = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
            "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, "
            "MarginV, Encoding\nStyle: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,"
            "10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")


async def test_the_events_of_each_subtitles_track_of_a_real_file_are_counted_and_a_header_only_track_has_none(tmp_path):
    (tmp_path / "empty.ass").write_text(ASS_HEAD, encoding="utf-8")
    lines = "".join(f"Dialogue: 0,0:00:0{n}.00,0:00:0{n}.50,Default,,0,0,0,,line {n}\n" for n in range(1, 4))
    (tmp_path / "full.ass").write_text(ASS_HEAD + lines, encoding="utf-8")
    made = tmp_path / "both.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(tmp_path / "empty.ass"), "-i", str(tmp_path / "full.ass"),
                    "-map", "0", "-map", "1", "-c", "copy", str(made)], check=True)
    assert await subtitle_events(str(made)) == {0: 0, 1: 3}  # in the order of the streams, the empty one counted as 0
    assert await subtitle_events(str(tmp_path / "missing.mkv")) is None


# --- the module on the bus -------------------------------------------------------------------------------------------

TITLES = {"jpn": "Original", "rus": "Студия"}


class Torrents(BaseModule):
    name = "torrents"

    def __init__(self, bus) -> None:
        super().__init__(bus)
        self.releases: list[ReleaseView] = []
        self.listed = True  # a series lists the page as its torrent source

    def register(self) -> None:
        self.handle("torrents.releases.get", self.on_releases)
        self.handle("catalog.sources.by_profile", self.on_by_profile)
        for topic in ("catalog.series.list", "sources.site.resolve"):
            self.handle(topic, getattr(self, "on_" + topic.split(".")[-1]))

    async def on_by_profile(self, env):
        return SourcesReply(sources=())

    async def on_releases(self, env):
        return {"releases": [r.model_dump(mode="json") for r in self.releases]}

    async def on_list(self, env):
        return SeriesListReply(series=(series_card(
            id=1, sources=[{"url": URL}] if self.listed else [], ignored_seasons="[2]", ignored_episodes="{}"),))

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


async def _rule(torrents, profile_id: int, name: str, blocks: list[dict]) -> None:
    """A rule the owner writes in a tab of the editor: the number after the blocks is the episode."""
    action = [{"action_type": "extract_single", "action_pattern": json.dumps(blocks, ensure_ascii=False)}]
    await torrents.request("rules.rules.add", {"profile_id": profile_id, "data": {
        "name": name, "continue_after_match": True, "action_pattern": json.dumps(action, ensure_ascii=False), "conditions": []}})


async def test_a_page_no_source_lists_is_forgotten_with_the_profiles_of_its_tabs(bus_stand):
    torrents, unpack, bus, root = bus_stand
    torrents.releases = [release(["Show/Show - 01.mkv"])]
    profiles = (await torrents.request("unpack.editor.open", {"url": URL}))["profiles"]
    await torrents.request("unpack.folder.set", {"url": URL, "tab": "voices", "name": "Студия"})
    torrents.listed = False
    await unpack.on_source_changed(Envelope(topic="series.source.removed", kind="event", payload={"series_id": 1, "source_id": 1}))
    assert await unpack.repo.pages() == []
    left = {p["id"] for p in await torrents.request("rules.profiles.list", {"kind": "torrent"})}
    assert not left & set(profiles.values())


async def test_a_folder_deleted_takes_the_hands_placements_in_it_and_auto_makes_the_automatic_ones_again(bus_stand):
    torrents, unpack, bus, root = bus_stand
    torrents.releases = [release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka"])]
    sound = item_of("page", "Show/Show - 01.rus.mka")
    # «Файлы» gives both files their episode: the sound goes on to «Озвучки».
    await torrents.request("unpack.assign", {"url": URL, "tab": "files", "season": 1, "episode": 1,
                                             "items": [item_of("page", "Show/Show - 01.mkv"), sound]})
    await torrents.request("unpack.folder.set", {"url": URL, "tab": "voices", "name": "Студия", "language": "rus"})
    await torrents.request("unpack.assign", {"url": URL, "tab": "voices", "items": [sound], "folder": "Студия"})
    await torrents.request("unpack.folder.delete", {"url": URL, "tab": "voices", "name": "Студия", "automatic": True,
                                                     "items": [sound]})
    editor = await torrents.request("unpack.editor.get", {"url": URL})
    assert editor["view"]["folders"] == [] and editor["view"]["hidden"] == [{"tab": "voices", "name": "Студия"}]
    assert [each for each in await unpack.repo.assignments(URL) if each["tab"] == "voices"] == []
    await torrents.request("unpack.auto", {"url": URL, "tabs": ["voices"]})
    assert (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["hidden"] == []
    # «−» takes a track out — red; «Авто» gives it back to the automatic layout; a hand's placement clears it too.
    await torrents.request("unpack.remove", {"url": URL, "tab": "voices", "items": [sound]})
    assert [(e["folder"], e["waiting"]) for e in (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["tabs"]["voices"]] == [
        (None, True)]
    await torrents.request("unpack.auto", {"url": URL, "tabs": ["voices"]})
    assert (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["removed"] == []
    await torrents.request("unpack.remove", {"url": URL, "tab": "voices", "items": [sound]})
    await torrents.request("unpack.assign", {"url": URL, "tab": "voices", "items": [sound], "folder": "Студия"})
    assert (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["removed"] == []
    await torrents.request("unpack.unassign", {"url": URL, "tab": "voices", "items": [sound]})
    # A folder only the hand made is not hidden when deleted; one the user names again is his: nothing hides it.
    await torrents.request("unpack.folder.delete", {"url": URL, "tab": "voices", "name": "Студия"})
    assert (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["hidden"] == []
    await torrents.request("unpack.folder.delete", {"url": URL, "tab": "voices", "name": "Студия", "automatic": True})
    await torrents.request("unpack.folder.set", {"url": URL, "tab": "voices", "name": "Студия"})
    assert (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["hidden"] == []
