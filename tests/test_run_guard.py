"""The limit of reading that belongs to a stand does not start a process that is not one."""
import os
import subprocess
import sys


def run_with(env_extra, tmp_path):
    env = {"PATH": os.environ["PATH"], "ST_DB_PATH": str(tmp_path / "x.db"), "ST_LOG_DIR": str(tmp_path), **env_extra}
    return subprocess.run([sys.executable, "-c", "import run"], env=env, cwd=".", capture_output=True, text=True, timeout=60)


def test_a_process_that_is_no_stand_does_not_start_with_a_limit_of_reading(tmp_path):
    done = run_with({"ST_READ_RECENT": "10"}, tmp_path)
    assert done.returncode != 0 and "belongs to a stand only" in done.stderr


def test_a_process_does_not_start_on_a_database_behind_its_code_and_says_what_to_run(tmp_path):
    done = run_with({}, tmp_path)  # x.db: no file, no migration
    assert done.returncode != 0 and "SchemaBehind" in done.stderr and "alembic upgrade head" in done.stderr
