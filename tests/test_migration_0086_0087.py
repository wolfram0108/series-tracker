"""The migrations 0086 and 0087 (the rows of a table of a page and of a layout of a series kept as fields of their records),
proved before they are run on production; the test goes with the commit that runs them there."""
import importlib.util
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine

from core.contracts import CompositionRow, ReleaseTable, RowState, RowVoice, TableRow
from modules.composition import repository as composition_repository
from modules.sources.repository import _stored
from schema_db import migrated

ROOT = Path(__file__).resolve().parent.parent


def loaded(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "alembic" / "versions" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m0086, m0087 = loaded("0086_table_rows_one_field"), loaded("0087_layout_rows_two_fields")


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


def test_the_rows_of_a_table_are_moved_into_the_field_of_its_page_and_read_as_they_were(tmp_path):
    path = migrated(tmp_path / "m.db")
    with closing(sqlite3.connect(path)) as conn:
        run(path, m0087.downgrade, m0086.downgrade)
        conn.execute("INSERT INTO release_tables (url, site, names, title, read_at, complete, state, unread) VALUES "
                     "('https://p.test/1', 'p', '[\"Имя\"]', 'T', '2026-11-01T12:00:00+00:00', 1, 'ready', '[]')")
        conn.execute("INSERT INTO release_tables (url, site, state) VALUES ('https://p.test/2', 'p', 'reading')")
        for seq, row in enumerate(ROWS):
            conn.execute(f"INSERT INTO release_rows (url, seq, {m0086._COLUMNS}) VALUES ('https://p.test/1', ?, "
                         f"{', '.join('?' * 22)})", (seq, row.key, row.player, row.player_label, row.group, row.season,
                                                     row.episode, row.text, row.voice.name, row.voice.kind,
                                                     json.dumps(row.heights), row.state.value, row.reason, row.duration_s,
                                                     row.published, row.link, row.video, row.timeline, row.track.value,
                                                     row.language, row.subtitle_type,
                                                     json.dumps(row.site_fields, ensure_ascii=False), row.part))
        conn.commit()
    run(path, m0086.upgrade)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'release_rows'").fetchone() is None
        table = _stored(conn, "https://p.test/1").table
        assert table == ReleaseTable(url="https://p.test/1", site="p", names=["Имя"], title="T",
                                     read_at="2026-11-01T12:00:00+00:00", complete=True, rows=ROWS)
        assert _stored(conn, "https://p.test/2").table is None  # a page never read completely has no rows


def test_the_rows_of_a_layout_are_moved_into_the_fields_of_its_series_and_read_as_they_were(tmp_path):
    rows = [CompositionRow(series_id=7, source_id=3, row_key="vk-1", season=1, episode_start=2, episode_end=2,
                           voice_group="Рутина", voice_label="Рутина", voice_name="Рутина", player="vk", player_label="VK",
                           group="Сезон 1", text="Серия 2", heights=[1080], state="playable", published="2026-11-01",
                           link="l", video="v", timeline="t", stale=True),
            CompositionRow(series_id=7, source_id=3, row_key="vk-x", excluded=True, exclusion="rule", player="vk",
                           state="absent", voice_unknown=4),
            CompositionRow(series_id=7, source_id=4, row_key="sub-1", season=1, episode_start=2, player="torrent",
                           state="playable", track="subtitles", language="rus", subtitle_type="signs")]
    path = migrated(tmp_path / "m.db")
    with closing(sqlite3.connect(path)) as conn:
        run(path, m0087.downgrade)
        conn.execute("INSERT INTO composition_state (series_id, rev, writes, inputs) VALUES (7, 5, 9, 'print')")
        names = [each.strip() for each in m0087._COLUMNS.split(",")]
        for seq, row in enumerate(rows):
            values = {**row.model_dump(mode="json"), "grp": row.group, "excluded": int(row.excluded),
                      "stale": int(row.stale), "heights": json.dumps(row.heights)}
            conn.execute(f"INSERT INTO composition_rows (seq, {m0087._COLUMNS}) VALUES ({', '.join('?' * 28)})",
                         (seq, *[values[name] for name in names]))
        # A series with rows and no record of its state yet.
        values = {**rows[0].model_dump(mode="json"), "series_id": 8, "grp": "", "excluded": 0, "stale": 0, "heights": "[]"}
        conn.execute(f"INSERT INTO composition_rows (seq, {m0087._COLUMNS}) VALUES (0, {', '.join('?' * 27)})",
                     [values[name] for name in names])
        conn.commit()
    run(path, m0087.upgrade)
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'composition_rows'").fetchone() is None
        stored = composition_repository._read(conn, 7)
        assert (stored.rev, stored.writes, stored.rows, stored.subtitles) == (5, 9, rows[:2], rows[2:])
        # What a window is given as it is: the JSON of the models, `in_output` with it.
        assert json.loads(stored.rows_json) == [each.model_dump(mode="json") for each in rows[:2]]
        assert [row.row_key for row in composition_repository._read(conn, 8).rows] == ["vk-1"]
