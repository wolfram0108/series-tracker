"""Why something is refused or unavailable: a code and the facts, never a sentence.

A reason travels as a JSON string `{"code": ..., <facts>}`; the interface owns the wording. Text that is
not such JSON (words of another system, a producer not moved to codes yet) is foreign and shown as it is.
"""
from __future__ import annotations

import json
from typing import Any

EXTERNAL = "external"


def reason(code: str, **params: Any) -> str:
    return json.dumps({"code": code, **params}, ensure_ascii=False, separators=(",", ":"))


def parse(stored: str | None) -> dict[str, Any] | None:
    """The reason as the interface gets it ({code, params}); None when there is none."""
    if not stored:
        return None
    try:
        value = json.loads(stored)
    except ValueError:
        value = None
    if isinstance(value, dict) and isinstance(value.get("code"), str):
        return {"code": value["code"], "params": {key: item for key, item in value.items() if key != "code"}}
    return {"code": EXTERNAL, "params": {"text": stored}}


def describe(stored: str | None) -> str:
    """A reason for a log line: the code with its facts, nested reasons included."""
    found = parse(stored)
    if found is None:
        return ""
    if found["code"] == EXTERNAL:
        return str(found["params"]["text"])
    facts = ", ".join(f"{key}={_fact(item)}" for key, item in found["params"].items())
    return f"{found['code']}({facts})" if facts else found["code"]


def _fact(item: Any) -> str:
    if isinstance(item, list):
        return "[" + ", ".join(_fact(each) for each in item) + "]"
    return describe(item) if isinstance(item, str) and item.startswith("{") else str(item)
