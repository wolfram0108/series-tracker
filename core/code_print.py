"""The print of the code of the service: a module that keeps what it counted with the print of its inputs counts again
whatever was counted by another code (a release, a migration)."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def code_print() -> str:
    """Its modules, its core and its migrations, each file by its path and its bytes."""
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for folder in ("core", "modules", "alembic/versions"):
        for path in sorted((root / folder).rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()
