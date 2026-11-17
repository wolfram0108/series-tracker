"""The torrent source (stage 15): the releases a tracker page lists, and the link of a row of a release's track.

A page of a tracker lists releases (`sources.releases.get`); a release is downloaded by `torrents` into its folder, its
files are read by `unpack`, and every track becomes a row of the table of the page, as a row of a site. The player of
such a row is a release: `torrent:<release>`, a line of the player `torrent`; its link names the file and the stream.
"""
from __future__ import annotations

import posixpath
from enum import StrEnum
from typing import Iterable, get_args
from urllib.parse import quote, unquote

from pydantic import BaseModel, ConfigDict, Field

from .release_table import SubtitleType

TORRENT_PLAYER = "torrent"
_SCHEME = "torrent:"
# A release of a page changed: its state, its version, its files (`ReleaseView`); `unpack` reads the files of a ready one.
RELEASE_CHANGED_TOPIC = "torrents.release.changed"
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


class ReleaseFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    size: int
    progress: float = 0.0
    priority: int = 1


class ReleaseView(BaseModel):
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
    files: list[ReleaseFile] = Field(default_factory=list)


class PageRelease(BaseModel):
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


class PageReleases(BaseModel):
    """`sources.releases.get {url}`: what the page lists now, read from the tracker; `url` is the canonical address."""

    model_config = ConfigDict(extra="forbid")

    url: str
    site: str
    title: str = ""
    releases: list[PageRelease] = Field(default_factory=list)


def player_of(release: str) -> str:
    """The player a row of a release's track names: the release is a line of the player `torrent`."""
    return f"{TORRENT_PLAYER}:{release}"


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


def track_file_line(path: str, place: str) -> str:
    """A line of the tabs «Озвучки» and «Субтитры»: `D:<folder> | <name> | X:<extension> | S:<S01E05>`."""
    folder, name = _split(path)
    parts = [f"D:{folder}" if folder else "", name, f"X:{extension_of(path)}", f"S:{place}" if place else ""]
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
