"""The TypeScript types of the API: an isolated instance of the work tree on a free port, `openapi-typescript` over its
schema into `web/src/api/schema.d.ts`, and out. The last line is `SCHEMA_DONE` or `SCHEMA_FAIL <reason>`. Run it only through
`progon_runner.py` with that line as the predicate. It leaves the running system (:5000) alone, which `npm run gen:api` does not."""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = str(Path(__file__).resolve().parents[6])
PY = sys.executable

work = tempfile.mkdtemp(prefix="schema-stand-")
db = os.path.join(work, "app.db")
env = {**os.environ, "ST_DB_PATH": db, "ST_DB_URL": f"sqlite:///{db}", "ST_AUTH_REQUIRED": "false",
       "ST_COOKIE_SECURE": "false", "ST_SECRET_KEY": "schema", "ST_DOCS_ENABLED": "true", "ST_LOG_DIR": work}
for name in ("ST_QBIT_URL", "ST_CONFORM_URL"):
    env.pop(name, None)

migrated = subprocess.run([PY, "-m", "alembic", "upgrade", "head"], cwd=ROOT, env=env, capture_output=True, text=True)
if migrated.returncode != 0:
    print("SCHEMA_FAIL migrations", migrated.stderr[-400:])
    sys.exit(1)

probe = socket.socket()
probe.bind(("127.0.0.1", 0))
port = probe.getsockname()[1]
probe.close()

proc = subprocess.Popen([PY, "-m", "uvicorn", "run:app", "--host", "127.0.0.1", "--port", str(port)], cwd=ROOT,
                        env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
ready = threading.Event()
journal = open(os.path.join(work, "stand.log"), "w")


def pump() -> None:
    for line in proc.stdout:
        journal.write(line)
        journal.flush()
        if "Application startup complete" in line:
            ready.set()


threading.Thread(target=pump, daemon=True).start()
try:
    if not ready.wait(90):
        print("SCHEMA_FAIL the instance did not come up")
        sys.exit(1)
    made = subprocess.run(["npx", "openapi-typescript", f"http://127.0.0.1:{port}/openapi.json", "-o", "src/api/schema.d.ts"],
                          cwd=os.path.join(ROOT, "web"), capture_output=True, text=True)
    print("SCHEMA_DONE" if made.returncode == 0 else f"SCHEMA_FAIL {made.stderr[-400:]}")
finally:
    proc.terminate()
    try:
        proc.wait(15)
    except subprocess.TimeoutExpired:
        proc.kill()
    journal.close()
    shutil.rmtree(work, ignore_errors=True)
