# Реестр эндпоинтов прода (этап 0, снят 2026-06-11)

Всего: 73 маршрутов, 78 метод-эндпоинтов.
Источник истины — код `routes/` на коммите main. Колонка «Ревизия» заполняется на этапе 5.

| Методы | Путь | Обработчик | Где | Ревизия |
|---|---|---|---|---|
| GET | `/` | index | routes/__init__.py:22 | подтверждена (gateway: index.html + /static) |
| GET | `/api/agent/queue` | get_agent_queue | routes/system.py:128 | подтверждена (Р-20: torrents.queue.get, форма с hash — находка 39) |
| POST | `/api/agent/reset` | reset_agent | routes/system.py:133 | удалена (находка 23: была сломана и мертва; зависших статусов в новой системе нет по построению — Р-11) |
| GET | `/api/auth` | get_all_auth | routes/settings.py:58 | подтверждена (Р-22: trackerauth.credentials.get ×4 + settings tmdb_token) |
| POST | `/api/auth` | save_all_auth | routes/settings.py:68 | подтверждена (Р-22: credentials.set + событие changed → torrents перелогинивается) |
| POST | `/api/database/clear` | clear_database | routes/system.py:32 | удалена (Р-22: мёртвая полная очистка БД) |
| POST | `/api/database/clear_table` | clear_table | routes/system.py:53 | подтверждена (Р-22: админ-вкладка БД, прямой доступ gateway; исключены auth и tracker_sessions) |
| GET | `/api/database/table/<string:table_name>` | get_table_content | routes/system.py:200 | подтверждена (Р-22: админ-вкладка БД) |
| GET | `/api/database/tables` | get_db_tables | routes/system.py:42 | подтверждена (Р-22: админ-вкладка БД) |
| GET | `/api/directories` | get_directories | routes/filebrowser.py:11 | подтверждена (Р-22: library.directories.list) |
| GET | `/api/downloads/queue` | get_download_queue | routes/system.py:182 | подтверждена (Р-20: downloads.queue.get) |
| POST | `/api/downloads/queue/clear` | clear_download_queue | routes/system.py:190 | подтверждена (Р-20: downloads.queue.clear; во фронте не вызывается — админ-инструмент) |
| GET | `/api/hello-info` | hello_info | routes/__init__.py:34 | удалена (Р-22: тестовая страница разработки) |
| GET | `/api/logs` | get_logs | routes/system.py:71 | подтверждена (Р-22: чтение logs/*.log gateway, фильтры group/level/limit) |
| PUT | `/api/media-items/<int:item_id>/ignore` | set_item_ignored_status | routes/media.py:56 | удалена (Р-21: мёртвый дубль uid-точки по числовому id) |
| POST | `/api/media-items/<string:unique_id>/chapters` | fetch_and_save_chapters | routes/media.py:26 | подтверждена (Р-21: slicing.chapters.get — контракт Р-16) |
| POST | `/api/media-items/<string:unique_id>/chapters/filtered` | get_filtered_chapters | routes/media.py:284 | подтверждена (Р-21: slicing.chapters.filtered) |
| POST | `/api/media-items/<string:unique_id>/chapters/mark-garbage` | mark_garbage_chapters | routes/media.py:335 | подтверждена (Р-21: slicing.chapters.mark) |
| PUT | `/api/media-items/<string:unique_id>/ignore` | set_item_ignored_status_by_uid | routes/media.py:85 | подтверждена (Р-21: scan.item.set_ignored + scan.plan.updated вместо sync_vk_statuses) |
| POST | `/api/media-items/<string:unique_id>/slice` | create_slice_task | routes/media.py:112 | подтверждена (Р-21: slicing.task.create) |
| POST | `/api/media-items/<string:unique_id>/slice-with-filter` | create_slice_task_with_filter | routes/media.py:388 | подтверждена (Р-21: slicing.task.create {garbage_indices}) |
| POST | `/api/media-items/<string:unique_id>/verify-sliced-files` | verify_sliced_files | routes/media.py:144 | подтверждена (Р-21: slicing.verify) |
| POST | `/api/parse_url` | parse_url | routes/settings.py:87 | подтверждена (Р-22: sources.parse; torrent_id считает sources по core/ids) |
| GET | `/api/parser-profiles` | get_parser_profiles | routes/parser.py:10 | подтверждена (Р-22: rules.profiles.list) |
| POST | `/api/parser-profiles` | create_parser_profile | routes/parser.py:15 | подтверждена (Р-22: rules.profiles.create, 409 на дубль) |
| PUT | `/api/parser-profiles/<int:profile_id>` | update_parser_profile | routes/parser.py:34 | подтверждена (Р-22: rules.profiles.update) |
| DELETE | `/api/parser-profiles/<int:profile_id>` | delete_parser_profile | routes/parser.py:49 | подтверждена (Р-22: rules.profiles.delete, 400 если используется) |
| GET | `/api/parser-profiles/<int:profile_id>/rules` | get_rules_for_profile | routes/parser.py:59 | подтверждена (Р-22: rules.rules.list) |
| POST | `/api/parser-profiles/<int:profile_id>/rules` | add_rule_to_profile | routes/parser.py:64 | подтверждена (Р-22: rules.rules.add) |
| POST | `/api/parser-profiles/scrape-titles` | scrape_vk_titles | routes/parser.py:74 | подтверждена (Р-22: sources.vk.scan) |
| POST | `/api/parser-profiles/test` | test_parser_rules | routes/parser.py:123 | подтверждена (Р-22: rules.test, форма конструктора) |
| PUT | `/api/parser-rules/<int:rule_id>` | update_rule | routes/parser.py:95 | подтверждена (Р-22: rules.rules.update) |
| DELETE | `/api/parser-rules/<int:rule_id>` | delete_rule | routes/parser.py:105 | подтверждена (Р-22: rules.rules.delete) |
| POST | `/api/parser-rules/reorder` | reorder_rules | routes/parser.py:114 | подтверждена (Р-22: rules.rules.reorder) |
| POST | `/api/scanner/scan_all` | scan_all_now | routes/system.py:170 | подтверждена (Р-20: scan.all.start {force_replace}; 409 при идущем) |
| POST | `/api/scanner/settings` | update_scanner_settings | routes/system.py:157 | перепроектирована (Р-20: запись settings; вместо немедленного скана — пересчёт расписания по settings.changed) |
| GET | `/api/scanner/status` | get_scanner_status | routes/system.py:153 | подтверждена (Р-20: scan.status.get; + публикация статуса при подключении SSE-клиента) |
| GET | `/api/series` | get_series | routes/series.py:21 | подтверждена (Р-19: композиция gateway — catalog+metadata+счётчики батчем, N+1 устранён) |
| POST | `/api/series` | add_series | routes/series.py:51 | подтверждена (Р-19: catalog.series.create + metadata.map.set + torrents.db.add) |
| GET | `/api/series/<int:series_id>` | get_series_details | routes/series.py:75 | подтверждена (Р-19: catalog + sources.tracker.resolve + metadata.map.get) |
| POST | `/api/series/<int:series_id>` | update_series | routes/series.py:427 | подтверждена (Р-19: catalog.series.update → library.relocate | renaming.reprocess) |
| DELETE | `/api/series/<int:series_id>` | delete_series | routes/series.py:507 | подтверждена (Р-19: catalog.series.delete, событийный каскад владельцев) |
| GET | `/api/series/<int:series_id>/composition` | get_series_composition | routes/series.py:210 | подтверждена (Р-21: scan.composition / torrents.composition по source_type) |
| POST | `/api/series/<int:series_id>/deep-adoption` | deep_adoption | routes/media.py:270 | подтверждена (Р-21: команда slicing.deep_adoption, фон как в оригинале) |
| POST | `/api/series/<int:series_id>/ignored-seasons` | update_ignored_seasons | routes/series.py:560 | подтверждена (Р-19: catalog.series.update, JSON-формат колонки сохранён) |
| GET | `/api/series/<int:series_id>/media-items` | get_media_items_for_series | routes/media.py:12 | подтверждена (Р-21: scan.media.list) |
| POST | `/api/series/<int:series_id>/relocate` | relocate_series | routes/series.py:671 | удалена (Р-21: дубль сценария сохранения свойств — Р-19/Р-17) |
| GET | `/api/series/<int:series_id>/rename_preview` | get_rename_preview | routes/series.py:105 | подтверждена (Р-21: renaming.preview — dry-run переобработки) |
| POST | `/api/series/<int:series_id>/reprocess` | reprocess_series_torrents_route | routes/series.py:403 | подтверждена (Р-21: 409 по renaming.tasks.active, иначе команда renaming.reprocess) |
| POST | `/api/series/<int:series_id>/reprocess_vk_files` | reprocess_vk_files_route | routes/series.py:189 | подтверждена (Р-21: то же — renaming.reprocess сам определяет тип) |
| POST | `/api/series/<int:series_id>/reset_torrents` | reset_torrents | routes/series.py:620 | удалена (Р-21: мертва на фронте, разрушительна; согласовано) |
| POST | `/api/series/<int:series_id>/scan` | scan_series_route | routes/series.py:545 | подтверждена (Р-20: query scan.series.run, синхронный ответ; force из debug_force_replace) |
| GET | `/api/series/<int:series_id>/sliced-files` | get_sliced_files_for_series | routes/series.py:574 | подтверждена (Р-21: slicing.files.list + обогащение из scan.media.list) |
| GET | `/api/series/<int:series_id>/source-filenames` | get_series_source_filenames | routes/series.py:662 | подтверждена (Р-21: сборка gateway из torrents.db.files.for_series / scan.media.list) |
| POST | `/api/series/<int:series_id>/state` | set_series_state_route | routes/series.py:531 | перепроектирована (Р-11/Р-19: транспорт catalog.viewing.start/stop, БД не пишется) |
| POST | `/api/series/<int:series_id>/toggle_auto_scan` | toggle_auto_scan | routes/series.py:496 | подтверждена (Р-19: catalog.series.update; SSE — дельта series.updated) |
| GET | `/api/series/<int:series_id>/torrents/history` | get_series_torrents_history | routes/series.py:555 | подтверждена (Р-19: torrents.db.history) |
| POST | `/api/series/<int:series_id>/viewing_heartbeat` | viewing_heartbeat | routes/series.py:540 | удалена (Р-11: эфемерный viewing со страховкой gateway.sse.clients; setInterval уходит в блоке 6) |
| PUT | `/api/series/<int:series_id>/vk-quality-priority` | set_vk_quality_priority | routes/series.py:596 | подтверждена (Р-19: catalog.series.update) |
| GET | `/api/series/active_torrents` | get_active_torrents_monitoring | routes/series.py:611 | подтверждена (Р-19: torrents.queue.get; формы HTTP и SSE выровнены — находка 39) |
| GET/POST | `/api/settings/debug_flags` | handle_debug_flags | routes/settings.py:139 | перепроектирована (Р-22: группы = модули новой системы; DEBUG-фильтр в core/logging) |
| GET/POST | `/api/settings/force_replace` | handle_force_replace_setting | routes/settings.py:171 | подтверждена (Р-22: settings) |
| GET/POST | `/api/settings/less_strict_scan` | handle_less_strict_scan_setting | routes/settings.py:193 | подтверждена (Р-22: settings) |
| GET/POST | `/api/settings/parallel_downloads` | handle_parallel_downloads | routes/settings.py:182 | подтверждена (Р-22: settings; downloads подписан на изменение) |
| GET/POST | `/api/settings/slicing_delete_source` | handle_slicing_delete_source | routes/settings.py:205 | подтверждена (Р-22: settings) |
| GET | `/api/stream` | stream | routes/system.py:7 | подтверждена (Р-18: SSE_MAP, формы payload — sse_contract.md) |
| GET | `/api/tmdb/details/<int:tmdb_id>` | details | routes/tmdb.py:36 | подтверждена (Р-22: metadata.details + success-обёртка) |
| POST | `/api/tmdb/search` | search | routes/tmdb.py:9 | подтверждена (Р-22: metadata.search + success-обёртка) |
| GET | `/api/trackers` | get_trackers | routes/trackers.py:5 | подтверждена (Р-22: sources.trackers.list) |
| PUT | `/api/trackers/<int:tracker_id>` | update_tracker | routes/trackers.py:15 | подтверждена (Р-22: sources.tracker.set_mirrors) |
| GET | `/directory-picker-test` | directory_picker_test | routes/__init__.py:26 | удалена (Р-22: тестовая страница; была зарегистрирована дважды) |
| GET | `/directory-picker-test` | directory_picker_test | routes/test.py:6 | удалена (Р-22: дубль регистрации) |
| GET | `/hello-world` | hello_world | routes/__init__.py:30 | удалена (Р-22: тестовая страница разработки) |
