import faulthandler

import pytest

# The slowest test takes about half a minute: a test that stands longer than this is stuck, and a
# stuck suite must say where instead of waiting for ever.
STUCK_AFTER_S = 180


@pytest.fixture(autouse=True)
def stuck_test_guard():
    faulthandler.dump_traceback_later(STUCK_AFTER_S, exit=True)
    yield
    faulthandler.cancel_dump_traceback_later()


def _relaxed_paces():
    """(module, name, value) of every pause that spares a real host; a name that no longer exists is skipped."""
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
        (f"{sites}.ruchime.site", "CATALOG_BUDGET", Budget(attempts=2, pauses=(0.0,), limit_s=UNTIMED)),
        (f"{sites}.vk_channel.site", "VK_PAGE_INTERVAL_S", 0.0),
    ]


@pytest.fixture(autouse=True)
def hosts_are_not_paced_in_tests(monkeypatch):
    """The pauses that spare a real host (between requests, probes and repeated asks) would only slow fakes down."""
    import importlib
    for module_name, name, value in _relaxed_paces():
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, name):
            monkeypatch.setattr(module, name, value)


@pytest.fixture(autouse=True)
def storage_without_leftovers():
    """What a test's stalled storage call still owns must not reach the next test."""
    from core import storage
    yield
    storage._holds.clear()
    storage._waiting.clear()
