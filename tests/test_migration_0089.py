"""The migration 0089 (the rows of the tables of pages and of the layouts of series kept by columns), proved before it is
run on production; the test goes with the commit that runs it there."""
import importlib.util
import sqlite3
from contextlib import closing
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from pydantic import TypeAdapter
from sqlalchemy import create_engine

from core.contracts import CompositionRow, ReleaseTable, RowState, RowTrack, RowVoice, TableRow
from modules.sources.repository import _stored
from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent
TABLE_ROWS, LAYOUT_ROWS = TypeAdapter(list[TableRow]), TypeAdapter(list[CompositionRow])


def loaded(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m0089 = loaded("0089_rows_by_columns")


def run(path: str, *migrations) -> None:
    with create_engine(f"sqlite:///{path}").begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            for each in migrations:
                each()


ROWS = [
    TableRow(key="vk-1", player="vk", player_label="VK", group="Сезон 1", season="1", episode="3", text="Серия 3 «тест»",
             voice=RowVoice(name="Рутина", kind="voice"), heights=[720, 1080], state=RowState.PLAYABLE, reason=None,
             duration_s=1440.5, published="2026-11-01", link="https://cdn.test/a?b=1", video="777", timeline="t1",
             track="sound", language="rus", subtitle_type="", site_fields={"id": 5, "имя": ["x"]}, part="cat"),
    TableRow(key="kd-2", player="kodik", text="", heights=[], state=RowState.ABSENT, reason='{"code":"gone"}',
             track="subtitles", subtitle_type="full"),
]
LAYOUT = [
    CompositionRow(series_id=3, source_id=7, row_key="vk-1", season=1, episode_start=3, episode_end=3, part=2,
                   voice_group="Рутина", voice_label="Рутина", voice_name="Рутина", player="vk", player_label="VK",
                   group="Сезон 1", text="Серия 3", heights=[720, 1080], state=RowState.PLAYABLE, published="2026-11-01",
                   link="https://cdn.test/a", video="777", timeline="t1", language="rus", stale=True),
    CompositionRow(series_id=3, source_id=7, row_key="x", excluded=True, exclusion="no_number", voice_label="Неизвестная "
                   "озвучка 1", voice_unknown=1, player="kodik", state=RowState.ABSENT),
]
SUBTITLES = [CompositionRow(series_id=3, source_id=7, row_key="kd-2", season=1, episode_start=1, episode_end=4,
                            voice_label="Полные", player="kodik", state=RowState.PLAYABLE, track=RowTrack.SUBTITLES,
                            subtitle_type="full")]


def test_the_rows_kept_as_json_are_kept_by_columns_and_read_as_they_were_and_go_back(tmp_path):
    path = migrated(tmp_path / "m.db")
    run(path, m0089.downgrade)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("INSERT INTO release_tables (url, site, names, title, read_at, complete, state, unread, rows) VALUES "
                     "('https://p.test/1', 'p', '[\"Имя\"]', 'T', '2026-11-01T12:00:00+00:00', 1, 'ready', '[]', ?)",
                     (TABLE_ROWS.dump_json(ROWS),))
        conn.execute("INSERT INTO release_tables (url, site, state) VALUES ('https://p.test/2', 'p', 'reading')")
        conn.execute("INSERT INTO composition_state (series_id, rev, rows, subtitles) VALUES (3, 4, ?, ?)",
                     (LAYOUT_ROWS.dump_json(LAYOUT).decode(), LAYOUT_ROWS.dump_json(SUBTITLES)))
        conn.execute("INSERT INTO composition_state (series_id, rev) VALUES (5, 1)")
        conn.commit()
    run(path, m0089.upgrade)
    run(path, m0089.upgrade)  # a field kept by columns already stays as it is
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        assert _stored(conn, "https://p.test/1").table == ReleaseTable(
            url="https://p.test/1", site="p", names=["Имя"], title="T", read_at="2026-11-01T12:00:00+00:00", complete=True,
            rows=ROWS)
        assert conn.execute("SELECT rows FROM release_tables WHERE url = 'https://p.test/2'").fetchone()[0] is None
    from modules.composition.repository import _read
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        stored = _read(conn, 3)
        assert LAYOUT_ROWS.validate_json(stored.rows.json()) == LAYOUT
        assert LAYOUT_ROWS.validate_json(stored.subtitles.json()) == SUBTITLES
        assert len(_read(conn, 5).rows) == 0
    run(path, m0089.downgrade)
    with closing(sqlite3.connect(path)) as conn:
        table, = conn.execute("SELECT rows FROM release_tables WHERE url = 'https://p.test/1'").fetchone()
        rows, subtitles = conn.execute("SELECT rows, subtitles FROM composition_state WHERE series_id = 3").fetchone()
    assert TABLE_ROWS.validate_json(table) == ROWS
    assert (LAYOUT_ROWS.validate_json(rows), LAYOUT_ROWS.validate_json(subtitles)) == (LAYOUT, SUBTITLES)
