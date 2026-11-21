"""What the downloader programs and the readers of playlists agree on: the statuses by which a host says a file is not there, and
how long a program may be silent. What a failure says of its source is counted in calc/downloads/failure.py."""
from __future__ import annotations

# The statuses by which a host says a file or a segment of it is not there for good.
GONE = (404, 410)

# A downloader program (ffmpeg, yt-dlp, the worker of segments) that prints nothing for this long is stuck: its download
# fails as a way that broke. One fuse for every program, read when it is used, so that a caller that changes it changes it
# for all of them.
SILENCE_S = 180.0
