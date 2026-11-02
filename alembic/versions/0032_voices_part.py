"""voices: the part of an episode is a part of the key of a voice-over.

A source can give one episode in parts; a reference is one part, so a voice-over is planned, downloaded and aligned for
one part. `voice_dubs.part` is 0 for an episode without parts, so that the key (series, season, episode, part,
voice_key) has no NULL in it. The rows that exist keep every column and get part 0: they were planned for the episode
as a whole.

The table is made again, since SQLite cannot change a unique key in place; the identifiers of the rows are kept.
downgrade keeps one row of each (series, season, episode, voice_key): the row of the episode as a whole, else the first.
What the rows that go own is handed to the cleanup journal in the same migration: their temporary files, the tracks
placed for them, the jobs at the service and the submissions that were not answered.
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None

_COLUMNS = ("id, series_id, season, episode, voice_key, voice_label, chain, attempt, status, temp_path, error, "
            "updated_at, job_id, aligned_path, output_path, outcome, ref_uid, ref_path, move_from, submit_key, "
            "out_dir, admitted, file_id")
_INDEXES = (
    "CREATE INDEX ix_voice_dubs_series ON voice_dubs (series_id)",
    "CREATE INDEX ix_voice_dubs_job ON voice_dubs (job_id)",
    "CREATE INDEX ix_voice_dubs_submit_key ON voice_dubs (submit_key)",
    "CREATE INDEX ix_voice_dubs_file ON voice_dubs (file_id)",
)


def _table(part: bool) -> str:
    key = "series_id, season, episode, part, voice_key" if part else "series_id, season, episode, voice_key"
    return (
        "CREATE TABLE voice_dubs_next ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " series_id INTEGER NOT NULL REFERENCES series (id),"
        " season INTEGER NOT NULL,"
        " episode INTEGER NOT NULL,"
        + (" part INTEGER NOT NULL DEFAULT 0," if part else "")
        + " voice_key TEXT NOT NULL,"
        " voice_label TEXT NOT NULL,"
        " chain TEXT NOT NULL,"
        " attempt INTEGER NOT NULL DEFAULT 0,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " temp_path TEXT,"
        " error TEXT,"
        " updated_at DATETIME NOT NULL,"
        " job_id TEXT,"
        " aligned_path TEXT,"
        " output_path TEXT,"
        " outcome TEXT,"
        " ref_uid TEXT,"
        " ref_path TEXT,"
        " move_from TEXT,"
        " submit_key TEXT,"
        " out_dir TEXT,"
        " admitted INTEGER NOT NULL DEFAULT 1,"
        " file_id INTEGER,"
        f" UNIQUE ({key}))")


def _swap(copy: str) -> None:
    op.execute(copy)
    op.execute("DROP TABLE voice_dubs")
    op.execute("ALTER TABLE voice_dubs_next RENAME TO voice_dubs")
    for statement in _INDEXES:
        op.execute(statement)


def upgrade() -> None:
    op.execute(_table(part=True))
    _swap(f"INSERT INTO voice_dubs_next ({_COLUMNS}) SELECT {_COLUMNS} FROM voice_dubs")


# The row that stays of each key: the row of the episode as a whole (part 0, which every row before this migration is), else
# the first. The rows that go are the ones this does not name; the same text says who stays and who is handed over.
_STAYS = ("SELECT COALESCE(MIN(CASE WHEN part = 0 THEN id END), MIN(id)) FROM voice_dubs "
          "GROUP BY series_id, season, episode, voice_key")
_NOW = "strftime('%Y-%m-%d %H:%M:%f', 'now')"


def downgrade() -> None:
    # What the rows that go own is handed over in the same migration, so that no name is left without a record.
    goes = f"id NOT IN ({_STAYS})"
    for column, kind in (("output_path", "artifact"), ("move_from", "artifact"), ("aligned_path", "path"),
                         ("temp_path", "path")):
        op.execute(
            "INSERT OR IGNORE INTO voice_cleanup (path, kind, error, updated_at) "
            f"SELECT {column}, '{kind}', 'handed over', {_NOW} FROM voice_dubs WHERE {goes} AND {column} IS NOT NULL")
    # A job at the service, whatever its step, and a submission that was sent and not answered, with the folder each wrote in.
    # One that a row that stays still holds is the work of that row too: it is not handed over.
    held = f"SELECT {{column}} FROM voice_dubs WHERE id IN ({_STAYS}) AND {{column}} IS NOT NULL"
    op.execute("INSERT OR IGNORE INTO voice_cleanup (path, kind, error, scope, updated_at) "
               f"SELECT job_id, 'job', 'handed over', out_dir, {_NOW} FROM voice_dubs WHERE {goes} AND job_id IS NOT NULL "
               f"AND job_id NOT IN ({held.format(column='job_id')})")
    op.execute("INSERT OR IGNORE INTO voice_cleanup (path, kind, error, scope, updated_at) "
               f"SELECT submit_key, 'submission', 'handed over', out_dir, {_NOW} FROM voice_dubs "
               f"WHERE {goes} AND status = 'aligning' AND job_id IS NULL AND submit_key IS NOT NULL "
               f"AND submit_key NOT IN ({held.format(column='submit_key')})")
    op.execute(_table(part=False))
    _swap(f"INSERT INTO voice_dubs_next ({_COLUMNS}) SELECT {_COLUMNS} FROM voice_dubs WHERE id IN ({_STAYS})")
