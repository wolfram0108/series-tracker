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


@pytest.fixture(autouse=True)
def hosts_are_not_paced_in_tests(monkeypatch):
    """The pauses that spare a real host (between requests, probes and repeated asks) would only slow fakes down."""
    from modules.sources import module as sources
    from modules.sources import policy
    from core.retry import UNTIMED, Budget
    from modules.sources.video_providers.ruchime import provider as ruchime
    from modules.sources.video_providers.ruchime import site
    monkeypatch.setattr(sources, "REQUEST_MIN_INTERVAL", 0.0)
    monkeypatch.setattr(policy, "DEFAULT", policy.Budget(pauses=(0.0,)))
    monkeypatch.setattr(ruchime, "PROBE_INTERVAL_S", 0.0)
    monkeypatch.setattr(site, "PAGE_INTERVAL_S", 0.0)
    monkeypatch.setattr(site, "PAGE_COOLDOWN_S", 0.0)
    monkeypatch.setattr(site, "PAGE_BUDGET", Budget(attempts=4, pauses=(0.0,), limit_s=UNTIMED))
    monkeypatch.setattr(site, "RELEASE_BUDGET", Budget(attempts=3, pauses=(0.0,), limit_s=UNTIMED))


@pytest.fixture(autouse=True)
def storage_without_leftovers():
    """What a test's stalled storage call still owns must not reach the next test."""
    from core import storage
    yield
    storage._holds.clear()
    storage._waiting.clear()
