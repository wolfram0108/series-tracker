"""The reading of the releases of a torrent source (stage 15, module unpack).

The layout (modules/unpack/layout.py) on synthetic releases of the kinds of the corpus of the owner's releases: every file
in one folder with suffixes, sounds in folders by studio, seasons in folders of their own, two videos of one episode; a
placement by hand before a rule; the rows of a release being updated not obtained, of a first download none. Then the
module on the bus with the real rules and real small files made by ffmpeg and read by ffprobe: the preset of the editor
made at the start, the table of the page. The editor places and never leaves out: a red item (no episode, no folder, no
language) is no row, and every file of a release is downloaded — nothing tells `torrents` otherwise."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest

from core import BaseModule, Bus
from core.envelope import Envelope
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


def numbered(paths: dict[str, tuple[int, int] | None]) -> dict:
    """The results of the tab «Файлы» as its rules would give them: season and episode by item, None for a file no rule
    gave a number."""
    out = {}
    for path, value in paths.items():
        if value is not None:
            out[item_of("page", path)] = {"season": value[0], "episode": value[1]}
    return out


def test_every_placed_track_of_a_placed_video_and_of_a_file_beside_it_is_a_row_with_that_video_and_a_red_one_none():
    r = release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka", "Show/Show - 01.rus.ass"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show - 01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")),
                                      "Show/Show - 01.rus.mka": streams(("audio", 0, "rus", "")),
                                      "Show/Show - 01.rus.ass": streams(("subtitle", 0, "rus", "Надписи"))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, rows = compose(inputs)
    # The link names the n-th track of its kind in its file (`-map <file>:a:<n>`, `:s:<n>`), not the index of the stream; a
    # row of subtitles says so (DESIGN.md 7.1.1) and is no voice-over. The sound file has no title: it is in no folder,
    # red, and goes no further — no row.
    assert [(row.key, row.voice.name, row.voice.kind, row.track, row.link) for row in rows] == [
        ("page/Show/Show - 01.mkv#1", "Original", "audio", RowTrack.SOUND, track_link("page", "Show/Show - 01.mkv", 0)),
        ("page/Show/Show - 01.rus.ass#0", "Надписи", "subtitles", RowTrack.SUBTITLES,
         track_link("page", "Show/Show - 01.mkv", 0, file="Show/Show - 01.rus.ass"))]
    assert rows[1].language == "rus" and rows[1].subtitle_type == ""
    assert all(row.heights == [1080] for row in rows)
    assert all(row.text == "S01E01 | Show/Show - 01.mkv" and row.player == "torrent:page" for row in rows)
    assert {row.timeline for row in rows} == {"page/Show/Show - 01.mkv"}  # one video, one timeline, whatever file a track is in
    # The titles the file gives make the folders by themselves; the sound file has none and waits for the operator.
    assert view["unplaced"] == {"files": 0, "voices": 1, "subtitles": 0, "voice_streams": 0, "subtitle_streams": 0}


def test_the_tabs_are_a_chain_a_file_files_left_red_goes_neither_to_voices_nor_to_subtitles():
    """What «Файлы» placed goes on to the next tabs; a file it left without an episode is red there and goes no further. A
    file placed in an episode no single video has passed «Файлы»: it is listed, waiting."""
    r = release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka", "Show/Show - 01.rus.ass",
                 "Show/Show - 02.rus.mka", "Show/Show - 02.rus.ass", "Show/Show - 03.rus.mka"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show - 01.mkv": streams(("video", 0, "", "")),
                                      **{p: streams(("audio" if p.endswith("mka") else "subtitle", 0, "rus", "Студия"))
                                         for p in ("Show/Show - 01.rus.mka", "Show/Show - 01.rus.ass", "Show/Show - 02.rus.mka",
                                                   "Show/Show - 02.rus.ass", "Show/Show - 03.rus.mka")}}},
                    results={"files": numbered({"Show/Show - 01.mkv": (1, 1), "Show/Show - 01.rus.mka": (1, 1),
                                                "Show/Show - 01.rus.ass": (1, 1), "Show/Show - 03.rus.mka": (1, 3)})})
    view, _rows = compose(inputs)
    assert [e["path"] for e in view["tabs"]["voices"]] == ["Show/Show - 01.rus.mka", "Show/Show - 03.rus.mka"]
    assert [e["path"] for e in view["tabs"]["subtitles"]] == ["Show/Show - 01.rus.ass"]
    assert view["unplaced"]["files"] == 2


def ruled(numbers: dict[str, tuple[int, int, int, str, str]]) -> dict:
    """The results of «Файлы» as rules in their order give them: path → (season, episode, order, rule, tag)."""
    return {item_of("page", path): {"season": season, "episode": episode, **({"video_tag": tag} if tag else {}),
                                    "events": [{"rule": rule, "order": order, "action": "extract",
                                                "extracted": {"episode": episode}}]}
            for path, (season, episode, order, rule, tag) in numbers.items()}


def test_an_episode_is_the_first_sources_and_what_a_later_one_put_in_it_is_red_with_the_conflict_told():
    """«Директор» (first) and «Серия» (second) both give episode 21: the director's cut takes it, the plain video, its sound
    and subtitles are red, the conflict told; one rule giving two videos one episode leaves both red."""
    r = release(["Show/Show 21Dir.mkv", "Show/Show 21Dir.rus.mka", "Show/Show 21.mkv", "Show/Show 21.rus.mka",
                 "Show/Show 21.rus.ass", "Show/Show 22.mkv", "Show/Show 22 (v2).mkv"])
    inputs = Inputs(releases=[r], manual={}, folders=[], streams={},
                    results={"files": ruled({"Show/Show 21Dir.mkv": (1, 21, 0, "Директор", ""),
                                             "Show/Show 21Dir.rus.mka": (1, 21, 0, "Директор", ""),
                                             "Show/Show 21.mkv": (1, 21, 1, "Серия", ""),
                                             "Show/Show 21.rus.mka": (1, 21, 1, "Серия", ""),
                                             "Show/Show 21.rus.ass": (1, 21, 1, "Серия", ""),
                                             "Show/Show 22.mkv": (1, 22, 1, "Серия", ""),
                                             "Show/Show 22 (v2).mkv": (1, 22, 1, "Серия", "")})})
    view, _rows = compose(inputs)
    files = {entry["path"]: entry for entry in view["tabs"]["files"]}
    assert files["Show/Show 21Dir.mkv"]["place"] == {"season": 1, "episode": 21, "last": None, "tag": ""}
    assert files["Show/Show 21Dir.rus.mka"]["conflict"] is None
    for path in ("Show/Show 21.mkv", "Show/Show 21.rus.mka", "Show/Show 21.rus.ass"):
        assert files[path]["place"] is None
        assert files[path]["conflict"] == {"code": "taken", "episode": "S01E21", "by": "Директор"}, path
    for path in ("Show/Show 22.mkv", "Show/Show 22 (v2).mkv"):
        assert files[path]["place"] is None and files[path]["conflict"] == {"code": "twice", "episode": "S01E22", "by": "Серия"}
    # Red goes no further: the plain sound and subtitles are in none of the next tabs.
    assert [e["path"] for e in view["tabs"]["voices"]] == ["Show/Show 21Dir.rus.mka"]
    assert view["tabs"]["subtitles"] == []
    # The hand goes before every rule: a video placed by hand takes the episode from the rules.
    inputs.manual = {"files": {item_of("page", "Show/Show 21.mkv"): {"season": 1, "episode": 21, "episode_last": None, "tag": ""}}}
    files = {entry["path"]: entry for entry in compose(inputs)[0]["tabs"]["files"]}
    assert files["Show/Show 21.mkv"]["conflict"] is None
    assert files["Show/Show 21Dir.mkv"]["conflict"] == {"code": "taken", "episode": "S01E21", "by": "manual"}


def test_a_tag_of_the_video_makes_a_version_an_episode_of_its_own_and_a_variant_of_the_output():
    """The director's cut tagged DIR: `S01E21 DIR` and `S01E21` are two episodes; each sound goes with the video of its own
    version, and its rows are a line of the player of their own (`torrent:page#DIR`), a variant apart."""
    r = release(["Show/Show 21Dir.mkv", "Show/Show 21Dir.rus.mka", "Show/Show 21.mkv", "Show/Show 21.rus.mka"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show 21Dir.mkv": streams(("video", 0, "", "")),
                                      "Show/Show 21.mkv": streams(("video", 0, "", "")),
                                      "Show/Show 21Dir.rus.mka": streams(("audio", 0, "rus", "Студия")),
                                      "Show/Show 21.rus.mka": streams(("audio", 0, "rus", "Студия"))}},
                    results={"files": ruled({"Show/Show 21Dir.mkv": (1, 21, 0, "Директор", "DIR"),
                                             "Show/Show 21Dir.rus.mka": (1, 21, 0, "Директор", "DIR"),
                                             "Show/Show 21.mkv": (1, 21, 1, "Серия", ""),
                                             "Show/Show 21.rus.mka": (1, 21, 1, "Серия", "")})})
    view, rows = compose(inputs)
    assert all(entry["conflict"] is None for entry in view["tabs"]["files"])
    assert sorted((row.player, row.player_label, row.link) for row in rows) == [
        ("torrent:page", "Тайтл · 1-12", track_link("page", "Show/Show 21.mkv", 0, file="Show/Show 21.rus.mka")),
        ("torrent:page#DIR", "Тайтл · 1-12 · DIR", track_link("page", "Show/Show 21Dir.mkv", 0, file="Show/Show 21Dir.rus.mka"))]
    assert {row.text for row in rows} == {"S01E21 | Show/Show 21.mkv", "S01E21 DIR | Show/Show 21Dir.mkv"}
    # The hand gives a version too.
    inputs.results = {"files": {}}
    inputs.manual = {"files": {item_of("page", path): {"season": 1, "episode": 21, "episode_last": None, "tag": tag}
                               for path, tag in (("Show/Show 21Dir.mkv", "DIR"), ("Show/Show 21Dir.rus.mka", "DIR"),
                                                 ("Show/Show 21.mkv", ""), ("Show/Show 21.rus.mka", ""))}}
    assert sorted(row.player for row in compose(inputs)[1]) == ["torrent:page", "torrent:page#DIR"]


def test_a_flag_of_the_files_container_is_no_type_of_subtitles_and_no_fact_of_the_row():
    """The source tells of subtitles their name, language and type (full, signs); how a track lies in a container (forced,
    default) is the output's (DESIGN.md 7.1.1): the flags of the release's own file make neither a type nor a field."""
    r = release(["Show/E01.mkv"])
    facts = streams(("video", 0, "", ""), ("subtitle", 1, "rus", "Надписи"))
    facts["streams"][1].update(forced=True, default=True)
    inputs = Inputs(releases=[r], manual={}, folders=[], streams={"page": {"Show/E01.mkv": facts}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1)})})
    view, rows = compose(inputs)
    assert [(entry["folder"], entry["subtitle_type"]) for entry in view["tabs"]["subtitle_streams"]] == [("Надписи", "")]
    assert [(row.subtitle_type, "forced" in row.site_fields, "default" in row.site_fields) for row in rows] == [("", False, False)]


def test_the_file_names_the_folder_and_the_language_by_itself_und_waits_and_a_rule_overrides_the_file():
    r = release(["Show/E01.mkv"])
    video = item_of("page", "Show/E01.mkv")
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "rus", "AniLiberty"),
                                                              ("audio", 2, "und", "Original"), ("audio", 3, "jpn", ""))}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1)})})
    view, rows = compose(inputs)
    tracks = [(e["folder"], e["by"], e["language"], e["waiting"]) for e in view["tabs"]["voice_streams"]]
    # A track with no known language is placed by nothing: it is in no folder, red, whatever its title.
    assert tracks == [("AniLiberty", "meta", "rus", False), (None, None, "und", True), (None, None, "jpn", True)]
    assert [(f["name"], f["manual"]) for f in view["folders"]] == [("AniLiberty", False)]
    assert view["unplaced"]["voice_streams"] == 2
    # Only the green track is a row: a track with no folder or no language is red and goes no further.
    assert [(row.voice.name, row.language) for row in rows] == [("AniLiberty", "rus")]
    # A rule of the tab «Озв. конт.» says otherwise: its folder and language go before what the file says.
    inputs.results["voice_streams"] = {f"{video}#2": {"voice_folder": "Оригинал", "language": "jpn", "events": [{"rule": "яп"}]}}
    view, _rows = compose(inputs)
    assert [(e["folder"], e["by"], e["language"], e["waiting"]) for e in view["tabs"]["voice_streams"]][1] == ("Оригинал", "яп", "jpn", False)


def test_a_deleted_folder_of_the_automatic_layout_is_not_made_again_and_the_hands_placement_stays():
    r = release(["Show/E01.mkv"])
    video = item_of("page", "Show/E01.mkv")
    inputs = Inputs(releases=[r], folders=[], hidden={("voice_streams", "Original"), ("voice_streams", "Студия")},
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "rus", "AniLiberty"),
                                                              ("audio", 2, "jpn", "Original"), ("audio", 3, "rus", "x"))}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1)}),
                             "voice_streams": {f"{video}#3": {"voice_folder": "Студия", "events": [{"rule": "студия"}]}}},
                    manual={"voice_streams": {f"{video}#1": {"folder": "Original"}}})
    view, rows = compose(inputs)
    # Deleted: «Original» of the title and «Студия» of a rule are made by neither — the tracks are red; the hand's placement
    # into «Original» stays, it is no work of the automatic layout.
    assert [(e["item"][-2:], e["folder"], e["by"], e["waiting"]) for e in view["tabs"]["voice_streams"]] == [
        ("#1", "Original", "manual", False), ("#2", None, None, True), ("#3", None, None, True)]
    assert view["hidden"] == [{"tab": "voice_streams", "name": "Original"}, {"tab": "voice_streams", "name": "Студия"}]
    # «Авто»: nothing hidden, the automatic layout makes them again.
    inputs.hidden = set()
    view, rows = compose(inputs)
    assert [(e["folder"], e["by"]) for e in view["tabs"]["voice_streams"]] == [("Original", "manual"), ("Original", "meta"), ("Студия", "студия")]


def test_a_track_taken_out_of_its_folder_is_red_whatever_the_file_says_until_a_hand_places_it():
    r = release(["Show/E01.mkv"])
    video = item_of("page", "Show/E01.mkv")
    inputs = Inputs(releases=[r], folders=[], removed={("voice_streams", f"{video}#1"), ("voice_streams", f"{video}#2")},
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "rus", "AniLiberty"),
                                                              ("audio", 2, "jpn", "Original"))}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1)})},
                    manual={"voice_streams": {f"{video}#2": {"folder": "Студия"}}})
    view, rows = compose(inputs)
    # «−» on AniLiberty: in no folder, red, no row; the hand's later placement of #2 goes before its removal.
    assert [(e["folder"], e["by"], e["waiting"]) for e in view["tabs"]["voice_streams"]] == [(None, None, True), ("Студия", "manual", False)]
    assert [row.voice.name for row in rows] == ["Студия"]
    assert view["removed"] == [{"tab": "voice_streams", "item": f"{video}#1"}, {"tab": "voice_streams", "item": f"{video}#2"}]


def test_the_folders_of_two_tabs_of_a_kind_are_two_sets_that_do_not_mix():
    r = release(["Show/E01.mkv", "Show/E01.mka"])
    inputs = Inputs(releases=[r], manual={}, folders=[{"tab": "voices", "name": "Original", "language": "eng", "subtitle_type": ""}],
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")),
                                      "Show/E01.mka": streams(("audio", 0, "rus", "Original"))}},
                    results={"files": numbered({"Show/E01.mkv": (1, 1), "Show/E01.mka": (1, 1)})})
    view, _rows = compose(inputs)
    # One name, two folders: that of «Озвучки» (its language set there) and that of «Озв. конт.» (the file's language).
    assert [(f["tab"], f["name"], f["items"], f["language"]) for f in view["folders"]] == [
        ("voice_streams", "Original", 1, ""), ("voices", "Original", 1, "eng")]
    assert [e["language"] for e in view["tabs"]["voice_streams"]] == ["jpn"]
    assert [e["language"] for e in view["tabs"]["voices"]] == ["eng"]


def test_a_folder_a_rule_gives_names_the_track_and_the_users_folder_goes_before_it():
    r = release(["Show/E01.mkv", "Show/Rus Audio/JAM/E01.mka", "Show/Rus Audio/Studio/E01.mka"])
    inputs = Inputs(releases=[r], folders=[{"tab": "voices", "name": "Студия", "language": "rus", "subtitle_type": ""}],
                    streams={"page": {"Show/E01.mkv": streams(("video", 0, "", "")),
                                      "Show/Rus Audio/JAM/E01.mka": streams(("audio", 0, "", "")),
                                      "Show/Rus Audio/Studio/E01.mka": streams(("audio", 0, "", ""))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files}),
                             "voices": {item_of("page", "Show/Rus Audio/JAM/E01.mka"): {"voice_folder": "JAM",
                                                                                         "events": [{"rule": "студии"}]},
                                        item_of("page", "Show/Rus Audio/Studio/E01.mka"): {"voice_folder": "Studio"}}},
                    manual={"voices": {item_of("page", "Show/Rus Audio/Studio/E01.mka"): {"folder": "Студия"}}})
    view, rows = compose(inputs)
    # JAM has no language: not placed, no row; the user's folder has its language: a row.
    assert [(row.voice.name, row.site_fields["language"]) for row in rows] == [("Студия", "rus")]
    voices = {entry["item"]: entry for entry in view["tabs"]["voices"]}
    # The rule gives JAM no language and the file has none: the track is not placed, there is no folder JAM.
    assert voices[item_of("page", "Show/Rus Audio/JAM/E01.mka")]["folder"] is None
    assert voices[item_of("page", "Show/Rus Audio/Studio/E01.mka")]["by"] == "manual"
    assert [(f["name"], f["items"], f["manual"]) for f in view["folders"]] == [("Студия", 1, True)]


def test_seasons_in_folders_of_their_own_place_their_files_and_a_track_of_another_season_is_not_taken():
    r = release(["Show/Season 1/E01.mkv", "Show/Season 2/E01.mkv", "Show/Season 2/E01.mka"])
    inputs = Inputs(releases=[r], folders=[], manual={},
                    streams={"page": {p.path: streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")) if p.path.endswith("mkv")
                                      else streams(("audio", 0, "rus", "Студия")) for p in r.files}},
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
                    streams={"page": {p.path: streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")) for p in r.files}},
                    results={"files": numbered({"Show/a.mkv": (1, 1), "Show/b.mkv": (1, 2), "Show/extra.mkv": None})},
                    manual={"files": {item_of("page", "Show/a.mkv"): {"season": 1, "episode": 5, "episode_last": None}}})
    view, rows = compose(inputs)
    # The file no rule and no hand placed is red: it has no row, and nothing else is left out.
    assert [row.text for row in rows] == ["S01E02 | Show/b.mkv", "S01E05 | Show/a.mkv"]
    files = {entry["path"]: entry for entry in view["tabs"]["files"]}
    assert files["Show/extra.mkv"]["place"] is None and "excluded" not in files["Show/extra.mkv"]
    assert view["unplaced"]["files"] == 1


def test_a_release_being_updated_gives_its_rows_not_obtained_and_a_first_download_none():
    paths = ["Show/E01.mkv"]
    probed = {"page": {"Show/E01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original"))}}
    results = {"files": numbered({"Show/E01.mkv": (1, 1)})}
    _v, updating = compose(Inputs([release(paths, ReleaseState.UPDATING)], probed, results, {}))
    _v, first = compose(Inputs([release(paths, ReleaseState.DOWNLOADING)], probed, results, {}))
    assert [(row.state.value, json.loads(row.reason)["code"]) for row in updating] == [("not_obtained", "torrent_updating")]
    assert first == []


# --- the module on the bus -------------------------------------------------------------------------------------------

TITLES = {"jpn": "Original", "rus": "Студия"}


def _file(path: str, *, video: bool, audios: list[str]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    args = ["ffmpeg", "-v", "error", "-y"]
    if video:
        args += ["-f", "lavfi", "-i", "color=c=blue:s=64x36:d=1"]
    for _ in audios:
        args += ["-f", "lavfi", "-i", "sine=f=440:d=1"]
    for i in range(len(audios) + (1 if video else 0)):
        args += ["-map", str(i)]
    for i, lang in enumerate(audios):  # a title too: the track lies in a folder by it, green, a row
        args += [f"-metadata:s:a:{i}", f"language={lang}", f"-metadata:s:a:{i}", f"title={TITLES[lang]}"]
    subprocess.run(args + ["-c:a", "aac", "-shortest", path], check=True)


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
        return []

    async def on_releases(self, env):
        return {"releases": [r.model_dump(mode="json") for r in self.releases]}

    async def on_list(self, env):
        return [{"id": 1, "sources": [{"url": URL, "source_type": "torrent"}] if self.listed else [], "ignored_seasons": "[2]",
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


async def _rule(torrents, profile_id: int, name: str, blocks: list[dict]) -> None:
    """A rule the owner writes in a tab of the editor: the number after the blocks is the episode."""
    action = [{"action_type": "extract_single", "action_pattern": json.dumps(blocks, ensure_ascii=False)}]
    await torrents.request("rules.rules.add", {"profile_id": profile_id, "data": {
        "name": name, "continue_after_match": True, "action_pattern": json.dumps(action, ensure_ascii=False), "conditions": []}})


async def test_a_ready_release_is_read_its_files_wait_for_a_rule_and_one_rule_places_every_episode(bus_stand):
    torrents, unpack, bus, root = bus_stand
    folder = str(root / "staging" / "kinozal-1")
    _file(f"{folder}/Show/Show - 01.mkv", video=True, audios=["jpn", "rus"])
    _file(f"{folder}/Show/Show - 02.mkv", video=True, audios=["jpn"])
    ready = release(["Show/Show - 01.mkv", "Show/Show - 02.mkv", "Show/readme.nfo"], folder=folder)
    torrents.releases = [ready]
    changed = bus.subscribe("unpack.table.changed")
    progress = bus.subscribe("unpack.read.progress")

    unpack.publish_event(RELEASE_CHANGED_TOPIC, ready.model_dump(mode="json"))
    assert (await asyncio.wait_for(changed.queue.get(), 10)).payload == {"url": URL}
    # The read of the version told file by file (the nfo is no file to read), and the editor holds no read any more.
    told = []
    while not progress.queue.empty():
        told.append((lambda each: (each["done"], each["total"]))(progress.queue.get_nowait().payload))
    assert told == [(0, 2), (1, 2), (2, 2)]

    # No rule is the source's yet: every tab has its own empty profile, no file has an episode, the page has no rows.
    editor = await torrents.request("unpack.editor.get", {"url": URL})
    profiles = editor["profiles"]
    assert set(profiles) == {"files", "voices", "subtitles", "voice_streams", "subtitle_streams"} and len(set(profiles.values())) == 5
    for each in profiles.values():
        assert not await torrents.request("rules.rules.list", {"profile_id": each})
    assert [(e["place"], e["by"]) for e in editor["view"]["tabs"]["files"]] == [(None, None)] * 3
    assert (await torrents.request("unpack.table.get", {"url": URL}))["rows"] == []
    assert [each["reading"] for each in editor["view"]["releases"]] == [None]

    await _rule(torrents, profiles["files"], "серия", [{"type": "text", "value": " - "}, {"type": "number"}])
    await asyncio.wait_for(changed.queue.get(), 10)
    table = await torrents.request("unpack.table.get", {"url": URL})
    assert table["names"] == ["Тайтл · 1-12"]
    assert [(row["key"], row["text"], row["site_fields"]["language"]) for row in table["rows"]] == [
        ("page/Show/Show - 01.mkv#1", "S01E01 | Show/Show - 01.mkv", "jpn"),
        ("page/Show/Show - 01.mkv#2", "S01E01 | Show/Show - 01.mkv", "rus"),
        ("page/Show/Show - 02.mkv#1", "S01E02 | Show/Show - 02.mkv", "jpn")]
    assert all(row["heights"] == [36] for row in table["rows"])
    files = {e["path"]: e for e in (await torrents.request("unpack.editor.get", {"url": URL}))["view"]["tabs"]["files"]}
    assert files["Show/Show - 02.mkv"]["by"] == "серия" and files["Show/readme.nfo"]["place"] is None


async def test_a_page_no_source_lists_is_forgotten_with_the_profiles_of_its_tabs(bus_stand):
    torrents, unpack, bus, root = bus_stand
    torrents.releases = [release(["Show/Show - 01.mkv"])]
    profiles = (await torrents.request("unpack.editor.get", {"url": URL}))["profiles"]
    await torrents.request("unpack.folder.set", {"url": URL, "tab": "voices", "name": "Студия"})
    torrents.listed = False
    await unpack.on_source_changed(Envelope(topic="series.source.removed", kind="event", payload={"series_id": 1, "source_id": 1}))
    assert await unpack.repo.pages() == []
    left = {p["id"] for p in await torrents.request("rules.profiles.list", {"kind": "torrent"})}
    assert not left & set(profiles.values())


async def test_files_dropped_on_a_season_take_its_episodes_from_the_one_given_in_the_order_sent(bus_stand):
    torrents, unpack, bus, root = bus_stand
    ova = ["Bonus/OVA 1.mkv", "Bonus/OVA 2.mkv", "Bonus/OVA 3.mkv"]
    torrents.releases = [release(ova)]
    await torrents.request("unpack.assign", {"url": URL, "tab": "files", "items": [item_of("page", p) for p in ova],
                                             "season": 0, "episode": 4, "episode_last": 9, "in_order": True})
    editor = await torrents.request("unpack.editor.get", {"url": URL})
    assert [(e["path"], e["place"], e["by"]) for e in editor["view"]["tabs"]["files"]] == [
        (path, {"season": 0, "episode": 4 + n, "last": None, "tag": ""}, "manual") for n, path in enumerate(ova)]


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


async def test_a_page_written_with_a_mirror_is_the_page_of_the_trackers_own_domain_in_the_editor(bus_stand):
    torrents, unpack, bus, root = bus_stand
    torrents.releases = [release(["Show/Show - 01.mkv"])]
    mirror = URL.replace("kinozal.tv", "kinozal.me")
    await torrents.request("unpack.assign", {"url": mirror, "tab": "files", "items": [item_of("page", "Show/Show - 01.mkv")],
                                             "season": 2, "episode": 7})
    await torrents.request("unpack.folder.set", {"url": mirror, "tab": "voices", "name": "Студия"})
    by_mirror = await torrents.request("unpack.editor.get", {"url": mirror})
    by_page = await torrents.request("unpack.editor.get", {"url": URL})
    assert by_mirror["url"] == by_page["url"] == URL and by_mirror["profiles"] == by_page["profiles"]
    assert by_page["view"]["tabs"]["files"][0]["place"] == {"season": 2, "episode": 7, "last": None, "tag": ""}
    assert [f["name"] for f in by_page["view"]["folders"]] == ["Студия"]


async def test_the_progress_of_a_download_changes_neither_the_table_nor_the_editor(bus_stand):
    torrents, unpack, bus, root = bus_stand
    changed = bus.subscribe("unpack.table.changed", "unpack.editor.changed")
    downloading = release(["Show/Show - 01.mkv", "Show/Show - 02.mkv"], state=ReleaseState.DOWNLOADING)
    torrents.releases = [downloading]
    for progress in (0.1, 0.5, 0.9):
        moved = downloading.model_copy(update={"files": [f.model_copy(update={"progress": progress}) for f in downloading.files]})
        await unpack.on_release_changed(Envelope(topic=RELEASE_CHANGED_TOPIC, kind="event", payload=moved.model_dump(mode="json")))
    told = [changed.queue.get_nowait().topic for _ in range(changed.queue.qsize())]
    assert told == ["unpack.table.changed", "unpack.editor.changed"]  # the first news only


async def test_every_file_of_a_release_is_downloaded_the_editor_tells_torrents_nothing_of_it(bus_stand):
    """No hidden logic of the download: the editor places, a red file is just red, and no command of what not to download
    goes to `torrents` — neither for a release that downloads nor for a placement by hand."""
    torrents, unpack, bus, root = bus_stand
    told = bus.subscribe("torrents.release.priorities")
    downloading = release(["Show/Season 1/Show - 01.mkv", "Show/Season 2/Show - 01.mkv"], state=ReleaseState.DOWNLOADING)
    torrents.releases = [downloading]
    await unpack.on_release_changed(Envelope(topic=RELEASE_CHANGED_TOPIC, kind="event", payload=downloading.model_dump(mode="json")))
    await torrents.request("unpack.assign", {"url": URL, "tab": "files", "items": [item_of("page", "Show/Season 2/Show - 01.mkv")],
                                             "season": 2, "episode": 1})
    await torrents.request("unpack.editor.get", {"url": URL})  # every command sent before this answer is on the bus by now
    assert told.queue.empty()


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
                             "subtitles": {item_of("page", p): {"subtitle_folder": p.split("/")[2], "language": "rus"}
                                           for p in paths if p.endswith(".ass")},
                             "voices": {item_of("page", "Show/Audio/JAM/E01.mka"): {"voice_folder": "JAM", "language": "rus"}}})
    view, _rows = compose(inputs)
    assert [(f["tab"], f["kind"], f["name"], f["fonts"]) for f in view["folders"]] == [
        ("subtitles", "subtitles", "Cafe", ["Show/sub/Cafe/fonts/a.ttf"]), ("subtitles", "subtitles", "Shift", ["Show/sub/Cafe/fonts/a.ttf"]),
        ("voices", "voice", "JAM", [])]


def test_the_editor_tells_the_file_episode_and_stream_of_every_track_and_what_every_video_holds():
    r = release(["Show/Show - 01.mkv", "Show/Show - 01.rus.mka"])
    inputs = Inputs(releases=[r], manual={}, folders=[],
                    streams={"page": {"Show/Show - 01.mkv": streams(("video", 0, "", ""), ("audio", 1, "jpn", "Original")),
                                      "Show/Show - 01.rus.mka": streams(("audio", 0, "rus", ""))}},
                    results={"files": numbered({p.path: (1, 1) for p in r.files})})
    view, _rows = compose(inputs)
    stream = view["tabs"]["voice_streams"][0]
    assert (stream["path"], stream["place"], stream["stream"]["index"], stream["stream"]["language"]) == (
        "Show/Show - 01.mkv", {"season": 1, "episode": 1, "last": None, "tag": ""}, 1, "jpn")
    voice = view["tabs"]["voices"][0]
    assert (voice["path"], voice["place"]["episode"]) == ("Show/Show - 01.rus.mka", 1)
    assert [(each["path"], [s["kind"] for s in each["streams"]]) for each in view["containers"]] == [
        ("Show/Show - 01.mkv", ["video", "audio"])]
    # Every stream of a container carries its line as the rules read it, the video's too: the window shows it as it is.
    assert [s["line"] for s in view["containers"][0]["streams"]] == ["T:видео | A:0 | C:h264",
                                                                     "T:звук | A:1 | L:jpn | N:Original | C:aac"]
