"""The torrent source (stage 15): the releases a tracker page lists, and the link of a row of a release's track.

A page of a tracker lists releases (`sources.releases.get`); a release is downloaded by `torrents` into its folder, its
files are read by `unpack`, and every track becomes a row of the table of the page, as a row of a site. The player of
such a row is a release: `torrent:<release>`, a line of the player `torrent`; its link names the file and the stream.
"""
from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Any, Iterable, Literal, get_args
from urllib.parse import quote, unquote

from pydantic import BaseModel, ConfigDict, Field

from .frozen import Frozen
from .release_table import SubtitleType

TORRENT_PLAYER = "torrent"
_SCHEME = "torrent:"
# The folder the releases are downloaded to (the staging folder of the torrent sources), a key of `settings`.
STAGING_DIR_KEY = "torrents.staging_dir"


class ReleaseState(StrEnum):
    """`metadata`: a magnet waits for the list of its files; `downloading`: the first version is being downloaded, the
    source is not in the series yet; `ready`: every wanted file is there; `updating`: a new version is being checked and
    downloaded, the whole release is unavailable; `failed`: see `reason`; `archived`: the files were removed (a release
    no longer watched, after what was taken from it lives in the output files)."""

    METADATA = "metadata"
    DOWNLOADING = "downloading"
    READY = "ready"
    UPDATING = "updating"
    FAILED = "failed"
    ARCHIVED = "archived"


class ReleaseFile(Frozen):
    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    progress: float = 0.0
    priority: int = 1


class ReleaseView(Frozen):
    """A release `torrents` keeps for a page. `folder` is where its files are (`folder/path`); `pending` a newer version
    the page lists that waits for the end of a sync of a series with this source."""

    model_config = ConfigDict(extra="forbid")

    url: str
    release: str
    title: str = ""
    published: str | None = None
    infohash: str | None = None
    state: ReleaseState
    reason: str | None = None
    watched: bool = True
    folder: str | None = None
    pending: bool = False
    files: tuple[ReleaseFile, ...] = Field(default_factory=tuple)
    # When the page was last checked for a newer version of it (none: never), and the next check of the schedule after
    # that (core/schedule.py; none: no check scheduled): past it the release is not known to be current.
    checked_at: str | None = None
    fresh_until: str | None = None
    # What the client says of the torrent now, as it words it (qBittorrent's state: `checkingDL`, `downloading`, ...); none:
    # not known (the client was not asked yet, or the torrent is not in it). A fact of the moment, kept in memory only.
    client: str | None = None


class PageRelease(Frozen):
    """A release as the page lists it. `release` is its id on the page, the same for every version of it: the page itself
    for a page of one release (kinozal, rutracker), the quality for a catalog of a release per quality (anilibria), the
    episodes and the quality for a catalog of a release per episode (astar)."""

    model_config = ConfigDict(extra="forbid")

    release: str
    title: str = ""
    link: str | None = None
    magnet: str | None = None
    published: str | None = None
    episodes: str | None = None
    quality: str | None = None
    infohash: str | None = None


class PageReleases(Frozen):
    """`sources.releases.get {url}`: what the page lists now, read from the tracker; `url` is the canonical address."""

    model_config = ConfigDict(extra="forbid")

    url: str
    site: str
    title: str = ""
    releases: tuple[PageRelease, ...] = Field(default_factory=tuple)


class ReleasesReply(Frozen):
    """`torrents.releases.get {url}`: the releases kept for the page."""

    releases: tuple[ReleaseView, ...] = Field(default_factory=tuple)


class ReleaseRef(BaseModel):
    """`torrents.release.download` and `torrents.release.drop_files`: a release by its page and its id on the page."""

    url: str
    release: str


class ReleaseWatch(ReleaseRef):
    """`torrents.release.watch`: the automatic update of the release on or off."""

    watched: bool


class ReleasesChecked(Frozen):
    """`torrents.release.check {url}`: the releases whose update began or waits for the end of a sync."""

    releases: tuple[str, ...] = Field(default_factory=tuple)


class StagingFolder(Frozen):
    """`torrents.staging.get`, and the event `torrents.staging.changed`: the folder the releases download into ("" when none is set)."""

    value: str


class StagingSet(BaseModel):
    """`torrents.staging.set`: the folder to download the releases into, an absolute path. What the window sent is taken as it came
    (a value that is no text is refused with the others that are no absolute path)."""

    value: Any = None


class StagingSaved(Frozen):
    """`torrents.staging.set`: the folder was saved, or refused with a `reason` (a path that is not absolute)."""

    ok: bool
    value: str
    reason: str | None = None


class TorrentProgress(Frozen):
    """A torrent of the old pipeline in the monitor: a row of `torrent_progress` with the name of its series."""

    model_config = ConfigDict(extra="forbid")

    id: int
    qb_hash: str
    series_id: int
    status: str | None = None
    progress: int = 0
    dlspeed: int | None = None
    eta: int | None = None
    error_message: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    series_name: str = ""


class ReleaseTask(Frozen):
    """A release of a torrent source in the monitor: one task per series whose source is its page, whatever the state of the release."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["release"]
    qb_hash: str
    url: str
    release: str
    title: str
    series_id: int | None = None
    series_name: str = ""
    status: str
    progress: float
    files_done: int
    files_total: int
    error_message: str | None = None


class TorrentTasks(Frozen):
    """`torrents.db.progress.list`, and the event `torrents.progress.changed`: what the monitor of «Агенты» shows of the torrents."""

    tasks: tuple[TorrentProgress | ReleaseTask, ...] = Field(default_factory=tuple)


def order_name(player: str) -> str:
    """The name a player of a row takes in the order of sources of the settings (its place, its switch for every source, its
    group of players): every release of every tracker's page is one place, the torrent's — a release is a line of one page
    only, nothing to set for every series. Any other player is its own name."""
    return TORRENT_PLAYER if player.startswith(_SCHEME) else player


def player_of(release: str, tag: str = "") -> str:
    """The player a row of a release's track names: the release is a line of the player `torrent`; a version of the episode
    with a tag of its video (DIR) is a line of its own (`torrent:<release>#DIR`), a variant of the output apart."""
    return f"{TORRENT_PLAYER}:{release}" + (f"#{tag}" if tag else "")


def track_link(release: str, video: str, stream: int, file: str | None = None) -> str:
    """`torrent:<release>/<video>#<n>`: the n-th track of its kind (0 the first sound) in the video file `video` (paths in
    the release); a track of an external file names it with `?file=<path>`, the number being that file's. The video of the
    row is always the video file, so every track of one video is known as one video whatever file it lies in."""
    link = f"{_SCHEME}{quote(release, safe='')}/{quote(video, safe='/')}"
    if file is not None:
        link += f"?file={quote(file, safe='/')}"
    return f"{link}#{stream}"


def parse_link(link: str) -> tuple[str, str, str, int] | None:
    """(release, video, file, stream) of a track link, `file` being the video itself for a stream of the container; None
    for any other address."""
    if not link.startswith(_SCHEME):
        return None
    body, _, stream = link[len(_SCHEME):].rpartition("#")
    body, _, query = body.partition("?file=")
    release, _, video = body.partition("/")
    if not release or not video or not stream.isdigit():
        return None
    video = unquote(video)
    return unquote(release), video, unquote(query) if query else video, int(stream)


def video_of_link(link: str) -> str:
    """Which video a track row is: the video file in its release (the video track of a source is never compared by content)."""
    parsed = parse_link(link)
    return f"{parsed[0]}/{parsed[1]}" if parsed else ""


# --- the lines of the torrent editor (its five tabs), the input of the rules of each tab -----------------------------

class EditorTab(StrEnum):
    FILES = "files"
    VOICES = "voices"
    SUBTITLES = "subtitles"
    # The tracks inside the video files: the sound («Озв. конт.») and the subtitles («Суб. конт.»), each a tab of its own.
    VOICE_STREAMS = "voice_streams"
    SUBTITLE_STREAMS = "subtitle_streams"


def stream_tab(kind: str) -> "EditorTab":
    """The tab of a track inside a container by the kind of its stream (`audio`, `subtitle`)."""
    return EditorTab.SUBTITLE_STREAMS if kind == "subtitle" else EditorTab.VOICE_STREAMS


class FileKind(StrEnum):
    VIDEO = "video"
    AUDIO = "audio"
    SUBTITLES = "subtitles"
    FONT = "font"
    OTHER = "other"


# The words of the kinds in a line, as the mockup of the editor shows them; a rule matches the word, not the code.
KIND_WORDS = {FileKind.VIDEO: "видео", FileKind.AUDIO: "звук", FileKind.SUBTITLES: "субтитры", FileKind.FONT: "шрифт",
              FileKind.OTHER: "другое"}
_EXTENSIONS = {
    FileKind.VIDEO: {"mkv", "mp4", "avi", "m2ts", "ts", "mov", "wmv", "webm", "m4v", "flv", "mpg", "mpeg", "vob", "ogm"},
    FileKind.AUDIO: {"mka", "ac3", "eac3", "dts", "aac", "flac", "mp3", "m4a", "ogg", "opus", "wav", "thd", "dtshd", "mp2"},
    FileKind.SUBTITLES: {"ass", "ssa", "srt", "sup", "sub", "idx", "vtt"},
    FileKind.FONT: {"ttf", "otf", "ttc"},
}
_SEP = " | "


def extension_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def kind_of(path: str) -> FileKind:
    ext = extension_of(path)
    return next((kind for kind, known in _EXTENSIONS.items() if ext in known), FileKind.OTHER)


def _split(path: str) -> tuple[str, str]:
    folder, _, name = path.rpartition("/")
    return folder, name


def episode_word(season: int | None, episode: int | None, last: int | None = None) -> str:
    """`S01E05` (`S01E05-E06` for a file of several episodes): one spelling of the place of a file in every line."""
    if episode is None:
        return ""
    word = f"S{season or 0:02d}E{episode:02d}"
    return f"{word}-E{last:02d}" if last is not None and last != episode else word


def files_line(path: str) -> str:
    """A line of the tab «Файлы»: `D:<folder> | <name> | K:<kind> | X:<extension>`."""
    folder, name = _split(path)
    parts = [f"D:{folder}" if folder else "", name, f"K:{KIND_WORDS[kind_of(path)]}", f"X:{extension_of(path)}"]
    return _SEP.join(part for part in parts if part and not part.endswith(":"))


def episode_fields(season: int | None, episode: int | None, last: int | None = None, tag: str = "") -> str:
    """The episode of a file in a line, every part with its code, as the editor parsed it: `S:1 | E:5`, `E:5-6` for a file of
    several episodes, `TG:DIR` for a version with a tag of its video. A rule takes a number after its code."""
    if episode is None:
        return ""
    number = f"{episode}-{last}" if last is not None and last != episode else str(episode)
    return _SEP.join(part for part in (f"S:{season or 0}", f"E:{number}", f"TG:{tag}" if tag else "") if part)


def track_file_line(path: str, place: str) -> str:
    """A line of the tabs «Озвучки» and «Субтитры»: `D:<folder> | <name> | X:<extension> | S:1 | E:5` (`place`: episode_fields)."""
    folder, name = _split(path)
    parts = [f"D:{folder}" if folder else "", name, f"X:{extension_of(path)}", place]
    return _SEP.join(part for part in parts if part and not part.endswith(":"))


STREAM_KINDS = {"video": "видео", "audio": "звук", "subtitle": "субтитры"}


def stream_line(kind: str, index: int, language: str = "", title: str = "", codec: str = "") -> str:
    """A line of the tabs «Озв. конт.» and «Суб. конт.», one per stream: `T:<kind> | A:<number> | L:<language> | N:<title> | C:<codec>`."""
    parts = [f"T:{STREAM_KINDS.get(kind, kind)}", f"A:{index}", f"L:{language}" if language else "",
             f"N:{title}" if title else "", f"C:{codec}" if codec else ""]
    return _SEP.join(part for part in parts if part)


# What a rule of the editor may set besides numbers: the folder of a voice-over or of subtitles by its name, the language,
# the type of subtitles (DESIGN.md, 4.1).
SUBTITLE_TYPES = tuple(each for each in get_args(SubtitleType) if each)


def fonts_of(paths: Iterable[str], subtitles: str) -> list[str]:
    """The font files of a release a subtitles file is drawn with (DESIGN.md 7.1): those in its folder and below it; none
    there — those of the nearest folder above it that has any (a folder of fonts beside the folders of subtitles), up to
    the root of the release."""
    fonts = sorted(path for path in paths if kind_of(path) == FileKind.FONT)
    folder = posixpath.dirname(subtitles)
    while True:
        prefix = f"{folder}/" if folder else ""
        found = [path for path in fonts if path.startswith(prefix)]
        if found or not folder:
            return found
        folder = posixpath.dirname(folder)
