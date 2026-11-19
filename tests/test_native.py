"""The native core (docs/missions/rust-core) reads what the modules write: the rows of the layout written by the module
composition come back from the core field by field, and the core and the module name the same columns of the table.

The core is built from git (native/build.sh): a checkout without it fails here with the way to build it."""
import pytest

from core.contracts import CompositionRow, RowTrack
from core.db import Database
from modules.composition import repository
from schema_db import migrated

try:
    from core import _native
except ImportError:  # pragma: no cover - a checkout that did not build the core
    pytest.fail("the native core is not built: run native/build.sh", pytrace=False)


def test_the_core_and_the_module_name_the_same_columns_of_the_rows_of_the_layout():
    core = [name.strip() for name in _native.COLUMNS.split(",")]
    assert core == [name for name in repository._ROW_COLUMNS if name != "seq"]


def rows() -> list[CompositionRow]:
    common = dict(series_id=7, source_id=3, player="vk", state="playable")
    return [
        CompositionRow(**common, row_key="a", season=1, episode_start=1, episode_end=1, voice_group="G", voice_label="G",
                       voice_name="G", player_label="VK", group="grp", text="Серия 1", heights=[1080, 720], link="l",
                       video="v", timeline="t"),
        CompositionRow(**{**common, "state": "absent"}, row_key="b", excluded=True, exclusion="rule", voice_unknown=2,
                       heights=[], published="2026-11-19", stale=True),
        CompositionRow(**common, row_key="c", season=2, episode_start=3, episode_end=5, part=1, heights=[480],
                       track=RowTrack.SUBTITLES, language="ru", subtitle_type="signs"),
    ]


async def test_the_rows_the_module_writes_are_the_rows_the_core_reads(tmp_path):
    path = migrated(tmp_path / "base.db")
    repo = repository.CompositionRepository(Database(path))
    written = rows()
    await repo.write(7, rows=[row for row in written if row.track == RowTrack.SOUND],
                     subtitles=[row for row in written if row.track == RowTrack.SUBTITLES], rev=1)
    held = _native.layout_rows(str(path), 7)
    names = [name.strip() for name in _native.COLUMNS.split(",")]
    expected = [tuple(row.model_dump(mode="json")["group" if name == "grp" else name] for name in names) for row in written]
    assert len(held) == 3
    assert sorted(tuple(each) for each in held.tuples()) == sorted(expected)


def test_a_series_the_table_has_no_rows_of_is_no_rows(tmp_path):
    assert len(_native.layout_rows(str(migrated(tmp_path / "base.db")), 404)) == 0
