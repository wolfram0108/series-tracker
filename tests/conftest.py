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
