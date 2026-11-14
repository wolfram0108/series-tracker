"""The socket of external source providers (contracts/provider_socket.md): what crosses the border between the tracker and a
provider process, described once. The tracker's connector (modules/sources/provider) and a provider build and read these
models alike; a field a receiver does not know is ignored, so adding an optional field leaves the protocol as it is.

The table itself (`TableDraft`, `TableRow`) is not defined here: its fields are closed and belong to the sites of the
tracker (core/contracts/release_table.py, modules/sources/sites). A draft travels as the dict it serialises to.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PROTOCOL = 1
# The provider says it is alive at least this often while a read or the event feed is silent; silence for SILENCE_S is a
# dead connection (detection of a failure, not a way to wait).
HEARTBEAT_S = 15.0
SILENCE_S = 3 * HEARTBEAT_S

# The frames of a read (`POST /v1/read`), by their `type`.
FRAME_PROGRESS = "progress"
FRAME_ALIVE = "alive"
FRAME_DRAFT = "draft"
FRAME_ERROR = "error"
FRAME_DONE = "done"

# The events of the feed (`GET /v1/events`).
EVENT_HELLO = "hello"
EVENT_CHANGED = "changed"

# The reasons the tracker gives when the socket fails it (the provider's own come with its answers).
PROVIDER_UNAVAILABLE = "provider_unavailable"
PROVIDER_INCOMPATIBLE = "provider_incompatible"
PROVIDER_STREAM_BROKEN = "provider_stream_broken"
PROVIDER_BAD_ANSWER = "provider_bad_answer"
PROVIDER_REFUSED = "provider_refused"


def _pattern(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"not a regular expression: {exc}") from exc
    return value


class ProviderInfo(BaseModel):
    id: str = Field(min_length=1)
    version: str = ""


class SiteAddress(BaseModel):
    """How an address of a site is told and written: its schemes, a path matching `path` whole, and the one canonical
    spelling (`{group}` of `path`)."""

    schemes: list[str] = Field(min_length=1)
    path: str
    canonical: str

    _path = field_validator("path")(_pattern)


class ManifestSite(BaseModel):
    name: str = Field(min_length=1)
    label: str = ""
    domains: list[str] = Field(min_length=1)
    source_type: Literal["vk_video", "web_video"] = "web_video"
    first_read_by_user: bool = False
    address: SiteAddress


class ManifestFamily(BaseModel):
    name: str = Field(min_length=1)
    label: str = ""
    owns: str
    video: str

    _owns = field_validator("owns")(_pattern)


class ManifestPlace(BaseModel):
    name: str
    label: str = ""


class ManifestPlayer(BaseModel):
    name: str = Field(min_length=1)
    label: str = ""
    kind: Literal["site", "line"] = "line"
    places: list[ManifestPlace] = Field(default_factory=list)


class ManifestSearch(BaseModel):
    name: str = Field(min_length=1)
    label: str = ""


class Manifest(BaseModel):
    """What a provider reads and plays, told without the network (`GET /v1/manifest`)."""

    protocol: int
    provider: ProviderInfo
    revision: str = Field(min_length=1)
    sites: list[ManifestSite] = Field(default_factory=list)
    families: list[ManifestFamily] = Field(default_factory=list)
    players: list[ManifestPlayer] = Field(default_factory=list)
    search: list[ManifestSearch] = Field(default_factory=list)


class FeedEvent(BaseModel):
    """`hello` and `changed` of the feed: the revision of the manifest the provider has now."""

    revision: str


class ReadRequest(BaseModel):
    """`POST /v1/read`: the whole page into one table. `last_rows` are `TableRow` dicts of the last complete table."""

    url: str
    rows: str = "all"
    recent: int | None = None
    fresh: bool = False
    off: list[str] = Field(default_factory=list)
    last_rows: list[dict] = Field(default_factory=list)
    term_hours: int | None = None


class ReadFrame(BaseModel):
    """One line of the stream of a read. `draft` is a `TableDraft` as the dict it serialises to."""

    type: Literal["progress", "alive", "draft", "error"]
    phase: str = ""
    current: int = 0
    total: int = 0
    draft: dict | None = None
    reason: str = ""


class DownloadRequest(BaseModel):
    """`POST /v1/download`: the file of the link of a row into `path` (absolute; the tracker and the provider share the
    paths). The number of connections and the way the file comes are the provider's own business."""

    url: str
    path: str
    max_height: int | None = None
    term_hours: int | None = None


class DownloadFrame(BaseModel):
    """One line of the stream of a download. `progress` carries the telemetry `figures` (contracts/provider_socket.md, section
    7); `done` the height of the picture of the file that lies at the path; `error` the reason and its `kind`: `final` is the last
    word of the source (asking again changes nothing), `broken` a way that broke (the tracker asks again, and the failure is the
    source's when its attempts are spent), `unasked` a failure that says nothing of the source (the provider did not reach it)."""

    type: Literal["progress", "alive", "done", "error"]
    figures: dict = Field(default_factory=dict)
    height: int | None = None
    reason: str = ""
    kind: Literal["final", "broken", "unasked"] = "broken"


class ProviderState(BaseModel):
    """The event `sources.provider.state`: how the socket stands (`absent` is never published: no provider, no event)."""

    provider: str
    state: Literal["connecting", "up", "down", "incompatible"]
    revision: str = ""
    reason: str = ""
