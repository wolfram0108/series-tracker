"""What the module unpack answers, made of what it is given: the inputs of the layout from what the module stores, the table of
a page and the editor of it, the profiles of the tabs. The releases, the stored placements and the answers of the rules are
asked and read by the module; the tabs of tracks read the places the files got, so the module counts the lines of a tab between
two askings of the rules."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from core.contracts import RulesApplyReply, SeriesCard, SourceType
from core.contracts.torrent import EditorTab, ReleaseView

from .layout import Inputs, compose

# The profile of each tab of the editor in the table unpack_sources, and the word its name is made with.
PROFILE_COLUMNS = {"files": "files_profile_id", "voices": "voices_profile_id", "subtitles": "subtitles_profile_id",
                   "voice_streams": "voice_streams_profile_id", "subtitle_streams": "subtitle_streams_profile_id"}
TAB_WORDS = {"files": "файлы", "voices": "озвучки", "subtitles": "субтитры", "voice_streams": "озв. конт.",
             "subtitle_streams": "суб. конт."}


def profile_name(url: str, tab: str) -> str:
    """The name of the profile of a tab of a page: the page and the tab, the way the profiles of the editor are told apart."""
    return f"Торрент: {url} · {TAB_WORDS[tab]}"


def profiles_of(stored: Mapping[str, Any] | None) -> dict[str, int]:
    """The profiles made for the tabs of a page (tab → id), from its stored row; a tab not made has none."""
    return {tab: stored[column] for tab, column in PROFILE_COLUMNS.items() if stored and stored.get(column)}


def torrent_urls(listing: Sequence[SeriesCard]) -> list[str]:
    """The addresses of the torrent sources of the series listed, as the sources are written."""
    return [source.url for one in listing for source in one.sources if source.source_type == SourceType.TORRENT]


def stored_inputs(releases: list[ReleaseView], streams: dict[str, dict[str, dict]], assignments: Sequence[Mapping[str, Any]],
                  folders: list[dict], hidden: Sequence[Mapping[str, Any]], removed: Sequence[Mapping[str, Any]]) -> Inputs:
    """The inputs of the layout without the answers of the rules: the releases, what was read of their files and what the
    user placed, named and took out by hand."""
    manual: dict[str, dict[str, dict]] = {tab.value: {} for tab in EditorTab}
    for row in assignments:
        manual[row["tab"]][row["item"]] = row
    return Inputs(releases=releases, streams=streams, results={}, manual=manual, folders=folders,
                  hidden={(row["tab"], row["name"]) for row in hidden},
                  removed={(row["tab"], row["item"]) for row in removed})


def results_of(reply: RulesApplyReply) -> dict[str, dict]:
    """The answers of the rules of a tab as the layout reads them: the result of every line, by its item."""
    return {key: result.model_dump() for key, result in reply.results.items()}


def table_reply(inputs: Inputs) -> dict:
    """`unpack.table.get`: the name of the page and the rows of its table."""
    _view, rows = compose(inputs)
    return {"names": [release.title for release in inputs.releases if release.title][:1],
            "rows": [row.model_dump(mode="json") for row in rows]}


def editor_reply(url: str, stored: Mapping[str, Any] | None, inputs: Inputs,
                 reading: Mapping[tuple[str, str], tuple[int, int]]) -> dict:
    """`unpack.editor.get`: the page, the profiles of its tabs and the view; each release shows how far its files are read
    (`reading`: (page, release) → (files read, files to read)), for a window opened in the middle of a read."""
    view, _rows = compose(inputs)
    for release in view["releases"]:
        found = reading.get((url, release["release"]))
        release["reading"] = None if found is None else {"done": found[0], "total": found[1]}
    return {"url": url, "profiles": profiles_of(stored), "view": view}
