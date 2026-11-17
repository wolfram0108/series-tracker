"""The layout of the releases of a torrent page: what the editor shows on its five tabs and the rows of the table of the page.

Pure: the files of each release (`torrents`), what each file holds (ffprobe), what the rules of each tab gave every line
and what the user placed by hand go in; the view of the editor and the rows go out. A placement by hand is the user's and
goes before a rule's; a rule reaches every file that comes, a placement by hand only the items it names.

A video file placed in an episode gives a row for every sound and subtitles stream it holds; a sound or subtitles file
placed in the same episode gives rows with that video, when one video is there (two videos of one episode leave it
unplaced: which video it is in sync with is the user's word). A track lies in the folder the user put it in, else the one
a rule names, else the one its title in the file names (folders are made of the titles by themselves); its language is the
rule's, else its folder's, else the file's. A track with no folder or with no known language (none, `und`) waits for the
operator: a rule settles it, and a rule overrides what the file says.

An episode is the season, the number and the tag of its video (a version: `S01E21` and `S01E21 DIR` are two episodes, two
variants of the output). It is taken by the first source that put a video in it — the hand, else the rules of «Файлы» in their
order: what a later source put in it (a video, a sound, subtitles) is red, with the conflict told; two videos one source put
in one episode are both red, which one it is is the user's word.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.contracts import RowState, RowTrack, RowVoice, TableRow
from core.contracts.torrent import (EditorTab, FileKind, stream_tab, ReleaseState, ReleaseView, episode_word, files_line, fonts_of,
                                    kind_of, player_of, stream_line, track_file_line, track_link)
from core.reasons import reason

KEYS_SEP = "/"
TRACK_KINDS = {"audio": "audio", "subtitle": "subtitles"}
# The kinds of tracks that are rows of the table: the sound, and the subtitles, which join the output by their own order
# (DESIGN.md 7.1.1), never as voice-overs.
ROW_KINDS = {"audio", "subtitles"}
# The states of a release whose files the table names; a first download is no part of the series yet.
SHOWN = {ReleaseState.READY, ReleaseState.UPDATING, ReleaseState.FAILED, ReleaseState.ARCHIVED}


def item_of(release: str, path: str, stream: int | None = None) -> str:
    """The key of an item of the editor: the release and the path, with the stream of a container."""
    key = f"{release}{KEYS_SEP}{path}"
    return key if stream is None else f"{key}#{stream}"


@dataclass(frozen=True)
class Place:
    season: int
    episode: int
    last: int | None = None
    tag: str = ""  # the version of the episode its video is (DIR); an episode with its tag is an episode of its own

    @property
    def word(self) -> str:
        word = episode_word(self.season, self.episode, self.last)
        return f"{word} {self.tag}" if self.tag else word


@dataclass(frozen=True)
class Conflict:
    """Why a file «Файлы» gave an episode is red: the episode was `taken` by a source before it, or one source put `twice`
    a video in it."""
    code: str
    word: str
    by: str  # the source that took the episode: `manual` or the name of a rule


@dataclass
class Inputs:
    releases: list[ReleaseView]
    streams: dict[str, dict[str, dict]]             # release → path → {streams, attachments, chapters, reason}
    results: dict[str, dict[str, dict]]             # tab → item → the result of the rules for its line
    manual: dict[str, dict[str, dict]]              # tab → item → the stored placement by hand
    folders: list[dict] = field(default_factory=list)
    hidden: set[tuple[str, str]] = field(default_factory=set)  # (tab, name): folders of the automatic layout deleted
    removed: set[tuple[str, str]] = field(default_factory=set)  # (tab, item): tracks the user took out of their folders


def lines(inputs: Inputs) -> dict[str, dict[str, str]]:
    """The line of every item of every tab, for the rules of the tab (the tabs of tracks need the places of the files)."""
    out: dict[str, dict[str, str]] = {tab: {} for tab in EditorTab}
    places, _conflicts = _placing(inputs)
    for release in inputs.releases:
        for f in release.files:
            item = item_of(release.release, f.path)
            out[EditorTab.FILES][item] = files_line(f.path)
            kind = kind_of(f.path)
            where = places.get(item)
            word = where.word if isinstance(where, Place) else ""
            if kind == FileKind.AUDIO:
                out[EditorTab.VOICES][item] = track_file_line(f.path, word)
            elif kind == FileKind.SUBTITLES:
                out[EditorTab.SUBTITLES][item] = track_file_line(f.path, word)
            elif kind == FileKind.VIDEO:
                for s in _probed(inputs, release.release, f.path):
                    if s["kind"] in TRACK_KINDS:
                        out[stream_tab(s["kind"])][item_of(release.release, f.path, s["index"])] = stream_line(
                            s["kind"], s["index"], s["language"], s["title"], s["codec"])
    return out


def _place_of(result: dict | None, manual: dict | None) -> Place | None:
    """The season, episode and tag of a file: the hand's, else the rule's; none — the file is red, it gives no row."""
    if manual is not None:
        return Place(manual["season"], manual["episode"], manual.get("episode_last"), manual.get("tag") or "")
    if not result:
        return None
    season, tag = result.get("season") or 1, str(result.get("video_tag") or "").strip()
    if result.get("episode") is not None:
        return Place(season, result["episode"], None, tag)
    if result.get("start") is not None:
        return Place(season, result["start"], result.get("end"), tag)
    return None


_LAST = 1 << 30  # the rank of a source no rule names: after every rule


def _source_of(result: dict | None, manual: dict | None) -> tuple[int, str]:
    """(rank, name) of what gave a file its episode: the hand first, then the rules by their place in the profile (the rule
    that gave the number)."""
    if manual is not None:
        return -1, "manual"
    for event in reversed((result or {}).get("events") or []):
        if {"episode", "start"} & set(event.get("extracted") or {}):
            return event.get("order", _LAST), event.get("rule") or ""
    return _LAST, _rule_of(result) or ""


def _placing(inputs: Inputs) -> tuple[dict[str, Place | None], dict[str, Conflict]]:
    """The place of every file and the conflicts: an episode is the first source's that put a video in it."""
    files_results, files_manual = inputs.results.get(EditorTab.FILES, {}), inputs.manual.get(EditorTab.FILES, {})
    given: dict[str, tuple[Place | None, tuple[int, str], FileKind]] = {}
    for r in inputs.releases:
        for f in r.files:
            item = item_of(r.release, f.path)
            result, manual = files_results.get(item), files_manual.get(item)
            given[item] = (_place_of(result, manual), _source_of(result, manual), kind_of(f.path))
    videos: dict[Place, list[tuple[int, str, str]]] = {}
    for item, (place, (rank, name), kind) in given.items():
        if place is not None and kind == FileKind.VIDEO:
            videos.setdefault(place, []).append((rank, name, item))
    owner: dict[Place, tuple[int, str]] = {}
    conflicts: dict[str, Conflict] = {}
    for place, found in videos.items():
        first = min(rank for rank, _name, _item in found)
        firsts = [each for each in found if each[0] == first]
        owner[place] = (first, firsts[0][1])
        if len(firsts) > 1:
            for _rank, name, item in firsts:
                conflicts[item] = Conflict("twice", place.word, name)
    for item, (place, (rank, _name), _kind) in given.items():
        taken = owner.get(place) if place is not None else None
        if item not in conflicts and taken is not None and rank != taken[0]:
            conflicts[item] = Conflict("taken", place.word, taken[1])
    return {item: None if item in conflicts else place for item, (place, _source, _kind) in given.items()}, conflicts


def _probed(inputs: Inputs, release: str, path: str) -> list[dict]:
    return (inputs.streams.get(release, {}).get(path) or {}).get("streams") or []


def _rule_of(result: dict | None) -> str | None:
    """The name of the last rule that gave the line something."""
    for event in reversed((result or {}).get("events") or []):
        if event.get("rule"):
            return event["rule"]
    return None


def _rule_number_of(result: dict | None) -> int | None:
    """The place in its profile of the last rule that gave the line something (the list of the rules numbers it so)."""
    for event in reversed((result or {}).get("events") or []):
        if event.get("rule"):
            return event.get("number")
    return None


# How a track got its folder when no hand and no rule placed it: the title its file gives it.
FROM_META = "meta"
# A language the file does not know: the track waits for a rule that names it.
UNKNOWN_LANGUAGES = {"", "und"}


def _folder_of(tab: str, kind: str, result: dict | None, manual: dict | None, stream: dict | None,
               hidden: set[tuple[str, str]] = frozenset()) -> tuple[str | None, str | None]:
    """(folder, how): the user's folder, else the rule's, else the title of the track in its file (`how`: `manual`, the
    name of the rule, or `meta`). A rule overrides what the file says; the hand overrides both. A folder of the automatic
    layout the user deleted (`hidden`) is made by neither until «Авто»: the track is in no folder."""
    if manual is not None:
        return manual["folder"], "manual"
    key = "voice_folder" if kind == "audio" else "subtitle_folder"
    if result and result.get(key):
        return (None, None) if (tab, result[key]) in hidden else (result[key], _rule_of(result))
    title = ((stream or {}).get("title") or "").strip()
    return (title, FROM_META) if title and (tab, title) not in hidden else (None, None)


def compose(inputs: Inputs) -> tuple[dict[str, Any], list[TableRow]]:
    """The view of the editor and the rows of the table."""
    places, conflicts = _placing(inputs)
    folder_facts = {(f["tab"], f["name"]): f for f in inputs.folders}
    # `containers`: every video file placed, with what it holds (the window lays the tabs «Озв. конт.» and «Суб. конт.» out by it: files of
    # one composition are one).
    view: dict[str, Any] = {"releases": [], "tabs": {tab.value: [] for tab in EditorTab}, "folders": [],
                            "unplaced": {tab.value: 0 for tab in EditorTab}, "containers": [], "hidden": [], "removed": []}
    rows: list[TableRow] = []
    named: dict[tuple[str, str], dict] = {}  # the folders the layout names: (tab, name) → facts and counts
    # The fonts a folder of subtitles goes with: those its external files are drawn with (fonts_of), by the release read.
    fonts: dict[str, set[str]] = {}
    paths_of: dict[str, list[str]] = {r.release: [f.path for f in r.files] for r in inputs.releases}

    def track(tab: str, item: str, line: str, kind: str, stream: dict | None, path: str = "",
              where: "Place | str | None" = None) -> dict:
        result, manual = inputs.results.get(tab, {}).get(item), inputs.manual.get(tab, {}).get(item)
        folder, how = _folder_of(tab, kind, result, manual, stream, inputs.hidden)
        if manual is None and (tab, item) in inputs.removed:
            folder, how = None, None  # taken out of its folder by the user («−»): red until a hand or «Авто»
        folder_kind = "voice" if kind == "audio" else "subtitles"
        facts = folder_facts.get((tab, folder), {}) if folder else {}
        language = (result or {}).get("language") or facts.get("language") or (stream or {}).get("language") or ""
        # The type is what a rule or the folder names; a flag of the file's container is no type (DESIGN.md 7.1.1).
        subtitle_type = (result or {}).get("subtitle_type") or facts.get("subtitle_type") or ""
        # A track is placed whole or not at all: with no known language it is in no folder — red, it waits for a rule.
        if folder and language.strip().lower() in UNKNOWN_LANGUAGES:
            folder, how, facts = None, None, {}
            subtitle_type = ""
        waiting = not folder
        # The file and its episode, and the stream (its place in the file and what it is): the window groups tracks by them.
        entry = {"item": item, "line": line, "kind": kind, "folder": folder, "by": how, "waiting": waiting,
                 "rule_number": _rule_number_of(result) if how not in (None, "manual", FROM_META) else None,
                 "language": language, "subtitle_type": subtitle_type if kind == "subtitles" else "", "path": path,
                 "place": _place_json(where),
                 "stream": None if stream is None else {key: stream.get(key) for key in _STREAM_KEYS}}
        view["tabs"][tab].append(entry)
        if folder and tab == EditorTab.SUBTITLES and path:
            owner = next(each for each in paths_of if item.startswith(f"{each}{KEYS_SEP}"))
            fonts.setdefault(folder, set()).update(fonts_of(paths_of[owner], path))
        if folder:
            named.setdefault((tab, folder), {"tab": str(tab), "kind": folder_kind, "name": folder, "items": 0,
                                             "manual": (tab, folder) in folder_facts,
                                                     "language": facts.get("language", ""),
                                                     "subtitle_type": facts.get("subtitle_type", "")})["items"] += 1
        if waiting:
            view["unplaced"][tab] += 1
        return entry

    for release in inputs.releases:
        view["releases"].append({"release": release.release, "title": release.title, "state": release.state.value,
                                 "reason": release.reason, "pending": release.pending})
        videos: dict[Place, list[str]] = {}
        for f in release.files:
            item = item_of(release.release, f.path)
            where, kind = places.get(item), kind_of(f.path)
            result, manual = inputs.results.get(EditorTab.FILES, {}).get(item), inputs.manual.get(EditorTab.FILES, {}).get(item)
            view["tabs"][EditorTab.FILES].append({
                "item": item, "path": f.path, "line": files_line(f.path), "kind": kind.value, "size": f.size,
                "priority": f.priority,
                "place": _place_json(where),
                "by": "manual" if manual is not None else _rule_of(result),
                "rule_number": None if manual is not None else _rule_number_of(result),
                "conflict": None if (found := conflicts.get(item)) is None else {"code": found.code, "episode": found.word,
                                                                                "by": found.by},
                "problem": (inputs.streams.get(release.release, {}).get(f.path) or {}).get("reason")})
            if where is None and kind in (FileKind.VIDEO, FileKind.AUDIO, FileKind.SUBTITLES):
                view["unplaced"][EditorTab.FILES] += 1
            if kind == FileKind.VIDEO and isinstance(where, Place):
                videos.setdefault(where, []).append(f.path)

        state, why = _row_state(release)
        for where, paths in sorted(videos.items(), key=lambda pair: (pair[0].season, pair[0].episode)):
            for video in paths:
                streams = _probed(inputs, release.release, video)
                height = max((s["height"] or 0 for s in streams if s["kind"] == "video" and not s["picture"]), default=0)
                view["containers"].append({"item": item_of(release.release, video), "path": video, "place": _place_json(where),
                                           "streams": [{**{key: s.get(key) for key in _STREAM_KEYS},
                                                        # the line of the stream as the rules of «Озв. конт.» and «Суб. конт.» read it
                                                        "line": stream_line(s["kind"], s["index"], s["language"], s["title"],
                                                                            s["codec"])}
                                                       for s in streams
                                                       if s["kind"] in ("video", *TRACK_KINDS) and not s.get("picture")]})
                for s in streams:
                    if s["kind"] not in TRACK_KINDS:
                        continue
                    item = item_of(release.release, video, s["index"])
                    kind = TRACK_KINDS[s["kind"]]
                    entry = track(stream_tab(s["kind"]), item, stream_line(s["kind"], s["index"], s["language"], s["title"],
                                                                       s["codec"]), kind, s, video, where)
                    # A red track (no folder or no language yet) is no row: only what the editor placed goes on.
                    if release.state in SHOWN and not entry["waiting"] and kind in ROW_KINDS:
                        rows.append(_row(release, item, track_link(release.release, video, _nth(streams, s)), video, where,
                                         kind, entry, s, height, state, why))
            if len(paths) != 1:
                continue
            video = paths[0]
            streams = _probed(inputs, release.release, video)
            height = max((s["height"] or 0 for s in streams if s["kind"] == "video" and not s["picture"]), default=0)
            for f in release.files:
                kind = kind_of(f.path)
                if kind not in (FileKind.AUDIO, FileKind.SUBTITLES) or places.get(item_of(release.release, f.path)) != where:
                    continue
                tab = EditorTab.VOICES if kind == FileKind.AUDIO else EditorTab.SUBTITLES
                track_kind = "audio" if kind == FileKind.AUDIO else "subtitles"
                for s in _probed(inputs, release.release, f.path) or [{"index": 0, "kind": "", "language": "",
                                                                         "title": "", "codec": "", "forced": False}]:
                    item = item_of(release.release, f.path)
                    entry = track(tab, item, track_file_line(f.path, where.word), track_kind, s, f.path, where)
                    if release.state in SHOWN and not entry["waiting"] and s.get("kind") in TRACK_KINDS \
                            and track_kind in ROW_KINDS:
                        rows.append(_row(release, item_of(release.release, f.path, s["index"]),
                                         track_link(release.release, video, _nth(_probed(inputs, release.release, f.path), s),
                                                    file=f.path), video, where,
                                         track_kind, entry, s, height, state, why))
        # The tabs are a chain: what «Файлы» placed goes on to «Озвучки» and «Субтитры», a file it left red goes no further.
        # A sound or subtitles file placed in an episode no single video has is listed still: it passed «Файлы».
        for f in release.files:
            kind, item = kind_of(f.path), item_of(release.release, f.path)
            where = places.get(item)
            if kind not in (FileKind.AUDIO, FileKind.SUBTITLES) or where is None:
                continue
            tab = EditorTab.VOICES if kind == FileKind.AUDIO else EditorTab.SUBTITLES
            if any(entry["item"] == item for entry in view["tabs"][tab]):
                continue
            track(tab, item, track_file_line(f.path, where.word), "audio" if kind == FileKind.AUDIO else "subtitles", None,
                  f.path, where)

    for f in inputs.folders:  # the folders the user made, even empty
        kind = "voice" if f["tab"] in (EditorTab.VOICES, EditorTab.VOICE_STREAMS) else "subtitles"
        named.setdefault((f["tab"], f["name"]), {"tab": f["tab"], "kind": kind, "name": f["name"], "items": 0, "manual": True,
                                                  "language": f["language"], "subtitle_type": f["subtitle_type"]})
    for each in named.values():
        each["fonts"] = sorted(fonts.get(each["name"], ())) if each["tab"] == EditorTab.SUBTITLES else []
    view["folders"] = sorted(named.values(), key=lambda each: (each["tab"], each["name"]))
    view["hidden"] = [{"tab": tab, "name": name} for tab, name in sorted(inputs.hidden)]
    view["removed"] = [{"tab": tab, "item": item} for tab, item in sorted(inputs.removed)]
    return view, rows


# What the window is told of a stream of a file.
_STREAM_KEYS = ("index", "kind", "codec", "language", "title", "channels", "height", "forced", "default")


def _place_json(where: "Place | str | None") -> dict | None:
    return {"season": where.season, "episode": where.episode, "last": where.last, "tag": where.tag} \
        if isinstance(where, Place) else None


def _nth(streams: list[dict], stream: dict) -> int:
    """The number of a track among the tracks of its kind in its file (`-map <file>:a:<n>`, the `dub_atracks` of the
    alignment service): what the link of a row names, not the index of the stream in the file."""
    return [each["index"] for each in streams if each["kind"] == stream["kind"]].index(stream["index"])


def _row_state(release: ReleaseView) -> tuple[RowState, str | None]:
    if release.state == ReleaseState.READY:
        return RowState.PLAYABLE, None
    if release.state == ReleaseState.UPDATING:
        return RowState.NOT_OBTAINED, reason("torrent_updating")
    if release.state == ReleaseState.ARCHIVED:
        return RowState.NOT_OBTAINED, reason("torrent_archived")
    return RowState.NOT_OBTAINED, release.reason or reason("torrent_failed")


def _row(release: ReleaseView, key: str, link: str, video: str, where: Place, kind: str, entry: dict, stream: dict,
         height: int, state: RowState, why: str | None) -> TableRow:
    # A version of the episode (its tag) is a line of the player of its own: a variant of the output apart.
    label = release.title or release.release
    return TableRow(key=key, player=player_of(release.release, where.tag),
                    player_label=f"{label} · {where.tag}" if where.tag else label,
                    text=f"{where.word} | {video}", voice=RowVoice(name=entry["folder"] or stream.get("title") or "",
                                                                   kind=kind),
                    track=RowTrack.SUBTITLES if kind == "subtitles" else RowTrack.SOUND,
                    language=entry["language"] or "", subtitle_type=entry["subtitle_type"] or "",
                    heights=[height] if height else [], state=state, reason=why, link=link,
                    # Every track of the file is on the file's own timeline: in step with its video (DESIGN.md, 7.0).
                    timeline=item_of(release.release, video),
                    site_fields={"language": entry["language"], "codec": stream.get("codec") or "",
                                 "channels": stream.get("channels"), "subtitle_type": entry["subtitle_type"],
                                 "folder": entry["folder"] or "", "file": key.split("#")[0].split(KEYS_SEP, 1)[1]},
                    part=release.release)
