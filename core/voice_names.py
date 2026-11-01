"""The one form in which two spellings of a voice-over name are the same name."""
from __future__ import annotations

import re

_NOISE = re.compile(r"[\W_]+", re.UNICODE)


def fold_name(name: str) -> str:
    """No case, no spaces, no punctuation: the form a group trigger and a voice-over name are compared in."""
    return _NOISE.sub("", name.casefold())
