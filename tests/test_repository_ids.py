"""The id of a row a repository has just inserted comes from the database (`Database.insert`, the lastrowid of that insert).

Reading it back with a query ("the newest row of the series") gives the row of another writer when two insert at once, and no
row at all when a restart or a cascade deletes the new one in between: a scan of a video series failed on that once in a
hundred runs of the restart scenarios of the model of downloads."""
import re
from pathlib import Path

MODULES = Path(__file__).resolve().parent.parent / "modules"
READ_BACK = re.compile(r"ORDER\s+BY\s+id\s+DESC\s+LIMIT\s+1", re.IGNORECASE)


def test_no_repository_reads_the_id_of_its_new_row_back_by_a_query():
    found = [f"{path.relative_to(MODULES.parent)}:{number}"
             for path in sorted(MODULES.rglob("*.py"))
             for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
             if READ_BACK.search(line)]
    assert found == [], f"take the id from Database.insert: {found}"
