"""catalog: sources of a series.

Every series, torrent ones included, gets exactly one row of series_sources that takes over url, source_type,
parser_profile_id and vk_search_mode. What web_video kept in its address moves into fields of the source:
?vibix=N -> vibix_season, ?off=a,b -> players_off; the dunhuatv choice (player, voice, kind) is dropped, because
every player and voice is read now. vk_quality_priority is dropped with the feature. The columns leave `series`
by rebuilding it (SQLite cannot drop columns that foreign keys point at), as in 0011.

downgrade takes the fields back from the source with the lowest position; other sources, the dunhuatv choice
and vk_quality_priority cannot come back.
"""
import json
from urllib.parse import parse_qs, urlsplit, urlunsplit

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

# Player kinds a choice in an address could name; the order is the one the old address was written in.
_PLAYERS = ("vk", "allvideo", "vibix", "kodik", "cvh")

# Columns that stay in `series`, in table order.
_KEPT = ("id, name, name_en, site, save_path, season, quality, state, last_scan_time, auto_scan_enabled, "
         "quality_override, resolution_override, ignored_seasons, ignored_episodes")

_SOURCES = """
CREATE TABLE series_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    series_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    url TEXT NOT NULL,
    source_type TEXT NOT NULL,
    parser_profile_id INTEGER,
    vk_search_mode TEXT NOT NULL DEFAULT 'search',
    vibix_season INTEGER,
    players_off TEXT NOT NULL DEFAULT '[]',
    UNIQUE (series_id, url),
    FOREIGN KEY(series_id) REFERENCES series (id),
    FOREIGN KEY(parser_profile_id) REFERENCES parser_profiles (id)
)
"""

_SOURCES_URL_INDEX = "CREATE INDEX ix_series_sources_url ON series_sources (url)"

_SERIES_WITHOUT_SOURCE = """
CREATE TABLE series_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL,
    site TEXT NOT NULL,
    save_path TEXT NOT NULL,
    season TEXT,
    quality TEXT,
    state TEXT,
    last_scan_time DATETIME,
    auto_scan_enabled BOOLEAN NOT NULL,
    quality_override TEXT,
    resolution_override TEXT,
    ignored_seasons TEXT,
    ignored_episodes TEXT
)
"""

_SERIES_WITH_SOURCE = """
CREATE TABLE series_new (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT NOT NULL,
    site TEXT NOT NULL,
    save_path TEXT NOT NULL,
    season TEXT,
    quality TEXT,
    state TEXT,
    last_scan_time DATETIME,
    auto_scan_enabled BOOLEAN NOT NULL,
    quality_override TEXT,
    resolution_override TEXT,
    source_type TEXT NOT NULL,
    parser_profile_id INTEGER,
    ignored_seasons TEXT,
    vk_search_mode TEXT NOT NULL,
    vk_quality_priority TEXT,
    ignored_episodes TEXT,
    FOREIGN KEY(parser_profile_id) REFERENCES parser_profiles (id)
)
"""


def _split_address(url: str) -> tuple[str, int | None, list[str]]:
    """The page alone, the Vibix season and the disabled players that a web_video address carried."""
    parts = urlsplit(url)
    if not parts.query:
        return url, None, []
    query = parse_qs(parts.query)
    season = (query.get("vibix") or [""])[0]
    off = {name for value in query.get("off", []) for name in value.split(",")}
    chosen = int(season) if season.isascii() and season.isdigit() and int(season) > 0 else None
    return urlunsplit(parts._replace(query="")), chosen, [name for name in _PLAYERS if name in off]


def _address_of(page: str, vibix_season: int | None, players_off: list[str]) -> str:
    query = []
    if vibix_season is not None:
        query.append(f"vibix={vibix_season}")
    if players_off:
        query.append("off=" + ",".join(name for name in _PLAYERS if name in players_off))
    return f"{page}{'&' if '?' in page else '?'}{'&'.join(query)}" if query else page


def _sequence(bind) -> int | None:
    """The highest id `series` ever had: AUTOINCREMENT keeps it so that numbers are not reused."""
    row = bind.execute(sa.text("SELECT seq FROM sqlite_sequence WHERE name='series'")).first()
    return row[0] if row else None


def _restore_sequence(bind, seq: int | None) -> None:
    """A rebuilt table counts from its highest surviving id; the old counter is put back."""
    if seq is None:
        return
    updated = bind.execute(sa.text("UPDATE sqlite_sequence SET seq=MAX(seq, :seq) WHERE name='series'"),
                           {"seq": seq}).rowcount
    if not updated:
        bind.execute(sa.text("INSERT INTO sqlite_sequence (name, seq) VALUES ('series', :seq)"), {"seq": seq})


def _swap_series(ddl: str, insert_sql: str) -> None:
    """Replace `series` with a table of the given shape inside the caller's transaction.

    legacy_alter_table keeps the foreign keys of the child tables pointing at the name `series`.
    """
    op.execute(ddl)
    op.execute(insert_sql)
    op.execute("DROP TABLE series")
    op.execute("ALTER TABLE series_new RENAME TO series")


def _assert_foreign_keys(bind, table: str) -> None:
    broken = bind.execute(sa.text(f"PRAGMA foreign_key_check({table})")).all()
    if broken:
        raise RuntimeError(f"{table}: {len(broken)} rows break a foreign key")


def upgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("PRAGMA legacy_alter_table=ON")
        op.execute("BEGIN")
        seq = _sequence(bind)
        profiles = {row[0] for row in bind.execute(sa.text("SELECT id FROM parser_profiles"))}
        series = bind.execute(sa.text(
            "SELECT id, url, source_type, parser_profile_id, vk_search_mode FROM series ORDER BY id")).all()
        op.execute(_SOURCES)
        op.execute(_SOURCES_URL_INDEX)
        for series_id, url, source_type, profile_id, search_mode in series:
            vibix_season, players_off = None, []
            if source_type == "web_video":
                url, vibix_season, players_off = _split_address(url)
            bind.execute(sa.text(
                "INSERT INTO series_sources (series_id, position, url, source_type, parser_profile_id, "
                "vk_search_mode, vibix_season, players_off) "
                "VALUES (:series_id, 0, :url, :source_type, :profile_id, :search_mode, :vibix_season, :players_off)"),
                {"series_id": series_id, "url": url, "source_type": source_type,
                 # A profile that no longer exists is no profile; the foreign key would refuse it.
                 "profile_id": profile_id if profile_id in profiles else None,
                 "search_mode": search_mode, "vibix_season": vibix_season,
                 "players_off": json.dumps(players_off)})
        _swap_series(_SERIES_WITHOUT_SOURCE,
                     f"INSERT INTO series_new ({_KEPT}) SELECT {_KEPT} FROM series")
        _restore_sequence(bind, seq)
        _assert_foreign_keys(bind, "series_sources")
        op.execute("COMMIT")
        op.execute("PRAGMA legacy_alter_table=OFF")


def downgrade() -> None:
    with op.get_context().autocommit_block():
        bind = op.get_bind()
        op.execute("PRAGMA foreign_keys=OFF")
        op.execute("PRAGMA legacy_alter_table=ON")
        op.execute("BEGIN")
        seq = _sequence(bind)
        firsts = bind.execute(sa.text(
            "SELECT series_id, url, source_type, parser_profile_id, vk_search_mode, vibix_season, players_off "
            "FROM series_sources s WHERE id = (SELECT id FROM series_sources WHERE series_id = s.series_id "
            "ORDER BY position, id LIMIT 1)")).all()
        # NOT NULL columns of a series that lost every source are filled with the values of a plain torrent.
        _swap_series(_SERIES_WITH_SOURCE,
                     f"INSERT INTO series_new ({_KEPT}, url, source_type, vk_search_mode) "
                     f"SELECT {_KEPT}, '', 'torrent', 'search' FROM series")
        for series_id, url, source_type, profile_id, search_mode, vibix_season, players_off in firsts:
            if source_type == "web_video":
                url = _address_of(url, vibix_season, json.loads(players_off))
            bind.execute(sa.text(
                "UPDATE series SET url=:url, source_type=:source_type, parser_profile_id=:profile_id, "
                "vk_search_mode=:search_mode WHERE id=:series_id"),
                {"series_id": series_id, "url": url, "source_type": source_type,
                 "profile_id": profile_id, "search_mode": search_mode})
        _restore_sequence(bind, seq)
        op.execute("DROP TABLE series_sources")
        _assert_foreign_keys(bind, "series")
        op.execute("COMMIT")
        op.execute("PRAGMA legacy_alter_table=OFF")
