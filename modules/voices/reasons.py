"""Why a voice-over is failed, rejected or started over: a code and the facts, never a sentence.

The reason is stored as JSON in the row and reaches the interface as {code, params}; the interface owns
the wording. Words of other systems (a downloader, the alignment service) are kept as `external` text.
"""
from __future__ import annotations

import json
from typing import Any

EXTERNAL = "external"


def reason(code: str, **params: Any) -> str:
    return json.dumps({"code": code, **params}, ensure_ascii=False, separators=(",", ":"))


def parse(stored: str | None) -> dict[str, Any] | None:
    """The reason as the interface gets it; None when there is none."""
    if not stored:
        return None
    try:
        value = json.loads(stored)
    except ValueError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("code"), str):
        return {"code": value["code"], "params": {key: item for key, item in value.items() if key != "code"}}
    return {"code": EXTERNAL, "params": {"text": stored}}
