"""Базовая схема: 19 живых таблиц существующей прод-БД (решение Р-6).

DDL снят с фикстуры прод-app.db байт-в-байт: формат данных — внешний
констрейнт (И-4). НЕ включены: 9 мёртвых таблиц (находка №1) и logs
(логирование живёт в файлах). При финальном переключении лишние
таблицы в копии прод-БД не мешают; их чистка — отдельной миграцией
после стабилизации.
"""
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

DDL = [
    'CREATE TABLE series (\n\tid INTEGER NOT NULL, \n\turl TEXT NOT NULL, \n\tname TEXT NOT NULL, \n\tname_en TEXT NOT NULL, \n\tsite TEXT NOT NULL, \n\tsave_path TEXT NOT NULL, \n\tseason TEXT, \n\tquality TEXT, \n\tstate TEXT, \n\tlast_scan_time DATETIME, \n\tauto_scan_enabled BOOLEAN NOT NULL, \n\tquality_override TEXT, \n\tresolution_override TEXT, \n\tsource_type TEXT NOT NULL, \n\tparser_profile_id INTEGER, \n\tignored_seasons TEXT, \n\tvk_search_mode TEXT NOT NULL, \n\tvk_quality_priority TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(parser_profile_id) REFERENCES parser_profiles (id)\n)',
    'CREATE TABLE series_statuses (\n\tseries_id INTEGER NOT NULL, \n\tis_waiting BOOLEAN NOT NULL, \n\tis_scanning BOOLEAN NOT NULL, \n\tis_metadata BOOLEAN NOT NULL, \n\tis_renaming BOOLEAN NOT NULL, \n\tis_checking BOOLEAN NOT NULL, \n\tis_activating BOOLEAN NOT NULL, \n\tis_downloading BOOLEAN NOT NULL, \n\tis_slicing BOOLEAN NOT NULL, \n\tis_ready BOOLEAN NOT NULL, \n\tis_error BOOLEAN NOT NULL, \n\tis_viewing DATETIME, \n\tPRIMARY KEY (series_id), \n\tFOREIGN KEY(series_id) REFERENCES series (id)\n)',
    'CREATE TABLE torrents (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER, \n\ttorrent_id TEXT NOT NULL, \n\tlink TEXT NOT NULL, \n\tdate_time TEXT, \n\tquality TEXT, \n\tepisodes TEXT, \n\tis_active BOOLEAN, \n\tqb_hash TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(series_id) REFERENCES series (id)\n)',
    'CREATE TABLE torrent_files (\n\tid INTEGER NOT NULL, \n\ttorrent_db_id INTEGER NOT NULL, \n\toriginal_path TEXT NOT NULL, \n\trenamed_path TEXT, \n\tstatus TEXT NOT NULL, \n\textracted_metadata TEXT, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(torrent_db_id) REFERENCES torrents (id)\n)',
    'CREATE TABLE media_items (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tunique_id TEXT NOT NULL, \n\tsource_title TEXT, \n\tseason INTEGER, \n\tepisode_start INTEGER NOT NULL, \n\tepisode_end INTEGER, \n\tplan_status TEXT NOT NULL, \n\tstatus TEXT NOT NULL, \n\tis_ignored_by_user BOOLEAN NOT NULL, \n\tsource_url TEXT NOT NULL, \n\tpublication_date DATETIME NOT NULL, \n\tvoiceover_tag TEXT, \n\tfinal_filename TEXT, \n\tchapters TEXT, \n\tchapters_filtered TEXT, \n\tslicing_status TEXT NOT NULL, \n\tis_available BOOLEAN NOT NULL, \n\tresolution INTEGER, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(series_id) REFERENCES series (id), \n\tUNIQUE (unique_id)\n)',
    'CREATE TABLE download_tasks (\n\tid INTEGER NOT NULL, \n\ttask_key TEXT NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tvideo_url TEXT, \n\tsave_path TEXT, \n\tstatus TEXT NOT NULL, \n\terror_message TEXT, \n\tattempts INTEGER, \n\tcreated_at DATETIME, \n\tupdated_at DATETIME, \n\ttask_type TEXT NOT NULL, \n\tprogress INTEGER, \n\tdlspeed INTEGER, \n\teta INTEGER, \n\ttotal_size_mb FLOAT, \n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_download_tasks_task_key ON download_tasks (task_key)',
    'CREATE TABLE slicing_tasks (\n\tid INTEGER NOT NULL, \n\tmedia_item_unique_id TEXT NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tstatus TEXT NOT NULL, \n\tprogress_chapters TEXT, \n\terror_message TEXT, \n\tcreated_at DATETIME, \n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_slicing_tasks_media_item_unique_id ON slicing_tasks (media_item_unique_id)',
    'CREATE TABLE sliced_files (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tsource_media_item_unique_id TEXT NOT NULL, \n\tepisode_number INTEGER NOT NULL, \n\tfile_path TEXT NOT NULL, \n\tstatus TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(series_id) REFERENCES series (id)\n)',
    'CREATE INDEX ix_sliced_files_source_media_item_unique_id ON sliced_files (source_media_item_unique_id)',
    'CREATE TABLE agent_tasks (\n\ttorrent_hash TEXT NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\ttorrent_id TEXT NOT NULL, \n\told_torrent_id TEXT, \n\tstage TEXT NOT NULL, \n\tPRIMARY KEY (torrent_hash), \n\tFOREIGN KEY(series_id) REFERENCES series (id)\n)',
    'CREATE TABLE renaming_tasks (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tmedia_item_unique_id TEXT, \n\told_path TEXT, \n\tnew_path TEXT, \n\tstatus TEXT NOT NULL, \n\tattempts INTEGER, \n\terror_message TEXT, \n\tcreated_at DATETIME, \n\ttask_type TEXT NOT NULL, \n\ttask_data TEXT, \n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_renaming_tasks_series_id ON renaming_tasks (series_id)',
    'CREATE INDEX ix_renaming_tasks_media_item_unique_id ON renaming_tasks (media_item_unique_id)',
    'CREATE TABLE relocation_tasks (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tnew_path TEXT NOT NULL, \n\tstatus TEXT NOT NULL, \n\terror_message TEXT, \n\tcreated_at DATETIME, \n\tPRIMARY KEY (id)\n)',
    'CREATE INDEX ix_relocation_tasks_series_id ON relocation_tasks (series_id)',
    'CREATE TABLE scan_tasks (\n\tid INTEGER NOT NULL, \n\tseries_id INTEGER NOT NULL, \n\tcreated_at DATETIME, \n\tstatus TEXT, \n\ttask_data TEXT, \n\tresults_data TEXT, \n\tPRIMARY KEY (id)\n)',
    'CREATE TABLE parser_profiles (\n\tid INTEGER NOT NULL, \n\tname TEXT NOT NULL, \n\tpreferred_voiceovers TEXT, \n\tPRIMARY KEY (id), \n\tUNIQUE (name)\n)',
    'CREATE TABLE parser_rules (\n\tid INTEGER NOT NULL, \n\tprofile_id INTEGER NOT NULL, \n\tname TEXT NOT NULL, \n\tpriority INTEGER NOT NULL, \n\taction_pattern TEXT, \n\tcontinue_after_match BOOLEAN NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(profile_id) REFERENCES parser_profiles (id)\n)',
    'CREATE TABLE parser_rule_conditions (\n\tid INTEGER NOT NULL, \n\trule_id INTEGER NOT NULL, \n\tcondition_type TEXT NOT NULL, \n\tpattern TEXT NOT NULL, \n\tlogical_operator TEXT NOT NULL, \n\tPRIMARY KEY (id), \n\tFOREIGN KEY(rule_id) REFERENCES parser_rules (id)\n)',
    'CREATE TABLE auth (\n\tauth_type TEXT NOT NULL, \n\tusername TEXT, \n\tpassword TEXT, \n\turl TEXT, \n\tPRIMARY KEY (auth_type)\n)',
    'CREATE TABLE settings (\n\t"key" TEXT NOT NULL, \n\tvalue TEXT, \n\tPRIMARY KEY ("key")\n)',
    'CREATE TABLE trackers (\n\tid INTEGER NOT NULL, \n\tcanonical_name TEXT NOT NULL, \n\tdisplay_name TEXT NOT NULL, \n\tmirrors TEXT, \n\tparser_class TEXT NOT NULL, \n\tauth_type TEXT NOT NULL, \n\tui_features TEXT, \n\tPRIMARY KEY (id), \n\tUNIQUE (canonical_name)\n)',
    'CREATE TABLE series_tmdb_mappings (\n\tseries_id INTEGER NOT NULL, \n\ttmdb_id INTEGER NOT NULL, \n\ttmdb_season_number INTEGER NOT NULL, \n\ttotal_episodes INTEGER, \n\tlast_updated DATETIME, \n\tposter_path TEXT, \n\tseries_name TEXT, \n\tPRIMARY KEY (series_id), \n\tFOREIGN KEY(series_id) REFERENCES series (id)\n)',
]


def upgrade() -> None:
    for stmt in DDL:
        op.execute(stmt)


def downgrade() -> None:
    raise RuntimeError("откат базовой схемы не предусмотрен")
