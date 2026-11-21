"""What a fetch is and what its stream and its transfer say: the job a request makes, what it is for, the stream a link comes to, the
words of a refused transfer, who a withdrawal stops, and when the next waiting fetch may begin."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Sequence

from core.contracts import DownloadFetch, DownloadNeed, FetchFinished, FetchStarted, StreamReply, VideoFetched, WorkWithdrawn
from core.reasons import reason

from .failure import Failed, Final, Unasked


@dataclass(frozen=True)
class Job:
    """A fetch as the executor holds it: the stream behind `url` into the absolute `path`, for the need `need_key`."""

    key: str | None
    url: str | None
    path: str | None
    max_height: int | None
    series_id: int | None
    whole: bool | None
    ytdlp: bool | None
    need_key: str = ""
    place: tuple = ()  # where it waits in the queue (`waiting_place`)


@dataclass(frozen=True)
class Stream:
    """What the stream request of a link says: the address and the headers, how the file comes, or why it cannot."""

    url: str
    headers: dict | None = None
    error: tuple[str, Failed] | None = None  # the words of the refusal and what they say of the source
    progressive: bool = False
    playlist: bool = False
    by_player: bool = False    # the player downloads the file itself (`sources.video.fetch`), the segments of a playlist
    by_provider: bool = False  # a provider of the socket takes the whole file by its own way into the path


def job_of(fetch: DownloadFetch) -> Job:
    return Job(fetch.key, fetch.url, fetch.path, fetch.max_height, fetch.series_id, fetch.whole, fetch.ytdlp)


def waiting_place(fetch: DownloadFetch, asked: int) -> tuple:
    """Where a fetch waits in the queue: the fetches of an earlier sync first, within a sync the earlier episode of the series (its video
    and voice-overs together, so that an episode is ready for its alignment while the next one downloads; the next source of an episode
    that failed goes before the later episodes), then the order asked. A fetch that names no place waits after those of its sync that do."""
    sync = fetch.need.sync_started if fetch.need is not None else ""
    return sync, fetch.place is None, fetch.place or 0, asked


def path_refusal(job: Job) -> str | None:
    """The reason a fetch is refused at once: a download goes into an absolute path."""
    return None if (job.path or "").startswith("/") else reason("save_path_relative")


def need_of(job: Job, fetch: DownloadFetch) -> DownloadNeed:
    """What the fetch is for, as its owner tells it; a fetch that names no need is one of its own."""
    return fetch.need or DownloadNeed(key=job.key, series_id=job.series_id or 0, kind="video" if job.whole else "dub", cap=job.max_height)


def started(job: Job) -> FetchStarted:
    return FetchStarted(key=job.key, series_id=job.series_id, path=job.path)


def finished(job: Job, ok: bool, error: str, transient: bool) -> FetchFinished:
    return FetchFinished(key=job.key, ok=ok, error="" if ok else error, transient=transient, path=job.path, series_id=job.series_id)


def task_key(task_id: int | str) -> str:
    """The key of the fetch a card names (`fetch:<key>`)."""
    return str(task_id).removeprefix("fetch:")


def may_begin(waiting: int, active: int, placing: int, total: int) -> bool:
    """Whether the next waiting fetch leaves the queue to ask its stream: as many begin as may go at once whatever their way, not counting
    those waiting for a place, and no more wait than that: a full way does not hold the queue from the other, and the links of the whole
    queue are not asked at once."""
    return bool(waiting) and active - placing < total and placing < total


def stops_series(series_id: int) -> Callable[[str | None, int | None], bool]:
    """Whether a fetch (its key and the series it is of) is among the fetches of the series."""
    return lambda key, owner: owner == series_id


def stops_notice(notice: WorkWithdrawn) -> Callable[[str | None, int | None], bool]:
    """Whether a fetch is among those the owner does not need: the fetches of the series, or of the keys the notice names."""
    return lambda key, owner: owner == notice.series_id and (notice.keys is None or key in notice.keys)


def removable(card: str, cards: Sequence[str], in_work: set[str]) -> bool:
    """Whether the card of that key may be removed by the user: it stayed after its sync and no fetch works for it."""
    return card in cards and card not in in_work


# --- the stream of a link ---------------------------------------------------------------------------------------------------

def stream_of(url: str, reply: StreamReply) -> Stream:
    """What the stream request of a link says. The player says itself whether its refusal is its last word (`transient`: a way that
    may pass); a player that was not reached (`unasked`) said nothing of the source. `by_player`: the player downloads the file itself, a
    playlist by its segments; `by_provider`: a provider of the socket takes the whole file. A link no player owns is the downloader's."""
    if reply.error:
        kind = Failed.UNASKED if reply.unasked else Failed.BROKEN if reply.transient else Failed.FINAL
        return Stream(url, error=(reply.error, kind))
    if reply.handled:
        by_player = bool(reply.player_fetch)
        return Stream(reply.stream_url, reply.headers, progressive=bool(reply.progressive), playlist=bool(reply.playlist) or by_player,
                      by_player=by_player, by_provider=reply.fetch_by == "provider")
    return Stream(url)


def unreached(url: str, cause: str, downloader_may_take: bool) -> Stream:
    """The stream request failed: with the downloader allowed the link goes to it as it is; otherwise the failure is the error of the job,
    since a page of a web player is no stream and yt-dlp would scrape a foreign player."""
    if downloader_may_take:
        return Stream(url)
    return Stream(url, error=(reason("stream_not_got", cause=cause), Failed.UNASKED))


def refusal_words(reply: VideoFetched) -> str:
    """The words of a transfer the player refused, carrying what they say of the source: its last word (`Final`), or nothing of it
    (`Unasked`); any other is a way that broke."""
    words = reason("player_fetch_failed", cause=reply.error) if reply.error else reason("player_fetch_refused")
    kind = reply.kind or ("final" if reply.final else "broken")
    return Final(words) if kind == "final" else Unasked(words) if kind == "unasked" else words
