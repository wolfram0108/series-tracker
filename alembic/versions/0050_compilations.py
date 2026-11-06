"""Stage 12: a compilation as the reference of the episodes it holds.

- `output_settings.compilation`: the key of the compilation a record is set on (the level between a season and an
  episode); the slot of a record takes it in.
- `scan_references.compilation`: the compilation the episode stands in, whole (JSON of `CompilationRef`), or NULL.
- `media_items.want_cut` (scan): the key of the compilation whose video the plan wants in the slot of the row;
  `media_items.pending_video`, `pending_cut` and `cut` (downloads): the video the next build of the container puts in the
  slot (a file of the series held by the row), what it is cut of (JSON, NULL for the single video itself), and what the
  video of the container in place is cut of.
- `voice_files.quality`: `dub` (the video of a voice-over, capped) or `best` (a compilation to cut episodes of, uncapped);
  a file is one row per (series, source, row key, address, quality).
- `voice_cuts` (voices): the cut of one slot out of its compilation by the alignment service.

downgrade drops all of it; the records set on a compilation and the cuts go with it.
"""
from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE output_settings ADD COLUMN compilation TEXT")
    op.execute("DROP INDEX output_settings_slot")
    op.execute("CREATE UNIQUE INDEX output_settings_slot ON output_settings"
               " (series_id, IFNULL(season, -1), IFNULL(episode, -1), IFNULL(compilation, ''), lever, IFNULL(target, ''))")
    op.execute("ALTER TABLE scan_references ADD COLUMN compilation TEXT")
    for column in ("want_cut TEXT", "pending_video TEXT", "pending_cut TEXT", "cut TEXT"):
        op.execute(f"ALTER TABLE media_items ADD COLUMN {column}")
    op.execute(
        "CREATE TABLE voice_files_new ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " source_id INTEGER NOT NULL,"
        " row_key TEXT NOT NULL,"
        " url TEXT NOT NULL,"
        " quality TEXT NOT NULL DEFAULT 'dub' CHECK (quality IN ('dub', 'best')),"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " attempt INTEGER NOT NULL DEFAULT 0,"
        " temp_path TEXT,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " UNIQUE (series_id, source_id, row_key, url, quality))")
    op.execute("INSERT INTO voice_files_new (id, series_id, source_id, row_key, url, status, attempt, temp_path, error,"
               " updated_at) SELECT id, series_id, source_id, row_key, url, status, attempt, temp_path, error, updated_at"
               " FROM voice_files")
    op.execute("DROP TABLE voice_files")
    op.execute("ALTER TABLE voice_files_new RENAME TO voice_files")
    op.execute("CREATE INDEX ix_voice_files_series ON voice_files (series_id)")
    op.execute(
        "CREATE TABLE voice_cuts ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " season INTEGER NOT NULL,"
        " episode INTEGER NOT NULL,"
        " part INTEGER NOT NULL DEFAULT 0,"
        " cut_key TEXT NOT NULL,"
        " cut TEXT NOT NULL,"
        " file_id INTEGER,"
        " ref_uid TEXT,"
        " ref_rev INTEGER,"
        " status TEXT NOT NULL DEFAULT 'planned',"
        " admitted INTEGER NOT NULL DEFAULT 0,"
        " submit_key TEXT,"
        " job_id TEXT,"
        " work_dir TEXT,"
        " out_path TEXT,"
        " from_ref_s REAL,"
        " from_dub_s REAL,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " UNIQUE (series_id, season, episode, part))")
    op.execute("CREATE INDEX ix_voice_cuts_series ON voice_cuts (series_id)")
    op.execute("CREATE INDEX ix_voice_cuts_job ON voice_cuts (job_id)")
    op.execute("CREATE INDEX ix_voice_cuts_submit_key ON voice_cuts (submit_key)")


def downgrade() -> None:
    op.execute("DROP TABLE voice_cuts")
    op.execute(
        "CREATE TABLE voice_files_old ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " source_id INTEGER NOT NULL,"
        " row_key TEXT NOT NULL,"
        " url TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " attempt INTEGER NOT NULL DEFAULT 0,"
        " temp_path TEXT,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " UNIQUE (series_id, source_id, row_key, url))")
    op.execute("INSERT INTO voice_files_old (id, series_id, source_id, row_key, url, status, attempt, temp_path, error,"
               " updated_at) SELECT id, series_id, source_id, row_key, url, status, attempt, temp_path, error, updated_at"
               " FROM voice_files WHERE quality = 'dub'")
    op.execute("DROP TABLE voice_files")
    op.execute("ALTER TABLE voice_files_old RENAME TO voice_files")
    op.execute("CREATE INDEX ix_voice_files_series ON voice_files (series_id)")
    for column in ("cut", "pending_cut", "pending_video", "want_cut"):
        op.execute(f"ALTER TABLE media_items DROP COLUMN {column}")
    op.execute("ALTER TABLE scan_references DROP COLUMN compilation")
    op.execute("DROP INDEX output_settings_slot")
    op.execute("DELETE FROM output_settings WHERE compilation IS NOT NULL")
    op.execute("ALTER TABLE output_settings DROP COLUMN compilation")
    op.execute("CREATE UNIQUE INDEX output_settings_slot ON output_settings"
               " (series_id, IFNULL(season, -1), IFNULL(episode, -1), lever, IFNULL(target, ''))")
