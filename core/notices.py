"""Videos a host serves in place of an episode to tell something: a spent quota of a parser, as a clip of a few seconds
that plays like any file. Such a clip has a frame and a length of its own, so neither the measure of a row nor the check
of a downloaded file tells it from an episode by itself.

A notice is known by the hash of the head of its file, as the owner's prototypes know it (source-prototypes,
sourcekit/notices.json). The registry holds hashes only: the clip itself is a host's answer and stays out of the
repository.

A file of an episode shorter than `EPISODE_MIN_S` is broken whatever it shows (the owner's rule): no episode is that
short, and a host that broke an upload serves seconds of it — a studio's intro of 6 s in place of an episode of 7 min.
Its length is no measure of a match: the alignment service finds an episode of minutes in a compilation of hours.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class Notice:
    id: str
    prefix: int     # the bytes of the head of the file the hash is of
    sha256: str
    words: str      # what the clip says, in words


NOTICES: tuple[Notice, ...] = (
    # 茉莉 (AppGet), 2026-11-11: 1280x720, 16.3 s, 1 581 830 bytes; its frame reads «您当前已达上限，请前往小程序领取次数» —
    # the limit of attempts is reached, more are given in the mini-program.
    Notice("moli-quota-20261111", 65536, "5f0e39e035cac70f502fe72f4407853bacc258fc9b08a55ff7e7b46f9c3c8702",
           "the parser's limit of attempts is spent"),
)


EPISODE_MIN_S = 60.0


def too_short(seconds: float) -> bool:
    """Whether a file of an episode is too short to be one; a length that could not be read judges nothing. The limit is
    read when asked: the test world, whose episodes last a second, has a limit of its own scale."""
    return 0 < seconds < EPISODE_MIN_S


def head_bytes() -> int:
    """How much of the head of a file tells every known notice."""
    return max((notice.prefix for notice in NOTICES), default=0)


def notice_of(head: bytes) -> Notice | None:
    """The notice whose file begins with this head; None for any other file."""
    for notice in NOTICES:
        if len(head) >= notice.prefix and hashlib.sha256(head[:notice.prefix]).hexdigest() == notice.sha256:
            return notice
    return None
