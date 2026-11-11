"""What a failed attempt of a download says of its source.

An attempt fails for one of three reasons, and each is answered differently (modules/downloads/module.py):

  FINAL    the source's last word on the file — it is gone, it is private: asking again changes nothing, the failure is
           the source's at once;
  BROKEN   the way to the file broke — a cut connection, a refusal for the moment, a program that stopped, anything nobody
           foresaw: the download is asked again, and when its attempts are spent the failure is the source's;
  UNASKED  the source was never reached — a module of ours did not answer, a name of ours was busy, a program of ours is
           missing or was stopped: the download is asked again, and its failure is never the source's.

A failure is FINAL or UNASKED only when it is known to be: its producer says so by the type of its words (`Final`,
`Unasked`), or a program that tells its failure only in words (yt-dlp of a page, ffmpeg of a stream) used the words listed
here. A failure nobody recognises is BROKEN.
"""
from __future__ import annotations

import enum


# The statuses by which a host says a file or a segment of it is not there for good.
GONE = (404, 410)


class Failed(enum.Enum):
    FINAL = "final"
    BROKEN = "broken"
    UNASKED = "unasked"


class Final(str):
    """The words of a failure its producer knows to be the source's last word on the file."""


class Unasked(str):
    """The words of a failure that says nothing of the source: the download did not reach it."""


# How the programs that fail only in words say that a file is not to be had: a private or removed video (yt-dlp, the
# words of ytdlp.py for it), an address the host has nothing at (yt-dlp, ffmpeg, the reader of a playlist).
_FINAL_WORDS = ("недоступно", "приватн", "video unavailable", "private video",
                "http error 404", "http error 410", "server returned 404", "server returned 410")


def kind_of(failure: str) -> Failed:
    if isinstance(failure, Final):
        return Failed.FINAL
    if isinstance(failure, Unasked):
        return Failed.UNASKED
    lowered = (failure or "").lower()
    return Failed.FINAL if any(word in lowered for word in _FINAL_WORDS) else Failed.BROKEN
