"""The real frame height of a stream, read by ffprobe: for the players whose labels of quality do not state it.

The labels of a player are words of the host (CDN VideoHub calls a 2160 frame "2k", animakima calls a 720 frame "1080p"),
so a height that matters is measured on the stream itself, with the headers the host signs the stream for.
"""
from __future__ import annotations

from typing import Mapping

from core.ffprobe import film_stream, streams_of

MEASURE_TIMEOUT_S = 40.0


async def frame_height(url: str, headers: Mapping[str, str]) -> int | None:
    """The height of the film of the stream (core.ffprobe.film_stream: no picture it carries); None when ffprobe is missing
    or the stream cannot be read."""
    agent = headers.get("User-Agent")
    blob = "".join(f"{key}: {value}\r\n" for key, value in headers.items() if key != "User-Agent")
    streams = await streams_of(url, input_args=[*(["-user_agent", agent] if agent else []),
                                                *(["-headers", blob] if blob else [])], timeout=MEASURE_TIMEOUT_S)
    film = film_stream(streams or [])
    try:
        return int(film["height"]) if film is not None else None
    except (KeyError, TypeError, ValueError):
        return None
