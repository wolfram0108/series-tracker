"""The schedule by the time of day (settings «Расписание», core/schedule.py): the syncs of all series begin at the times of
the day the owner puts on one line of 24 hours, the watched torrent releases are checked at times of their own or between
the syncs, and every site the sources read has its own term (modules/sources/term.py).

The interval of the syncs (`scan_interval_minutes`) becomes the times of day it gave: from the next sync it had set, every
interval round the day (local time of the server); the checks of the releases stand one between two syncs. The one term
of the sources (`sources_term_hours`) becomes the term of every site some source reads, when it is not the default of a
site. A release keeps when its page was last checked (`checked_at`: none yet)."""
import json
from datetime import datetime, timezone

from alembic import op
from sqlalchemy import text

revision = "0082"
down_revision = "0081"
branch_labels = None
depends_on = None

DAY = 24 * 60
DEFAULT_TERM_MINUTES = 60  # modules/sources/term.py: DEFAULT_MINUTES
MOST_POINTS = 48


def sync_times(interval: int, next_scan: str | None, now: datetime) -> list[str]:
    """The times of day an interval of syncs made, from the next sync it had set (now, when it had none)."""
    try:
        base = datetime.fromisoformat(next_scan) if next_scan else now
    except ValueError:
        base = now
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    local = base.astimezone()
    start = local.hour * 60 + local.minute
    step = max(interval, DAY // MOST_POINTS)
    minutes = sorted({(start + step * k) % DAY for k in range(-(-DAY // step))})
    return [f"{each // 60:02d}:{each % 60:02d}" for each in minutes]


def site_of(url: str) -> str:
    from urllib.parse import urlparse
    return (urlparse(url.split("|")[0].strip()).hostname or "").lower()


def upgrade() -> None:
    op.execute("ALTER TABLE torrent_releases ADD COLUMN checked_at TEXT")
    conn = op.get_bind()
    values = dict(conn.execute(text("SELECT key, value FROM settings")).fetchall())
    if "schedule" not in values and "scan_interval_minutes" in values:
        try:
            interval = int(values.get("scan_interval_minutes") or 60)
        except ValueError:
            interval = 60
        schedule = {"syncs": sync_times(max(interval, 1), values.get("next_scan_timestamp"), datetime.now(timezone.utc)),
                    "torrent_mode": "between", "torrent_free": [], "torrent_between": 1}
        conn.execute(text("INSERT INTO settings (key, value) VALUES ('schedule', :v)"), {"v": json.dumps(schedule)})
    try:
        hours = int(values.get("sources_term_hours") or 0)
    except ValueError:
        hours = 0
    if hours >= 1 and hours * 60 != DEFAULT_TERM_MINUTES and "sources.terms" not in values:
        sites = {site_of(url) for (url,) in conn.execute(
            text("SELECT url FROM series_sources WHERE source_type <> 'torrent'")).fetchall()} - {""}
        conn.execute(text("INSERT INTO settings (key, value) VALUES ('sources.terms', :v)"),
                     {"v": json.dumps({site: hours * 60 for site in sorted(sites)}, sort_keys=True)})
    conn.execute(text("DELETE FROM settings WHERE key IN ('scan_interval_minutes', 'sources_term_hours')"))


def downgrade() -> None:
    raise NotImplementedError("the times of day are not made into an interval again")
