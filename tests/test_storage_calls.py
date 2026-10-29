"""Class guard: a call into the file system never waits without a limit.

Modules reach the storage through core.storage, which bounds the wait; a thread offloaded by hand does not.
Local diagnostics and network clients with timeouts of their own are listed.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Network requests with their own timeouts, and diagnostics of local logs and of the database.
UNBOUNDED_BY_REASON = {"modules/trackerauth/module.py", "modules/gateway/api_settings.py"}


def test_modules_reach_the_storage_only_through_the_bounded_helper():
    offenders = sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "modules").rglob("*.py")
        if "asyncio.to_thread(" in path.read_text(encoding="utf-8")
        and path.relative_to(ROOT).as_posix() not in UNBOUNDED_BY_REASON)
    assert not offenders, f"файловые вызовы — через core.storage.call: {offenders}"
