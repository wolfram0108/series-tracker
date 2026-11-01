"""The guards of stage 10 (docs/missions/voice-alignment/stage-10/tools/checks), proved on synthetic trees.

Every guard gets a sample of each kind of violation, which must be caught at the right line, and a clean sample,
which must pass. The trees live in tmp_path; git-based guards get a throw-away repository. Nothing here reads the
real tree: the run on the real tree is a measurement of its own, not a test.
"""
import ast
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

# STAGE10_CHECKS_DIR points the tests at a copy of the guards, so that a deliberately broken copy can be run against them.
CHECKS = Path(os.environ.get("STAGE10_CHECKS_DIR") or Path(__file__).resolve().parent.parent / "docs" / "missions"
              / "voice-alignment" / "stage-10" / "tools" / "checks")
_bytecode = sys.dont_write_bytecode
sys.dont_write_bytecode = True  # imported guards must not leave __pycache__ in the documents folder
sys.path.insert(0, str(CHECKS))
try:
    import bus_catalog
    import comment_language
    import commit_attribution
    import guard_common
    import module_imports
    import no_wait_no_poll
    import revision_registry
    import table_owners
    import ui_styles
finally:
    sys.dont_write_bytecode = _bytecode

from guard_common import Outcome, Violation, run_guard  # noqa: E402


def write_tree(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
    return root


def places(outcome: Outcome) -> set[tuple[str, int]]:
    return {(v.path, v.line) for v in outcome.violations}


def line_of(root: Path, name: str, needle: str) -> int:
    for number, line in enumerate((root / name).read_text(encoding="utf-8").splitlines(), 1):
        if needle in line:
            return number
    raise AssertionError(f"{needle!r} is not in {name}")


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org",
                           "-c", "commit.gpgsign=false", *args], capture_output=True, text=True, check=True)
    return done.stdout.strip()


def two_commits(tmp_path: Path, base: dict[str, str], head: dict[str, str], message: str = "head") -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_tree(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    base_sha = git(repo, "rev-parse", "HEAD")
    write_tree(repo, head)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return repo, base_sha


# --- guard_common: report, evaluator, diff reader, comment scanner ---------------------------------------------

def test_the_trigger_line_and_the_exit_code_follow_the_outcome(capsys):
    assert run_guard("g", lambda args: Outcome(), []) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "ИЗМЕРЕНИЕ g: PASS"
    assert run_guard("g", lambda args: Outcome([Violation("a.py", 3, "причина")]), []) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "a.py:3 причина" and lines[-1] == "ИЗМЕРЕНИЕ g: FAIL 1"


def test_a_broken_guard_is_a_device_defect_not_a_verdict(capsys):
    def broken(args):
        raise RuntimeError("boom")

    assert run_guard("g", broken, []) == 2
    assert capsys.readouterr().out.splitlines()[-1] == "ИЗМЕРЕНИЕ g: FAIL прибор"


def test_permitted_places_and_notes_never_count_as_violations(capsys):
    outcome = Outcome(allowed=[Violation("a.py", 1, "ground")], notes=["something"])
    assert run_guard("g", lambda args: outcome, []) == 0
    out = capsys.readouterr().out
    assert "ДОПУЩЕНО a.py:1 ground" in out and "ИНФО something" in out


@pytest.mark.parametrize("pattern, topic, expected", [
    ("a.b", "a.b", True), ("a.*", "a.b", True), ("a.*", "a.b.c", False), ("a.#", "a.b.c", True),
    ("#", "x", True), ("a.b", "a", False), ("a.b.c", "a.b", False), ("*.b", "a.b", True), ("a.#.b", "a.x.b", False),
])
def test_topic_matching_follows_the_rule_of_the_bus(pattern, topic, expected):
    from core.envelope import topic_matches
    assert guard_common.topic_matches(pattern, topic) is expected
    assert topic_matches(pattern, topic) is expected


def evaluate(source: str, expression: str, **kw):
    tree = guard_common.parse_python(textwrap.dedent(source))
    call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "id", "") == expression)
    return guard_common.Evaluator(tree, kw.get("known")).values(call.args[0], kw.get("unresolved"))


@pytest.mark.parametrize("source, expected", [
    ('f("a.b")', {"a.b"}),
    ('NAME = "a.b"\nf(NAME)', {"a.b"}),
    ('def g(flag):\n    topic = "a.x" if flag else "a.y"\n    f(topic)', {"a.x", "a.y"}),
    ('def g():\n    for t in ("a.x", "a.y"):\n        f(t)', {"a.x", "a.y"}),
    ('ITEMS = ("a.x", "a.y")\ndef g():\n    for t in ITEMS:\n        f(t)', {"a.x", "a.y"}),
    ('def g(what):\n    f(f"a.{what}")\ng("added")\ng("removed")', {"a.added", "a.removed"}),
    ('class C:\n    def h(self, what):\n        f(f"a.{what}")\n    def k(self):\n        self.h("up")', {"a.up"}),
    ('def g(what="z"):\n    f(f"a.{what}")\ng()', {"a.z"}),
    ('f("a." + "b")', {"a.b"}),
])
def test_the_evaluator_finds_the_strings_an_expression_can_take(source, expected):
    assert evaluate(source, "f") == expected


@pytest.mark.parametrize("source", ['def g(x):\n    f(x)', 'f(compute())', 'def g(x):\n    f(x)\ng(other())'])
def test_the_evaluator_gives_up_instead_of_guessing(source):
    assert evaluate(source, "f") is None


def test_an_unknown_part_of_an_f_string_becomes_the_given_stand_in():
    assert evaluate('def g(x):\n    f(f"a.{x}")', "f", unresolved="*") == {"a.*"}


def test_an_imported_constant_is_taken_from_the_known_names():
    assert evaluate("from m import NAME\nf(NAME)", "f", known={"NAME": {"a.b"}}) == {"a.b"}


def test_the_diff_reader_returns_new_side_lines_of_added_and_changed_lines(tmp_path):
    repo, base = two_commits(tmp_path, {"a.py": "one\ntwo\nthree\n"}, {"a.py": "one\nTWO\nthree\nfour\nfive\n"})
    assert guard_common.added_lines(repo, base, "HEAD", ["*.py"]) == {"a.py": {2, 4, 5}}


def test_the_working_tree_mode_sees_uncommitted_and_untracked_work(tmp_path):
    repo, base = two_commits(tmp_path, {"a.py": "one\n"}, {"a.py": "one\nmid\n"})
    (repo / "a.py").write_text("one\nmid\ntwo\n", encoding="utf-8")
    (repo / "b.py").write_text("x\ny\n", encoding="utf-8")
    assert guard_common.added_lines(repo, base, "HEAD", ["*.py"]) == {"a.py": {2}}
    assert guard_common.added_lines(repo, base, guard_common.WORKTREE, ["*.py"]) == {"a.py": {2, 3}, "b.py": {1, 2}}


def comment_texts(source: str, filename: str) -> list[tuple[int, str]]:
    return [(c.first_line, c.text) for c in guard_common.web_comments(textwrap.dedent(source), filename)]


@pytest.mark.parametrize("source, expected", [
    ('const a = 1 // tail\n', [(1, "// tail")]),
    ('const u = "https://x.org/a"\n', []),
    ("const u = 'a // not'\n", []),
    ('const t = `x ${a} // inside`\n', []),
    ('const t = `x ${ b /* real */ }`\n', [(1, "/* real */")]),
    ('const r = /a\\/\\/b/\n// after\n', [(2, "// after")]),
    ('const r = /[/*]/\nconst q = 1 /* two\nlines */\n', [(2, "/* two\nlines */")]),
    ('const d = a / b // half\n', [(1, "// half")]),
])
def test_the_scanner_finds_comments_of_typescript_and_nothing_inside_strings(source, expected):
    assert comment_texts(source, "a.ts") == expected


def test_the_scanner_knows_the_three_regions_of_a_single_file_component():
    source = """
        <template>
          <div a="//x"> text <!-- tpl --></div>
        </template>
        <script setup lang="ts">
        const a = 1 // code
        </script>
        <style>
        .a { background: url(//cdn/x.png) } /* css */
        </style>
    """
    assert {text for _line, text in comment_texts(source, "a.vue")} == {"<!-- tpl -->", "// code", "/* css */"}


# --- module_imports ------------------------------------------------------------------------------------------

CLEAN_MODULES = {
    "modules/a/module.py": """
        import asyncio
        import httpx
        from core.module import BaseModule
        from .repository import Repo
        from . import helpers
        from modules.a.helpers import helper
    """,
    "modules/a/repository.py": "class Repo: ...\n",
    "modules/a/helpers.py": "def helper(): ...\n",
    "modules/a/sub/deep.py": "from ..repository import Repo\nfrom ...a.helpers import helper\n",
    "modules/b/module.py": "from core.bus import Bus\n",
    "run.py": "from modules.a.module import X\nfrom modules.b import y\n",
}


def test_a_module_that_imports_only_core_itself_and_libraries_passes(tmp_path):
    root = write_tree(tmp_path, CLEAN_MODULES)
    assert module_imports.check_module_imports(root).violations == []


@pytest.mark.parametrize("code", [
    "from modules.b import thing",
    "from modules.b.module import thing",
    "import modules.b.module",
    "from modules import b",
    "from ..b import thing",
    "from ..b.module import thing",
    "from .. import b",
    "from ... import thing",
    "import run",
    "from tests.conftest import x",
    "import importlib\nimportlib.import_module('modules.b.module')",
])
def test_every_way_to_reach_another_module_is_a_violation(tmp_path, code):
    root = write_tree(tmp_path, {**CLEAN_MODULES, "modules/a/leak.py": code + "\n"})
    outcome = module_imports.check_module_imports(root)
    assert {path for path, _line in places(outcome)} == {"modules/a/leak.py"}


def test_an_import_inside_a_function_and_under_type_checking_counts_too(tmp_path):
    code = "def f():\n    from modules.b import x\n\nif False:\n    from modules.b import y\n"
    root = write_tree(tmp_path, {**CLEAN_MODULES, "modules/a/leak.py": code})
    assert places(module_imports.check_module_imports(root)) == {("modules/a/leak.py", 2), ("modules/a/leak.py", 5)}


# --- table_owners --------------------------------------------------------------------------------------------

TABLES = {"alpha", "beta"}
OWNERS = {"alpha": ("m1", "ground"), "beta": ("m2", "ground")}
OWN_SQL = {
    "modules/m1/repository.py": '''
        """SELECT * FROM beta in a docstring is prose."""
        class Repo:
            async def read(self):
                await self.db.execute("SELECT * FROM alpha WHERE id = ?")
                await self.db.execute(
                    "INSERT INTO alpha (id) VALUES (?)"
                    " ON CONFLICT (id) DO UPDATE SET id = excluded.id")
    ''',
    "modules/m2/repository.py": 'QUERY = "SELECT * FROM beta b"\n',
}


def test_tables_mentioned_only_by_their_owners_pass(tmp_path):
    root = write_tree(tmp_path, OWN_SQL)
    assert table_owners.check_table_owners(root, TABLES, OWNERS).violations == []


@pytest.mark.parametrize("sql", [
    "SELECT * FROM alpha",
    "INSERT INTO alpha (id) VALUES (1)",
    "INSERT OR REPLACE INTO alpha (id) VALUES (1)",
    "INSERT OR IGNORE INTO alpha (id) VALUES (1)",
    "REPLACE INTO alpha (id) VALUES (1)",
    "UPDATE alpha SET id = 1",
    "UPDATE OR REPLACE alpha SET id = 1",
    "DELETE FROM alpha WHERE id = 1",
    "SELECT 1 FROM beta JOIN alpha ON 1",
    "select * from alpha",
])
def test_a_mention_of_a_foreign_table_in_any_sql_form_is_a_violation(tmp_path, sql):
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/leak.py": f'Q = "{sql}"\n'})
    outcome = table_owners.check_table_owners(root, TABLES, OWNERS)
    assert places(outcome) == {("modules/m2/leak.py", 1)}
    assert "alpha" in outcome.violations[0].reason and "m1" in outcome.violations[0].reason


def test_a_multi_line_statement_is_reported_at_the_line_of_the_table(tmp_path):
    code = 'Q = ("SELECT id "\n     "FROM beta "\n     "JOIN alpha ON 1")\n'
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/leak.py": code})
    assert places(table_owners.check_table_owners(root, TABLES, OWNERS)) == {("modules/m2/leak.py", 3)}


def test_an_f_string_with_a_known_part_is_read_and_prose_is_not_sql(tmp_path):
    code = ('COLUMNS = "id"\n'
            'Q = f"SELECT {COLUMNS} FROM alpha"\n'
            'MESSAGE = "update the alpha table from the file"\n'
            'def f():\n    """DELETE FROM alpha"""\n')
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/leak.py": code})
    assert places(table_owners.check_table_owners(root, TABLES, OWNERS)) == {("modules/m2/leak.py", 2)}


def test_a_loop_over_literal_table_names_counts_as_a_mention_of_each(tmp_path):
    code = ('def purge(conn):\n    for table in ("alpha", "beta"):\n'
            '        conn.execute(f"DELETE FROM {table} WHERE series_id = ?")\n')
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/purge.py": code})
    outcome = table_owners.check_table_owners(root, TABLES, OWNERS)
    assert places(outcome) == {("modules/m2/purge.py", 3)} and "alpha" in outcome.violations[0].reason


def test_a_table_name_that_cannot_be_known_is_a_violation_unless_the_place_is_permitted(tmp_path):
    code = 'def read(name):\n    return f\'SELECT * FROM "{name}"\'\n'
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/dyn.py": code})
    assert places(table_owners.check_table_owners(root, TABLES, OWNERS)) == {("modules/m2/dyn.py", 2)}
    outcome = table_owners.check_table_owners(root, TABLES, OWNERS, [("modules/m2/dyn.py", "read", "debug tool")])
    assert outcome.violations == [] and [v.line for v in outcome.allowed] == [2]


def test_the_ledger_must_be_complete_and_alive(tmp_path):
    root = write_tree(tmp_path, OWN_SQL)
    outcome = table_owners.check_table_owners(root, TABLES | {"gamma"}, {**OWNERS, "delta": ("m1", "g")})
    reasons = " | ".join(v.reason for v in outcome.violations)
    assert "«gamma»" in reasons and "владелец не назначен" in reasons
    assert "«delta»" in reasons and "нет в миграциях" in reasons
    outcome = table_owners.check_table_owners(root, TABLES, {**OWNERS, "beta": ("m3", "g")})
    assert any("модуля-владельца m3" in v.reason for v in outcome.violations)


def test_a_table_that_no_migration_makes_is_a_violation(tmp_path):
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/ghost.py": 'Q = "SELECT * FROM ghost"\n'})
    outcome = table_owners.check_table_owners(root, TABLES, OWNERS)
    assert places(outcome) == {("modules/m2/ghost.py", 1)} and "не создаётся миграциями" in outcome.violations[0].reason


def test_system_tables_and_table_functions_are_not_application_tables(tmp_path):
    code = 'Q1 = "SELECT name FROM sqlite_master"\nQ2 = "SELECT value FROM json_each(?)"\n'
    root = write_tree(tmp_path, {**OWN_SQL, "modules/m2/sys.py": code})
    assert table_owners.check_table_owners(root, TABLES, OWNERS).violations == []


def test_the_tables_come_from_the_real_alembic_run(tmp_path):
    root = write_tree(tmp_path, {
        "alembic.ini": "[alembic]\nscript_location = alembic\nsqlalchemy.url = sqlite:///unused.db\n",
        "alembic/env.py": '''
            import os
            from alembic import context
            from sqlalchemy import create_engine
            with create_engine(os.environ["ST_DB_URL"]).connect() as connection:
                context.configure(connection=connection)
                with context.begin_transaction():
                    context.run_migrations()
        ''',
        "alembic/versions/0001_init.py": '''
            from alembic import op
            revision = "0001"
            down_revision = None
            def upgrade():
                op.execute("CREATE TABLE alpha (id INTEGER)")
                op.execute("CREATE TABLE beta (id INTEGER)")
                op.execute("DROP TABLE beta")
            def downgrade():
                pass
        ''',
    })
    assert table_owners.migrated_tables(root) == {"alpha"}


# --- no_wait_no_poll -----------------------------------------------------------------------------------------

WAITS = '''
    import asyncio
    import time
    from asyncio import sleep as nap

    class Mod:
        async def poll(self):
            await asyncio.sleep(1)

        async def guarded(self, future):
            return await asyncio.wait_for(future, 5)

        async def yield_only(self):
            await asyncio.sleep(0)

        def blocking(self):
            time.sleep(2)

        async def aliased(self):
            await nap(3)
'''


def test_every_pause_or_timed_wait_outside_the_permitted_places_is_a_violation(tmp_path):
    root = write_tree(tmp_path, {"modules/m/module.py": WAITS})
    outcome = no_wait_no_poll.check_waits(root, [], [])
    assert sorted(v.line for v in outcome.violations) == [7, 10, 16, 19]  # sleep(0) is a yield, an alias is still a pause
    assert all("вне допущенных мест" in v.reason for v in outcome.violations)


def test_a_permitted_function_is_listed_and_never_a_violation(tmp_path):
    root = write_tree(tmp_path, {"modules/m/module.py": WAITS})
    allowed = [("modules/m/module.py", "Mod.poll", "schedule"), ("modules/m/module.py", "Mod.guarded", "safeguard"),
               ("modules/m/module.py", "Mod.blocking", "pace"), ("modules/m/module.py", "Mod.aliased", "pace")]
    outcome = no_wait_no_poll.check_waits(root, allowed, [])
    assert outcome.violations == [] and sorted(v.line for v in outcome.allowed) == [7, 10, 16, 19]


def test_a_whole_file_can_be_permitted_and_a_stale_permit_is_a_violation(tmp_path):
    root = write_tree(tmp_path, {"modules/m/module.py": WAITS, "modules/m/policy.py": WAITS})
    allowed = [("modules/m/policy.py", "*", "the policy"), ("modules/m/module.py", "Mod.poll", "x"),
               ("modules/m/module.py", "Mod.guarded", "x"), ("modules/m/module.py", "Mod.blocking", "x"),
               ("modules/m/module.py", "Mod.aliased", "x"), ("modules/m/module.py", "Mod.gone", "x")]
    outcome = no_wait_no_poll.check_waits(root, allowed, [])
    assert places(outcome) == {("modules/m/module.py", 0)} and "Mod.gone" in outcome.violations[0].reason


WEB = '''
    <script setup lang="ts">
    // setInterval(poll, 1000) in a comment is not a timer
    let debounceTimer: number | undefined
    function typed() { debounceTimer = setTimeout(parse, 500) }
    setInterval(poll, 1000)
    window.setInterval(poll, 1000)
    const later = () => setTimeout(load, 2000)
    </script>
'''


def test_timers_in_web_source_are_violations_except_the_permitted_ones(tmp_path):
    root = write_tree(tmp_path, {"web/src/A.vue": WEB, "web/src/A.test.ts": "setInterval(f, 1)\n"})
    allowed = [("web/src/A.vue", "debounceTimer", "debounce of input")]
    outcome = no_wait_no_poll.check_waits(root, [], allowed)
    assert places(outcome) == {("web/src/A.vue", 5), ("web/src/A.vue", 6), ("web/src/A.vue", 7)}
    assert [v.line for v in outcome.allowed] == [4]


def test_a_timer_permit_that_matches_nothing_is_a_violation(tmp_path):
    root = write_tree(tmp_path, {"web/src/A.vue": WEB})
    allowed = [("web/src/A.vue", "debounceTimer", "x"), ("web/src/A.vue", "vanished", "x")]
    outcome = no_wait_no_poll.check_waits(root, [], allowed)
    assert ("web/src/A.vue", 0) in places(outcome) and "vanished" in next(v.reason for v in outcome.violations if v.line == 0)


# --- bus_catalog ---------------------------------------------------------------------------------------------

BUS_CODE = {
    "modules/a/module.py": '''
        from core.module import BaseModule

        READY_TOPIC = "a.ready"

        class A(BaseModule):
            def register(self):
                self.handle("a.get", self.on_get)
                self.handle("a.queue.clear", self.on_clear)
                self.handle("b.changed", self.on_b)

            def go(self):
                self.publish_event("a.changed", {})
                self.send_command("b.do", {})
                self._announce("added")
                self._announce("updated")
                self._announce("removed")

            def _announce(self, what):
                self.publish_event(f"a.source.{what}", {})

            async def ask(self):
                await self.client.request("GET", "http://example.org")
                return await self.request("b.ask", {})
    ''',
    "modules/b/module.py": '''
        from core.module import BaseModule

        class B(BaseModule):
            def register(self):
                self.handle("b.do", self.on_do)
                self.handle("b.ask", self.on_ask)

            def changed(self):
                self.publish_event("b.changed", {})
    ''',
}
BUS_DOC = '''
    # Map

    | Модуль | Топики |
    |---|---|
    | a | `a.get {id}` → x; `.queue.clear`; `a.ready` |
    | b | `b.do`, `b.ask` |

    ## Events

    | Событие | Издатель | Подписчики | SSE |
    |---|---|---|---|
    | `a.changed {id}` | a | b | — |
    | `a.source.added \\| .updated \\| .removed {id}` | a | b | — |
    | `b.changed` | b | a | — |
'''


def bus_tree(tmp_path, doc=BUS_DOC, architecture=None):
    files = {**BUS_CODE, "contracts/bus_topics.md": doc}
    if architecture is not None:
        files[bus_catalog.ARCHITECTURE_DOC] = architecture
    return write_tree(tmp_path, files)


def test_code_and_catalog_that_agree_pass_with_shorthand_alternatives_and_f_strings(tmp_path):
    outcome = bus_catalog.check_bus_catalog(bus_tree(tmp_path))
    assert outcome.violations == []
    assert not any("GET" in note for note in outcome.notes)


def test_a_topic_in_code_without_a_record_is_a_violation(tmp_path):
    outcome = bus_catalog.check_bus_catalog(bus_tree(tmp_path, BUS_DOC.replace("`b.do`, `b.ask`", "`b.do`")))
    assert [(v.path, "b.ask" in v.reason) for v in outcome.violations] == [("modules/a/module.py", True)]


def test_a_record_without_code_is_a_violation_at_its_line(tmp_path):
    doc = BUS_DOC.replace("`a.ready`", "`a.ready`, `a.ghost {x}`")
    outcome = bus_catalog.check_bus_catalog(bus_tree(tmp_path, doc))
    assert [(v.path, v.line) for v in outcome.violations] == [("contracts/bus_topics.md", 5)]
    assert "a.ghost" in outcome.violations[0].reason


def test_the_two_readings_of_a_leading_dot_are_both_tried():
    doc = "| Модуль | Топики |\n|---|---|\n| a | `a.queue.get`; `.queue.clear`; `.drop`; `a.x.y/z` |\n"
    entries, _ = bus_catalog.doc_topics(doc, {"a"})
    assert [e.readings for e in entries] == [("a.queue.get",), ("a.queue.clear", "a.queue.queue.clear"),
                                             ("a.queue.drop", "a.drop"), ("a.x.y",), ("a.x.z",)]


def test_an_escaped_pipe_does_not_split_a_cell():
    doc = "| Модуль | Топики |\n|---|---|\n| a | state: ready \\| failed; `a.get` |\n"
    entries, _ = bus_catalog.doc_topics(doc, {"a"})
    assert [e.readings for e in entries] == [("a.get",)]


def test_a_wildcard_in_the_catalog_covers_the_topics_it_matches(tmp_path):
    doc = BUS_DOC.replace("| `a.source.added \\| .updated \\| .removed {id}` | a | b | — |", "| `a.source.*` | a | b | — |")
    assert bus_catalog.check_bus_catalog(bus_tree(tmp_path, doc)).violations == []


def test_prose_tokens_with_dots_are_set_aside_not_taken_for_topics(tmp_path):
    doc = BUS_DOC.replace("`b.do`, `b.ask`", "`b.do`, `b.ask`, `data.sources` is a field")
    outcome = bus_catalog.check_bus_catalog(bus_tree(tmp_path, doc))
    assert outcome.violations == [] and any("data.sources" in note for note in outcome.notes)


def test_an_unresolvable_topic_is_a_note_not_a_violation(tmp_path):
    code = {**BUS_CODE, "modules/b/dyn.py": "async def f(self, topic):\n    return await self.request(topic, {})\n"}
    root = write_tree(tmp_path, {**code, "contracts/bus_topics.md": BUS_DOC})
    outcome = bus_catalog.check_bus_catalog(root)
    assert outcome.violations == [] and any("modules/b/dyn.py:2" in note for note in outcome.notes)


ARCHITECTURE = '''
    ## 4. Other

    | Топик | Вид |
    |---|---|
    | `a.not_in_section_five` | x |

    ## 5. Catalog

    | Топик | Вид | Владелец |
    |---|---|---|
    | `a.get` | запрос | a |
    | `b.do` / `.ask` | команда | b |
    | `a.missing` | запрос | a |

    ## 6. Next
'''


def test_section_five_of_the_architecture_needs_code_and_a_record_in_the_catalog(tmp_path):
    outcome = bus_catalog.check_bus_catalog(bus_tree(tmp_path, architecture=ARCHITECTURE))
    reasons = sorted(v.reason for v in outcome.violations)
    assert len(reasons) == 2 and all("a.missing" in r for r in reasons)
    assert any("без кода" in r for r in reasons) and any("не записан" in r for r in reasons)


def test_a_missing_catalog_is_a_violation_not_a_crash(tmp_path):
    root = write_tree(tmp_path, BUS_CODE)
    assert places(bus_catalog.check_bus_catalog(root)) == {("contracts/bus_topics.md", 0)}


# --- revision_registry ---------------------------------------------------------------------------------------

REVISION = '''
    # Registry

    ## 1. First

    | Точка | Сейчас | Статус | Новое место |
    |---|---|---|---|
    | `gone_fn` | x | удалена | y — сделано (П-1) |
    | `Old.method` | x | удалена | y — сделано (П-1) |
    | `kept` | x | перепроектирована | z |
    | `a.gone` и `OldClass` | x | удалена | сделано (П-2) |
    | `legacy.py`, `LIMIT`, `/api/old` | x | удалена | сделано (П-3) |
    | no code here | x | удалена | сделано (П-3) |

    ## 2. Second

    | Точка | Статус | Причина |
    |---|---|---|
    | the point | подтверждена | r |
    | `later` | удалена | будет позже |
'''
SYMBOLS = '''
    # comment
    def:gone_fn — откуда: 1/1
    def:Old.method — откуда: 1/2
    topic:a.gone — откуда: 1/4
    class:OldClass — откуда: 1/4
    file:legacy.py — откуда: 1/5
    const:LIMIT — откуда: 1/5
    literal:/api/old — откуда: 1/5
    none:nothing — откуда: 1/6 — причина: no symbol to look for
'''
REGISTRY_CODE = {
    "modules/m/module.py": '''
        class Other:
            def method(self): ...

        def alive(): ...
    ''',
    "core/x.py": "VALUE = 1\n",
}


def registry_tree(tmp_path, code=None, symbols=SYMBOLS, revision=REVISION):
    return write_tree(tmp_path, {
        **(REGISTRY_CODE if code is None else code),
        revision_registry.REVISION_DOC: revision,
        revision_registry.SYMBOLS_FILE: symbols,
    })


def test_a_registry_whose_removed_symbols_are_gone_passes(tmp_path):
    assert revision_registry.check_revision(registry_tree(tmp_path)).violations == []


@pytest.mark.parametrize("name, code, fragment", [
    ("def", "def gone_fn(): ...", "gone_fn"),
    ("method of the pinned class", "class Old:\n    def method(self): ...", "Old.method"),
    ("topic", "class A:\n    def register(self):\n        self.handle('a.gone', self.f)", "a.gone"),
    ("topic requested", "class A:\n    async def f(self):\n        await self.request('a.gone', {})", "a.gone"),
    ("class", "class OldClass: ...", "OldClass"),
    ("constant", "LIMIT = 5", "LIMIT"),
    ("literal", "ROUTE = '/api/old'", "/api/old"),
])
def test_a_removed_symbol_that_is_defined_again_is_found_at_its_line(tmp_path, name, code, fragment):
    files = {**REGISTRY_CODE, "modules/m/back.py": code + "\n"}
    outcome = revision_registry.check_revision(registry_tree(tmp_path, files))
    assert fragment in " ".join(v.reason for v in outcome.violations)
    assert all(v.path == "modules/m/back.py" for v in outcome.violations)


def test_a_route_is_compared_without_the_names_of_its_path_parameters(tmp_path):
    revision = REVISION.replace("`legacy.py`, `LIMIT`, `/api/old`", "`legacy.py`, `LIMIT`, `/api/old/{id}`")
    symbols = SYMBOLS.replace("literal:/api/old", "literal:/api/old/{id}")
    assert revision_registry.check_revision(registry_tree(tmp_path, revision=revision, symbols=symbols)).violations == []
    files = {**REGISTRY_CODE, "modules/m/back.py": "ROUTE = '/api/old/{series_id}'\n"}
    outcome = revision_registry.check_revision(registry_tree(tmp_path / "again", files, symbols, revision))
    assert places(outcome) == {("modules/m/back.py", 1)}


def test_a_removed_file_that_is_back_is_found(tmp_path):
    files = {**REGISTRY_CODE, "legacy.py": "x = 1\n"}
    assert places(revision_registry.check_revision(registry_tree(tmp_path, files))) == {("legacy.py", 0)}


def test_a_mention_in_a_comment_or_a_docstring_is_not_a_definition(tmp_path):
    code = {**REGISTRY_CODE, "modules/m/doc.py": '"""gone_fn and OldClass are gone."""\n# def gone_fn(): ...\n'}
    assert revision_registry.check_revision(registry_tree(tmp_path, code)).violations == []


def test_a_name_that_came_back_for_another_reason_is_permitted_with_its_reason(tmp_path):
    symbols = SYMBOLS.replace("class:OldClass — откуда: 1/4",
                              "class:OldClass — откуда: 1/4 — допуск: modules/m/back.py — причина: new contract")
    files = {**REGISTRY_CODE, "modules/m/back.py": "class OldClass: ...\n"}
    outcome = revision_registry.check_revision(registry_tree(tmp_path, files, symbols))
    assert outcome.violations == [] and "new contract" in outcome.allowed[0].reason


@pytest.mark.parametrize("revision, fragment", [
    (REVISION.replace("| `kept` | x | перепроектирована | z |", "| `kept` | x | | z |"), "нет допустимого статуса"),
    (REVISION.replace("| `kept` | x | перепроектирована | z |", "| `kept` | x | перепроектирована |"), "столбцов"),
])
def test_every_row_of_the_registry_needs_a_status_and_a_whole_shape(tmp_path, revision, fragment):
    outcome = revision_registry.check_revision(registry_tree(tmp_path, revision=revision))
    assert fragment in " ".join(v.reason for v in outcome.violations)


@pytest.mark.parametrize("symbols, fragment", [
    (SYMBOLS.replace("none:nothing — откуда: 1/6 — причина: no symbol to look for", ""), "ни символа, ни «none»"),
    (SYMBOLS + "def:gone_fn — откуда: 9/9\n", "несуществующую строку"),
    (SYMBOLS + "def:foreign_name — откуда: 1/1\n", "не стоит в тексте строки"),
    (SYMBOLS + "nonsense\n", "строка не разобрана"),
    (SYMBOLS.replace("— причина: no symbol to look for", "— допуск: a.py"), "допуск без причины"),
])
def test_the_data_file_must_cover_every_removed_row_and_point_at_real_text(tmp_path, symbols, fragment):
    outcome = revision_registry.check_revision(registry_tree(tmp_path, symbols=symbols))
    assert fragment in " ".join(v.reason for v in outcome.violations)


def test_a_missing_registry_or_data_file_is_a_violation(tmp_path):
    assert places(revision_registry.check_revision(write_tree(tmp_path, {}))) == {(revision_registry.REVISION_DOC, 0)}
    root = write_tree(tmp_path / "second", {revision_registry.REVISION_DOC: REVISION})
    assert (revision_registry.SYMBOLS_FILE, 0) in places(revision_registry.check_revision(root))


# --- comment_language ----------------------------------------------------------------------------------------

PY_BASE = {"modules/a/x.py": "# старый комментарий остаётся\nvalue = 1\n"}
PY_HEAD = {"modules/a/x.py": '''
    # старый комментарий остаётся
    value = 1
    # new русский комментарий
    text = "# это строка по-русски"
    def f():
        """Докстринг по-русски."""
        raise ValueError("сообщение по-русски")
    x = 2  # English tail
    y = 3  # хвост по-русски
    # English only
''', "modules/a/module.py": '''
    """Модуль a: контракт по-русски."""
    def g():
        """Тоже по-русски."""
''', "modules/a/other.py": '"""Контракт по-русски, но это не module.py."""\n'}


def test_a_russian_hash_comment_in_an_added_line_is_a_violation_and_nothing_else_is(tmp_path):
    repo, base = two_commits(tmp_path, PY_BASE, PY_HEAD)
    outcome = comment_language.check_comment_language(repo, base, "HEAD")
    assert places(outcome) == {("modules/a/x.py", 3), ("modules/a/x.py", 9)}


def test_docstrings_are_checked_only_on_request_and_the_contract_of_module_py_is_exempt(tmp_path):
    repo, base = two_commits(tmp_path, PY_BASE, PY_HEAD)
    outcome = comment_language.check_comment_language(repo, base, "HEAD", docstrings=True)
    assert places(outcome) == {("modules/a/x.py", 3), ("modules/a/x.py", 9), ("modules/a/x.py", 6),
                               ("modules/a/module.py", 3), ("modules/a/other.py", 1)}


def test_an_old_russian_comment_on_an_unchanged_line_is_not_judged(tmp_path):
    repo, base = two_commits(tmp_path, PY_BASE, {**PY_BASE, "modules/a/y.py": "# fine\n"})
    assert comment_language.check_comment_language(repo, base, "HEAD").violations == []


WEB_HEAD = {
    "web/src/a.ts": '''
        const url = "https://example.org/x" // English tail
        // русский комментарий
        /* ещё русский */
        const s = "// русский в строке"
        const re = /a\\/\\/b/ // fine
    ''',
    "web/src/B.vue": '''
        <template>
          <div>текст в шаблоне <!-- русский --></div>
        </template>
        <script setup lang="ts">
        // комментарий русский
        const a = 1
        </script>
        <style>
        /* русский */
        .a { color: red }
        </style>
    ''',
}


def test_russian_comments_of_typescript_vue_and_css_are_violations_and_strings_are_not(tmp_path):
    repo, base = two_commits(tmp_path, {"README.md": "x\n"}, WEB_HEAD)
    outcome = comment_language.check_comment_language(repo, base, "HEAD")
    assert places(outcome) == {("web/src/a.ts", 2), ("web/src/a.ts", 3), ("web/src/B.vue", 2), ("web/src/B.vue", 5),
                               ("web/src/B.vue", 9)}


def test_a_generated_file_is_skipped(tmp_path):
    head = {"web/src/api/schema.d.ts": "/** @description Русское описание */\nexport type A = 1\n"}
    repo, base = two_commits(tmp_path, {"README.md": "x\n"}, head)
    assert comment_language.check_comment_language(repo, base, "HEAD").violations == []


def test_uncommitted_and_untracked_work_is_checked_in_the_working_tree_mode(tmp_path):
    repo, base = two_commits(tmp_path, {"a.py": "x = 1\n"}, {"a.py": "x = 1\n# fine\n"})
    (repo / "a.py").write_text("x = 1\n# fine\n# русский\n", encoding="utf-8")
    (repo / "new.py").write_text("y = 1  # тоже русский\n", encoding="utf-8")
    assert comment_language.check_comment_language(repo, base, "HEAD").violations == []
    outcome = comment_language.check_comment_language(repo, base, guard_common.WORKTREE)
    assert places(outcome) == {("a.py", 3), ("new.py", 1)}


# --- commit_attribution --------------------------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    "Fix\n\nCo-Authored-By: Claude <noreply@anthropic.com>",
    "Generated with Claude Code",
    "Правки сделаны в Claude Code",
    "claude",
    "generated by a tool",
    "Сгенерировано автоматически",
    "Код сгенерирован с помощью ассистента",
    "правка от ИИ",
    "AI-assisted change",
    "fix with an AI",
    "made with ChatGPT",
    "co-authored-by: someone",
    "Исправлено нейросетью",
    "robot \U0001F916 inside",
])
def test_every_trace_of_an_assistant_in_a_message_is_found(tmp_path, message):
    repo, base = two_commits(tmp_path, {"a": "1\n"}, {"a": "2\n"}, message)
    assert commit_attribution.check_commits(repo, base).violations


@pytest.mark.parametrize("message", [
    "Этап 10, П-2: конструктор отвечает по ключам строк",
    "Fix the maintain script and the domain mail",
    "Правка: ввод адреса, новые тесты, стили",
    "Типы интерфейса — сгенерированные из схемы API",
])
def test_an_honest_message_passes_and_words_are_matched_whole(tmp_path, message):
    repo, base = two_commits(tmp_path, {"a": "1\n"}, {"a": "2\n"}, message)
    assert commit_attribution.check_commits(repo, base).violations == []


def test_only_the_range_after_the_base_is_judged_and_a_message_can_be_checked_before_the_commit(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "a").write_text("1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Co-Authored-By: Claude")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "a").write_text("2\n")
    git(repo, "commit", "-q", "-am", "clean")
    assert commit_attribution.check_commits(repo, base).violations == []
    assert commit_attribution.check_message("subject\n\nCo-Authored-By: x").violations[0].line == 3


# --- ui_styles -----------------------------------------------------------------------------------------------

UI_BASE = {"web/src/s.css": ".old { width: 300px } @media (min-width: 600px) { .x { top: 1px } }\n"}
UI_CSS = '''
    .old { width: 300px } @media (min-width: 600px) { .x { top: 1px } }
    @media (min-width: 600px) { .a { color: red } } /* BAD */
    @media (max-width: 40rem) { .a { color: red } } /* BAD */
    @media (width >= 600px) { .a { color: red } } /* BAD */
    @container (min-width: 20rem) { .a { color: red } } /* BAD */
    @media (prefers-color-scheme: dark) { .a { color: red } }
    @media (hover: hover) { .a { color: red } }
    .a { width: 300px } /* BAD */
    .a { min-width: 12px; max-height: 3.5px } /* BAD */
    .a { width: clamp(10rem, 30%, 300px) }
    .a { width: min(100%, 20rem); height: auto; flex: 1 1 0 }
    .a { margin: 0 8px } /* BAD */
    .a { padding-inline: 12px } /* BAD */
    .a { gap: 0px; top: 0; margin: 0 auto }
    .a { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)) } /* BAD */
    .a { grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr)) }
    .a { border: 1px solid red; outline: 1px solid red }
    .a { border: 2px solid red } /* BAD */
    .a { font-size: 14px; line-height: 20px; letter-spacing: 1px; border-radius: 4px }
    .a { box-shadow: 0 0 4px red; transform: translateX(12px); transition: all 200ms }
    :root { --card-width: 300px; } /* BAD */
    :root { --font-size: 14px; --radius: 6px; --gap: 1rem }
    .a { /* width: 300px */ color: red }
    .a { width: var(--card-width) }
'''
UI_TS = '''
    const style = { minWidth: '200px' } // BAD
    const style2 = { width: '10rem', height: '20px' } // BAD
    const ok = { width: '10rem', fontSize: '14px' }
    const q = window.matchMedia('(max-width: 600px)') // BAD
    const dark = window.matchMedia('(prefers-color-scheme: dark)')
    setInterval(poll, 1000) // BAD
    debounceTimer = setTimeout(parse, 500)
'''


def marked_lines(text: str) -> set[int]:
    return {number for number, line in enumerate(textwrap.dedent(text).lstrip("\n").splitlines(), 1) if "BAD" in line}


def test_only_added_lines_with_width_thresholds_or_fixed_px_layout_sizes_are_violations(tmp_path):
    repo, base = two_commits(tmp_path, UI_BASE, {"web/src/s.css": textwrap.dedent(UI_CSS).lstrip("\n"),
                                                 "web/src/t.ts": textwrap.dedent(UI_TS).lstrip("\n")})
    outcome = ui_styles.check_ui_styles(repo, base, "HEAD", [("web/src/t.ts", "debounceTimer", "debounce")])
    assert {line for path, line in places(outcome) if path == "web/src/s.css"} == marked_lines(UI_CSS)
    assert {line for path, line in places(outcome) if path == "web/src/t.ts"} == marked_lines(UI_TS)
    assert [v.line for v in outcome.allowed] == [7]


def test_styles_inside_a_single_file_component_and_skipped_files(tmp_path):
    vue = '''
        <template><div style="width: 200px" /></template>
        <style scoped>
        .a { height: 40px }
        </style>
    '''
    head = {"web/src/C.vue": vue, "web/src/api/schema.d.ts": "width: 300px\n", "web/src/C.test.ts": "setInterval(f, 1)\n"}
    repo, base = two_commits(tmp_path, {"README.md": "x\n"}, head)
    assert places(ui_styles.check_ui_styles(repo, base, "HEAD")) == {("web/src/C.vue", 1), ("web/src/C.vue", 3)}


# --- every guard as a script ---------------------------------------------------------------------------------

@pytest.mark.parametrize("script, arguments", [
    ("module_imports.py", []),
    ("comment_language.py", ["--base", "HEAD"]),
    ("commit_attribution.py", ["--base", "HEAD"]),
    ("ui_styles.py", ["--base", "HEAD"]),
])
def test_a_script_prints_its_trigger_line_and_returns_zero_on_a_clean_tree(tmp_path, script, arguments):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write_tree(repo, {"modules/a/module.py": "from core.bus import Bus\n"})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    done = subprocess.run([sys.executable, "-B", str(CHECKS / script), "--root", str(repo), *arguments],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 0, done.stdout + done.stderr
    assert re.search(r"^ИЗМЕРЕНИЕ \S+: PASS$", done.stdout, re.M)


def test_a_script_on_a_dirty_tree_prints_the_violation_line_and_returns_one(tmp_path):
    root = write_tree(tmp_path, {"modules/a/module.py": "from modules.b import x\n"})
    done = subprocess.run([sys.executable, "-B", str(CHECKS / "module_imports.py"), "--root", str(root)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 1
    lines = done.stdout.splitlines()
    assert lines[0].startswith("modules/a/module.py:1 ") and lines[-1] == "ИЗМЕРЕНИЕ module_imports: FAIL 1"


def test_the_wait_guard_script_prints_its_trigger_even_when_the_permits_are_stale(tmp_path):
    root = write_tree(tmp_path, {"modules/a/module.py": "x = 1\n"})
    done = subprocess.run([sys.executable, "-B", str(CHECKS / "no_wait_no_poll.py"), "--root", str(root)],
                          capture_output=True, text=True, encoding="utf-8")
    assert done.returncode == 1 and re.search(r"^ИЗМЕРЕНИЕ no_wait_no_poll: FAIL \d+$", done.stdout, re.M)
