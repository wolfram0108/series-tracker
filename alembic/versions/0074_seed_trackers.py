"""The trackers the service reads are in every database (stage 15): the table `trackers` came filled from the database of the
first version only, so a database made by the migrations knew no tracker and every page of one was `tracker_unknown_url`.
A tracker already there keeps what it holds (its mirrors are the user's)."""
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None

TRACKERS = [
    (1, "anilibria", "Anilibria", '["anilibria.top", "aniliberty.top"]', "AnilibriaParser", "none",
     '{"quality_selector": "anilibria"}'),
    (2, "anilibria_tv", "Anilibria.TV", '["anilibria.tv"]', "AnilibriaTvParser", "none", "{}"),
    (3, "kinozal", "Kinozal.me", '["kinozal.me", "kinozal.guru", "kinozal.tv"]', "KinozalParser", "kinozal", "{}"),
    (4, "astar", "Astar.bz", '["v13.astar.bz"]', "AstarParser", "astar", '{"quality_selector": "astar"}'),
    (5, "rutracker", "RuTracker.org", '["rutracker.org"]', "RuTrackerParser", "rutracker", "{}"),
]


def upgrade() -> None:
    conn = op.get_bind()
    for row in TRACKERS:
        # By the name: an id taken by another row (none such on the server) gives the new row its own.
        if conn.exec_driver_sql("SELECT 1 FROM trackers WHERE canonical_name=?", (row[1],)).fetchone():
            continue
        free = conn.exec_driver_sql("SELECT 1 FROM trackers WHERE id=?", (row[0],)).fetchone() is None
        conn.exec_driver_sql(
            "INSERT INTO trackers (id, canonical_name, display_name, mirrors, parser_class, auth_type, ui_features) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (row[0] if free else None, *row[1:]))


def downgrade() -> None:
    pass  # the trackers stay: the sources of the series name them
