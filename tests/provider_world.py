"""The external provider of the socket (chinese_sources) as a test finds it: a folder on this machine that is not in the
repository. A test that needs it is skipped when the folder is not there (`needs_provider`); the rest of the suite does not
read it. Its package is imported from its folder, and its models of the video applications (the folder `tests` of the
provider) are found beside the tracker's own test helpers."""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.environ.get("ST_PROVIDER_SRC") or "/home/user/chinese_sources"


def present() -> bool:
    return os.path.isdir(os.path.join(ROOT, "chinese_sources"))


def load() -> None:
    """Make the package and the models of the provider importable (idempotent). The models come last on the path: a helper
    of the same name in the tracker's tests is the tracker's."""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    models = os.path.join(ROOT, "tests")
    if models not in sys.path:
        sys.path.append(models)


needs_provider = pytest.mark.skipif(not present(), reason=f"no provider of the socket at {ROOT}")

if present():
    load()
