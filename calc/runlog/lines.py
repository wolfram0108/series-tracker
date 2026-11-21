"""An entry of the dump as the line of the file: the payload of an envelope told for the dump (models by their kind and the size of their
lists, secrets masked), the JSON line, the gzip member of a batch of lines, the folder a page's dumps are kept in."""
from __future__ import annotations

import gzip
import json
import re
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from core.secrets import redacted

if TYPE_CHECKING:
    from core.envelope import Envelope

# The three kinds of what a run holds: the envelope of the bus, a line of the log, and the opening and the closing of the run itself.
BUS, LOG, OPEN, CLOSE = "bus", "log", "open", "close"


def page_folder(url: str) -> str:
    """The address of a page as the name of the folder of its dumps."""
    return re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "_", url.split("://", 1)[-1]).strip("_")[:100] or "page"


def _told(value: Any) -> Any:
    """A payload as plain data: a model handed over in the process whole (the layout, megabytes) is told by its kind and the size of its
    lists, and a model inside it, or inside a dictionary or a list, the same way, so that nothing of it is left to be written by the
    text of the object, which has every field of it."""
    if isinstance(value, BaseModel):
        return {"model": type(value).__name__, **{name: _field(each) for name, each in value.__dict__.items()}}
    if isinstance(value, dict):
        return {key: _told(each) for key, each in value.items()}
    if isinstance(value, (list, tuple)):
        return [_told(each) for each in value]
    return value


def _field(value: Any) -> Any:
    """A field of a model: a list or a dictionary is told by its size (its rows are in the dump as the planner's trace of the inputs of
    its plan), a model by its own kind, the rest as it is."""
    if isinstance(value, BaseModel):
        return _told(value)
    return len(value) if isinstance(value, (list, tuple, dict)) else value


def written(payload: Any, topic: str | None = None) -> Any:
    """What of a payload goes into a dump, with every secret in it masked, at any depth of the models it holds (the dump keeps no secret).
    `topic` is the topic the payload goes to: some fields are a secret in the payload of one topic only (core/secrets.py)."""
    return redacted(_told(payload), topic)


def line(entry: tuple[str, str, Any]) -> str:
    """The JSON line of an entry (kind, time, body): the body of a bus entry is the envelope, written with its topic, correlation number
    and the topic of the answer."""
    kind, at, body = entry
    if kind == BUS:
        envelope: "Envelope" = body
        body = {"kind": envelope.kind, "topic": envelope.topic, "cid": envelope.correlation_id, "reply_to": envelope.reply_to,
                "admission": envelope.admission, "payload": written(envelope.payload, envelope.topic)}
    return json.dumps({"t": at, "e": kind, **body}, ensure_ascii=False, default=repr)


def pack(entries: list) -> bytes:
    """A batch of entries as one gzip member, to be appended to the file of the run."""
    return gzip.compress(("\n".join(line(each) for each in entries) + "\n").encode("utf-8"), compresslevel=6)
