import faulthandler

import pytest

from schema_db import migrated

# The slowest test takes about half a minute: a test that stands longer than this is stuck, and a
# stuck suite must say where instead of waiting for ever.
STUCK_AFTER_S = 180
# The suite runs on the box of the service: when memory runs out, the kernel takes a test process first, never the service.
# Raising one's own score needs no privilege; the workers of a parallel run inherit it.
FIRST_TO_GO = 1000


def pytest_configure(config):
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write(str(FIRST_TO_GO))
    except OSError:
        pass  # not Linux, or no /proc: nothing to protect here


@pytest.fixture(autouse=True)
def stuck_test_guard():
    faulthandler.dump_traceback_later(STUCK_AFTER_S, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


def _relaxed_paces():
    """(module, name, value) of every pause that spares a real host."""
    from core.retry import UNTIMED, Budget
    from modules.sources import policy
    sites = "modules.sources.sites"
    return [
        ("modules.sources.module", "REQUEST_MIN_INTERVAL", 0.0),
        ("modules.sources.policy", "DEFAULT", policy.Budget(pauses=(0.0,))),
        ("modules.sources.players.registry", "PROBE_INTERVAL_S", 0.0),
        (f"{sites}.ruchime.pages", "PAGE_INTERVAL_S", 0.0),
        (f"{sites}.ruchime.pages", "PAGE_COOLDOWN_S", 0.0),
        (f"{sites}.ruchime.pages", "PAGE_BUDGET", Budget(attempts=4, pauses=(0.0,), limit_s=UNTIMED)),
        (f"{sites}.ruchime.pages", "RELEASE_BUDGET", Budget(attempts=3, pauses=(0.0,), limit_s=UNTIMED)),
        ("modules.sources.players.catalog", "CATALOG_BUDGET", Budget(attempts=2, pauses=(0.0,), limit_s=UNTIMED)),
        (f"{sites}.vk_channel.site", "VK_PAGE_INTERVAL_S", 0.0),
    ]


@pytest.fixture(autouse=True)
def hosts_are_not_paced_in_tests(monkeypatch):
    """The pauses that spare a real host (between requests, probes and repeated asks) would only slow fakes down. A pause
    that was renamed or moved fails here: kept silently, it would make every test wait for real."""
    import importlib
    for module_name, name, value in _relaxed_paces():
        monkeypatch.setattr(importlib.import_module(module_name), name, value)


@pytest.fixture
def db_path(tmp_path):
    """An empty database at the head of the schema; a test that needs rows of its own defines its own `db_path`."""
    return migrated(tmp_path / "test.db")


@pytest.fixture(autouse=True)
def storage_without_leftovers():
    """What a test's stalled storage call still owns must not reach the next test."""
    from core import storage
    yield
    storage._holds.clear()
    storage._waiting.clear()
