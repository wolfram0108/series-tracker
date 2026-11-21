"""Root conftest: isolates test logging.

`core.logging.LOG_DIR` is read from ST_LOG_DIR at import time, and `get_logger()` → `configure()` attaches a
RotatingFileHandler to that folder. Without isolation the tests (including fake modules such as fake_backend and probe)
would write into the production `logs/` and pollute the real logs and the debug group filter. The temporary folder is
set BEFORE the first import of core.logging, at the top of this file, which pytest loads before any test module.
setdefault lets an external ST_LOG_DIR (CI) take priority.
"""
import os
import tempfile

os.environ.setdefault(
    "ST_LOG_DIR", tempfile.mkdtemp(prefix="st-test-logs-"))
