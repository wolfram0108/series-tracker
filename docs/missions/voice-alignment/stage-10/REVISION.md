# Этап 10 — реестр ревизии существующего кода

> Дата: 2026-11-01; закрыт 2026-11-02: у каждой точки статус, отметки «сделано (П-N)» сверены сторожем `revision_registry`.
> Каждая точка кода, через которую проходит этап, получает статус: **подтверждена** (остаётся, с обоснованием),
> **перепроектирована** (переписывается под `ARCHITECTURE.md`), **удалена** (вместе со всеми вызовами в том же заходе).
> Точка без статуса в работу не берётся. Новое место — модуль и контракт из `ARCHITECTURE.md`.

## 1. Чтение сайтов (`modules/sources`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `sources.parse` для веб-источников (`module.py::on_parse`, ветка провайдера) | разбор страницы для формы, скана и озвучек; три формы ответа | удалена для веб-источников; торрентная ветка подтверждена | `sources.table.get`; один путь чтения (З-1) | `scan._included_episodes`, `voices._dispatch`, `gateway.parse_url`, `gateway.scrape_titles` — сделано (П-3): обработчик веб-ветки удалён; вызовы меняют П-5, П-6, П-8 |
| `sources.video.layout` (`on_video_layout`, `RuchimeProvider.layout`) | сезоны каталога Vibix отдельным чтением | удалена | каталог Vibix — строки таблицы ruchime | `gateway /api/web_video/layout`, `web/useWebSources` — сделано (П-3): обработчик удалён; вызовы меняют П-8, П-9; `web` — сделано (П-9) |
| `sources.video.coverage` (`on_video_coverage`, `RuchimeProvider.coverage`, событие `sources.coverage.progress`) | обход всех серий для окна добавления | удалена | полное чтение таблицы с событием `sources.table.progress` | `gateway /api/web_video/coverage`, `SSE coverage_progress`, `web/useWebSources` — сделано (П-3): обработчик и событие удалены; вызовы меняют П-8, П-9; `web` — сделано (П-9) |
| `sources.video.scan` (`on_video_scan`, `scan_episodes` провайдеров, событие `sources.video.progress`) | опрос серий под скан; у ruchime младшие плееры спрашиваются, только если ещё можно найти разрешение выше | удалена | таблица полная (З-2); кандидаты — из раскладки | `scan._scan_web_video`, `gateway.scrape_titles`, `SSE video_scan_progress` — сделано (П-3): обработчик и событие удалены; вызовы меняют П-6, П-8 |
| `sources.vk.scan` (`on_vk_scan`, `sources/vk.py`) | поиск видео канала, `video_to_fact` | перепроектирована | сайт `vk_channel` таблицы; запрос и режим поиска — поля источника | `scan._scan_vk`, `gateway.scrape_titles` — сделано (П-3): сайт `vk_channel`, топик и `sources/vk.py` удалены; вызовы меняют П-6, П-8 |
| `sources.provider.info` | есть ли у провайдера каталог озвучек | удалена | у всех сайтов одна форма таблицы | `voices._dispatch` — сделано (П-3): обработчик удалён; вызовы меняет П-5 |
| `video_providers/base.py::BaseVideoProvider` (`parse_release`, `scan_episodes`, `layout`, `coverage`, `has_voice_catalog`, `has_layout`) | контракт провайдера с разными формами по сайтам | перепроектирована | контракт сайта: «прочитать страницу → строки таблицы» | все провайдеры, `registry.py` — сделано (П-3): каталог `video_providers/` удалён, `StreamUnavailable` — в `players/contract.py` |
| `video_providers/table.py::Row.number`, `ReleaseTable` | строка таблицы с номером серии от парсера; таблица в памяти на 15 минут | перепроектирована | строка без номера (`ARCHITECTURE.md`, 4.1); таблица в БД со сроком 1 час | провайдеры — сделано (П-3): таблица — `TableReader` и БД |
| `video_providers/views.py` (`pick`, `facts`, `availability_report`, `coverage`, `played`, `causes`) | выбор лучшего потока, факты скана, отчёт о плеерах, охват диапазонами | удалена | выбор референса — `scan`; сводка источника — `composition` | провайдеры, `scan.save_availability` — сделано (П-3) |
| `video_providers/ranges.py::episode_ranges` | диапазоны серий у парсеров | удалена из `sources` | диапазоны появляются только из ответа конструктора (З-3); форматирование для окна — `web/src/lib` | `dunhuatv._describe`, `ruchime._vibix_sources`, `views.coverage` — сделано (П-3) |
| `video_providers/players.py::PLAYER_ORDER` | порядок плееров ruchime: VK, AllVideo, Vibix | перепроектирована | плееры страницы читаются все, включая Kodik; порядок выбора — приоритет источников `voices` | `ruchime` — сделано (П-3): файл удалён, плееры — реестр `players/registry.py`; Kodik страницы ruchime читает сайт `ruchime` тем же кодом, что `dunhuatv` |
| `ruchime/provider.py` (`_needs_lower_source`, `_apply_vibix`, `_quick_report`, `_survey`, `_rows_for`, `SURVEY_TTL_S`) | частичный опрос под скан, отдельный обход для окна, кэш на 15 минут | перепроектирована | сайт `ruchime`: все строки списка, все плееры, каталог Vibix | — сделано (П-3): `sites/ruchime/` |
| `ruchime/site.py::episode_title` | заголовок «<название сериала> - N серия» из номера | удалена | текст строки — `title` списка серий сайта | `site.read_episode`, `ruchime._rows_for` — сделано (П-3) |
| `ruchime/address.py` (`Choice`, `choice_of`, `address_of`: `?vibix=`, `?off=`) | выбор сезона Vibix и отключённых плееров в адресе сериала | перепроектирована | поля источника сериала (`catalog`) | `ruchime`, `vibix.link_of`, `web/webSources` — сделано (П-3): выбор применяет проекция `composition`, сайт читает всё; `web` — сделано (П-9) |
| `dunhuatv.py` (`split_address`, `Choice`, `_LEGACY_PLAYER`, `_choose`, `_catalog(everything=…)`) | сериал берёт один плеер и одну озвучку, выбор в адресе | перепроектирована | сайт `dunhuatv`: все плееры и все озвучки; выбор референса — `scan` | `voices`, `web/AddSeriesModal` — сделано (П-3): сайт `sites/dunhuatv/`; `voices` — П-5, `web` — сделано (П-9) |
| `dunhuatv.py::_seasons_of` и отказ `season_clash` | сезон CVH отбрасывается; совпавшие номера двух сезонов — отказ всего каталога | удалена | группа плеера в строке; номера — конструктор | тесты `test_sources_dunhuatv` о `season_clash` — сделано (П-3) |
| `dunhuatv.py::_episode_title` | заголовок «<название> - N серия» | удалена | текст строки сайта; для CVH — `line_of` | — — сделано (П-3) |
| `players/cdnvideohub.py::read_playlist` (`season or 1`, ключ `(studio, voice_type, season, episode)`) | подставляет сезон 1; озвучка = студия + тип | перепроектирована | сезон — как отдал плеер; имя озвучки — открытый вопрос `../ROADMAP.md` | `dunhuatv` — сделано (П-3): сезон — «N сезон» как дал плеер, нет сезона — нет группы; озвучка — студия и тип (Д-31); строка на элемент |
| `players/kodik.py::parse_episodes` | номер серии = `value` пункта | перепроектирована | строка = пункт: `data-id`, текст пункта, сезон из списка сезонов | `dunhuatv`, `KodikPlayer.fetch` — сделано (П-3): строка — пункт списка (`data-id`, текст дословно), сезон из списка сезонов |
| `players/kodik.py::parse_voices`, `players/vibix_protocol.py::parse_playlist` (номер из заголовка регуляркой) | имя озвучки Kodik читается; номер Vibix извлекается регуляркой | перепроектирована | номер Vibix не извлекается: текст «Серия N» уходит конструктору; озвучка потока `{…}` — в строку | `vibix.py`, `ruchime` — сделано (П-3): у записи каталога Vibix место (`position`) и озвучка потока, у пункта Kodik id и текст |
| `players/*` контракт `find / fetch / read`, `policy.py`, `core/retry.py` | общий контракт плееров, одна политика повторов | подтверждена | соответствует З-5 и Р-61; меняется только состав строки | — |
| `sources.video.stream` и `resolve_stream` провайдеров | поток кандидата перед загрузкой; пять имён плееров в диспетчере | перепроектирована | поток даёт сам плеер (`stream` в контракте плеера, с границей высоты озвучек); `resolve_stream` перебирает реестр плееров | `downloads` |
| `core/contracts/release.py` (`Release`, `ReleaseSource`) | ответ `parse_release`; источник с `first/last/ranges/episodes/links` | удалена | `core/contracts/release_table.py` | `voices.layout`, `gateway.schemas`, `tests/provider_contract.py` |
| `tests/provider_contract.py::check_release` | требует от провайдера без каталога озвучек пустых `sources`, а от провайдера с каталогом — отсортированных номеров и диапазонов | перепроектирована | проверка таблицы страницы | тесты провайдеров — сделано (П-3): файл удалён, таблицу проверяют тесты сайтов |

## 2. Сериал и источники (`modules/catalog`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `series.url`, `series.source_type`, `series.parser_profile_id`, `series.vk_search_mode` | один источник и один профиль на сериал | перепроектирована | `series_sources` для всех сериалов (Д-38); торрент-сериал получает один источник, торрентный путь берёт адрес из него и в остальном не меняется | `scan`, `torrents`, `voices`, `rules.format_*`, `gateway`, `web` — сделано (П-1) |
| `series.season`, `ignored_seasons`, `ignored_episodes` | сезон по умолчанию и память отключённого | подтверждена | термины серии, принадлежат сериалу | — |
| `series.vk_quality_priority` (приоритет разрешений пользователя в планировщике) | на проде не задан ни у одного сериала с веб- или VK-источником | удалена | Д-36: действует Т-20 | `scan._quality_priority`, `planner._quality_key`, `gateway /vk-quality-priority`, `web/StatusVkComposition` — сделано (П-1) |

## 3. Конструктор (`modules/rules`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `rules.apply` (список заголовков, ответ по порядку) | применение профиля | перепроектирована | строки с ключами, ответ по ключу | `composition` (единственный вызов) — сделано (П-2) |
| `rules.test` | тот же разбор для окна теста | удалена | `rules.apply` уже отдаёт события правил; один разбор | `gateway.rules_test`, `web/ParserTest` — сделано (П-2) |
| `rules.format_filename` (правила применяются к `source_title` ещё раз) | имя файла VK-элемента | перепроектирована | имя по номерам раскладки, без повторного применения | `downloads`, `renaming` — сделано (П-2) |
| `rules.format_torrent_file` | имя файла торрента | подтверждена | торренты вне этапа | — |
| движок `engine.py`, блоки и действия | язык правил | подтверждена; добавление действия «назначить диапазон» — открытый вопрос | — | обратный диапазон («10-5») не извлекается, причина в `errors` — сделано (П-2) |
| действие `assign_voiceover` (сейчас пишет только тег имени файла `voiceover_tag`) | тег в имени файла | перепроектирована | имя озвучки строки, главнее имени от плеера и сайта (Д-37); дальше проходит через группы озвучек | `scan._vk_candidates`, `rules/formatter.py` |
| сброс кэша вызовами CRUD | кэш скомпилированных профилей | подтверждена; добавляется событие `rules.profile.changed` | раскладка пересчитывается сама (Т-14) | — сделано (П-2) |

## 4. Раскладка и скан (`modules/scan`, новый `modules/composition`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `scan._included_episodes` | правила к быстрому списку, ответ сопоставляется с номером парсера | удалена | раскладка | `scan._scan_web_video` — сделано (П-6) |
| `scan._scan_web_video`, `scan._scan_vk` | отдельные ветки скана по виду источника | перепроектирована | синхронизация: таблицы источников → раскладка → план | `scan._run_series`, `scan.on_composition` — сделано (П-6): `scan._sync_video`, `sync.py` |
| `scan._process_video_candidates`, `_vk_candidates` (вызов `rules.apply`) | правила к заголовкам кандидатов | перепроектирована | кандидаты — строки раскладки одиночных серий | — сделано (П-6): `reference.py`, `candidates.py` |
| `scan._resolve_part_ones` и повтор той же логики в `scan._reconstruct` | «часть 1» для серии, у которой есть части — в двух местах | перепроектирована | одно место: `composition/project.py` | `scan` — сделано (П-4) |
| `scan._reconstruct` (`rules.apply` по сохранённым заголовкам) | номера для окна композиции вычисляются заново | удалена | `composition.get` | `scan.on_composition`, `gateway /composition` — сделано (П-6) со стороны `scan` |
| `scan.on_composition` (`refresh` запускает скан) | окно композиции и скан в одном запросе | перепроектирована | окно читает раскладку и план; синхронизация — отдельная команда | `web/StatusComposition`, `web/StatusVkComposition` — `scan.composition` удалён (П-6), план отдаёт `scan.output.get`; окно — П-9 |
| `scan.save_availability`, `scan.availability`, таблица `video_availability` | отчёт о плеерах после скана | удалена | состояния строк таблицы, сводка `composition` | `gateway api_series`, `web` — сделано (П-6) со стороны `scan`: запрос, таблица `video_availability` (миграция 0028) и сохранение отчёта удалены; шлюз и окно — П-8, П-9; окно не читает отчёт о плеерах — сделано (П-9) |
| `scan/planner.py` шаги 3–5 (склейки закрывают дыры и вытесняют одиночки) | склейка может стать референсом | перепроектирована для веб- и VK-источников | Т-21, Д-32: склейка не референс; серия, покрытая только склейкой, — ошибка «референс не подходит» | `scan._rebuild_plan` — сделано (П-6): склейка не попадает в план; победитель слота — референс `reference.py`, `planner.py` его только исполняет |
| `modules/slicing` (нарезка склейки VK по главам на серии) | работает со склейкой, стоящей в плане референса; на проде нарезанных файлов нет | подтверждена без изменений, в новой архитектуре не участвует | Д-34: код не трогается; склейка в план не попадает, нарезка не вызывается; вызовы из `scan.on_composition` уходят вместе с ним | `scan.on_composition` (`slicing.files.drop_for_source`, `slicing.verify`) — сделано (П-6): вызовов `slicing` в `scan` нет |
| `plan_with_supersession`, `superseded_by` (Р-37) | замена низкого разрешения высоким | подтверждена | Т-24 | — сделано (П-6): `plan_with_supersession` исполняет референс; не скачанные строки слота, не ставшие референсом, из `media_items` убираются |
| `core/ids.media_unique_id` (адрес + дата + сериал) | ключ кандидата | перепроектирована | ключ строки источника; скачанное сохраняет `unique_id` сопоставлением по адресу | `scan.repository`, `downloads` — сделано (П-6): ключ — (сериал, источник, ключ строки), `core/ids.py::row_unique_id` и `upgrade_row_unique_id` рядом с прежними формулами (те не менялись); сопоставление по адресу — `scan/candidates.py` |

## 5. Озвучки (`modules/voices`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `voices._dispatch` (`sources.provider.info`, `sources.parse` с `links`, `plan_layout(manual, selected_source)`) | озвучки только у сайта с каталогом озвучек; референс — выбранный источник из адреса | перепроектирована | вход — раскладка и план референсов | `scan` (команда `voices.dispatch`) — сделано (П-5) |
| `voices._reference_episodes` (серия озвучки = номер на сайте у референса) | озвучки идут мимо конструктора | удалена | серия озвучки — из раскладки по номерам конструктора | сделано (П-5) |
| `voices/layout.py::plan_layout` (режимы `manual` и `auto`, основной источник по числу серий) | раскладка озвучек одного сайта | перепроектирована | авторежим удалён; основной плеер озвучки — по приоритету источников | `voices.on_layout`, `gateway.parse_url` — сделано (П-5) |
| `voices/layout.py::choose_reference` | выбор референса по разрешению и приоритету | удалена | одно место выбора — `scan` | тесты — сделано (П-5) |
| `voices/layout.py::_ranges` | диапазоны для окна | удалена | З-3 | сделано (П-5) |
| `voices.layout` (запрос) | раскладка для окна добавления | удалена | `composition.get` | `gateway.parse_url` — сделано (П-5) |
| `voices/identity.py` (`_fold`, `_SEPARATORS`, `tokens`, `voice_groups`, `group_key`, `same_voice`) | озвучки склеиваются без групп: по написанию и по составу имени «A / B» | перепроектирована | только группы пользователя (З-6); сравнение с триггером без регистра и пробелов сохраняется | `voices.layout`, `_adopted_keys`, тесты `test_voices_groups_effect` — сделано (П-5) |
| `voices._adopted_keys` | ключ озвучки переживает смену правил | перепроектирована | ключ озвучки — группа либо «неизвестная N» из `composition` | сделано (П-5) |
| `voice_dubs` (`site_episode`, `chain`, `temp_path` на строке серии) | файл озвучки принадлежит одной серии | перепроектирована | `voice_files`: файл строки источника, общий для серий диапазона (Т-18) | `voices.repository`, журнал очистки — сделано (П-5) |
| `voices/editor.py`, `voices/settings.py` (`voices.groups`, `voices.player_groups`, `voices.source_priority`) | группы и порядок источников | подтверждена | данные групп и приоритета — у `voices` | — |
| `DUB_MAX_HEIGHT = 720`, conform, выход рядом, следование за референсом, журнал очистки, восстановление | Р-40 … Р-59 | подтверждена | Т-19, Т-27 | вход заменяется на раскладку |

## 6. Шлюз (`modules/gateway`)

| Точка | Сейчас | Статус | Новое место / причина |
|---|---|---|---|
| `POST /api/parse_url` (веб-ветка и вызов `voices.layout`) | шлюз собирает ответ из двух модулей | перепроектирована | торренты — как есть; веб-источник — `sources.table.get` и сводка `composition` — сделано (П-8): страница трекера разбирается `sources.parse`, адрес, который читает сайт (`sources.site.resolve`), — отказ 400 `not_a_torrent_page`; сводку и строки даёт `POST /api/sources/preview` |
| `POST /api/web_video/coverage`, `POST /api/web_video/layout` | отдельные чтения | удалена | таблица страницы — сделано (П-8): точки, модели `WebVideoCoverage`/`WebVideoLayout` и их типы удалены |
| `POST /api/parser-profiles/scrape-titles` (ветвление VK / веб, повторный скан, фильтр по запросу) | бизнес-логика в шлюзе | перепроектирована | строки для конструктора из таблицы источника (`line_of`) — сделано (П-8): точка удалена вместе с регулярными выражениями ссылок VK и подстроками адресов; строки — `composition.preview` |
| `POST /api/parser-profiles/test` | `rules.test` | перепроектирована | `rules.apply` — сделано (П-8): тело `{url, options?, profile_id, series_season?}`, ответ `composition.preview` (строки для конструктора и ответ на каждую) |
| `GET /api/series/{id}/composition` | `scan.on_composition` | перепроектирована | `composition.get` + план `scan` — `scan.on_composition` удалён (П-6); сделано (П-8): видео — `composition.get`, торрент — `torrents.composition`; план референсов — `GET /api/series/{id}/output` (`scan.output.get`), ответы не склеиваются |
| `GET /api/series/{id}` (`scan.availability`) | отчёт о плеерах | перепроектирована | сводка источников `composition` — `scan.availability` удалён (П-6); сделано (П-8): ответ несёт `kind` и `sources` из `catalog` |
| `SSE_MAP`: `video_scan_progress`, `coverage_progress` | ход старых чтений | удалена | `sources_table_progress`, `sources_table_ready`, `composition_changed`, `reference_changed` — сделано (П-8): добавлены также `sources_table_failed`, `composition_failed` |
| `GET /api/series/{id}/sliced-files` | склейка ответов `slicing` и `scan` в шлюзе | удалена | бизнес-логика в шлюзе, `slicing` в этапе не участвует (Д-34) — сделано (П-8); панель — сделано (П-9) |
| `POST /api/series/{id}/reprocess_vk_files` | дубль `POST /api/series/{id}/reprocess` | удалена | один вызов `renaming.reprocess`, вид сериала решает `renaming` — сделано (П-8); панель — сделано (П-9) |
| `POST /api/preview/naming/fetch` (оркестровка `torrents` и `sources`, событие `torrents.preview.failed` из шлюза) | шлюз вёл три модуля и издавал чужое событие | перепроектирована | команда `torrents.preview.fetch {items}` у `torrents`, исходы издаёт он один; шлюз шлёт команду — сделано (П-8); темы `torrents.preview.parse_torrent` и `.fetch_magnet` стали внутренними |
| `FILE_DUMP_FLAGS` (`save_html_*`, `save_json_vk_scraper`) в `GET /api/settings/debug_flags` | флаги, которых никто не читает | удалена | сканер VK удалён, потребителей нет — сделано (П-8); блок в панели — сделано (П-9) |
| `downloads.fs.sync`, `torrents.fs.verify` для сериала чужого вида | сверка и предупреждение о пути впустую | перепроектирована | сразу `{0, 0}` по `kind` из `catalog.series.get`, ничего не публикуется — сделано (П-8) |
| `PUT /api/media-items/{unique_id}/ignore`, `PUT /api/series/{id}/media-items/ignore-season` | HTTP-отключение строки плана и сезона у прежней вкладки; дубль отключения серии | удалена | `PUT /api/series/{id}/output/switch` (`scan.output.switch`): серия или сезон по номерам, память сериала и строки плана меняются вместе; отключённая серия остаётся в выходе с отметкой `off` — сделано (П-9) |
| `scan.items.set_ignored_bulk` (обработчик `on_set_ignored_bulk`, массовая правка строк сезона) | единственным отправителем был удалённый маршрут | удалена | то же — сделано (П-9) |

## 7. Интерфейс (`web/src`)

| Точка | Сейчас | Статус | Новое место / причина |
|---|---|---|---|
| `components/WebSourcesEditor.vue`, `composables/useWebSources.ts`, `lib/webSources.ts` (`SOURCE_ORDER = vk, allvideo, vibix`) | источники только ruchime | перепроектирована | один компонент источников (Т-31) — сделано (П-9): `SourcesEditor.vue` и `SourceCard.vue`, данные — `useSourceDrafts.ts` и `useSeriesSources.ts`, логика строк — `lib/sources.ts` |
| `AddSeriesModal.vue`: выпадающий список озвучки dunhuatv, `playerReportRows`, блок «Озвучки» (`webLayout`) | отдельный экран под каждый сайт | перепроектирована | компонент источников; блок озвучек — раздел «Выход» — сделано (П-9): список источников копится в окне до «Добавить», блока озвучек нет |
| `status/StatusProperties.vue`, блок «Источники» | редактор ruchime | перепроектирована | компонент источников с добавлением и удалением источника — сделано (П-9): действия идут сразу в `/api/series/{id}/sources` |
| `status/StatusComposition.vue`, `status/StatusVkComposition.vue` | номера из `scan._reconstruct` | перепроектирована | раскладка и план; раздел «Выход» — сделано (П-9): `StatusOutput` несёт все функции прежней вкладки видео-сериала — отключение серии и сезона (`output/switch`), имена файлов (`rename_preview`), переприменение правил (`reprocess`), глубокое усыновление (`deep-adoption`), синхронизация (`scan`), «Перечитать» у источника; убраны решениями: приоритет качества (Д-36), отчёт о доступности (`video_availability`), показ склеек и нарезки (Д-34: вкладка «Нарезка» осталась как была) |
| `settings/ParserTest.vue` (`scrape-titles`, `test`) | тест правил по заголовкам скана | перепроектирована | строки для конструктора выбранного источника |
| `settings/SettingsVoices.vue`, `VoiceGroupsEditor.vue` | группы и порядок источников | подтверждена | — |

## 8. Документы и решения

| Точка | Статус | Причина |
|---|---|---|
| `../CHARTER.md`, требование 2 (авторежим) | перепроектирована | авторежим снят; референс — Т-20…Т-24 |
| `../CHARTER.md`, требование 11 (выбор плеера и озвучки в окне добавления с диапазонами серий) | перепроектирована | диапазоны — только после назначения профиля (Т-13) |
| `../CHARTER.md`, требование 15 (первая версия — сравнение имён без групп) | перепроектирована | З-6 |
| `../CHARTER.md`, «Определение готовности источника», п. 1 | перепроектирована | выбор плеера и озвучки заменён источниками и референсом |
| Р-35 «исключённые серии не опрашиваются» | удалена | З-2 |
| Р-60 «выбор в адресе сериала» | перепроектирована | Т-9 |
| Р-37 «один файл на слот», Р-40 … Р-59 | подтверждена | см. разделы 4 и 5 |

## 9. Дополнения после сверки с кодом (перед реализацией)

Сверка по коду нашла точки, которых разделы 1–8 не называли. Статусы и правила те же.

### 9.1. Знание сайтов вне `sources` и дубли внутри

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `downloads/ytdlp.py` (`is_allvideo_url`, `_resolve_allvideo_stream`, `_extract_best_stream_from_playerjs`, ветка в `download_video`) | собственный разбор плеера AllVideo с заголовком Referer ruchime — копия `sources/players/allvideo.py` | удалена | поток кандидата разрешает только `sources.video.stream` (З-5) | `ytdlp.download_video`, тесты `test_downloads_module.py` (`test_is_allvideo_url` и тест с `_resolve_allvideo_stream`) — сделано (П-7): разбор и ветка удалены вместе с заголовком Referer AllVideo по умолчанию для прямой ссылки; заголовки потока даёт `sources.video.stream` |
| `RuchimeProvider.vibix` и `PLAYER_ORDER[2]` | два экземпляра плеера Vibix; браузер настроен только у одного | перепроектирована | один экземпляр плеера в реестре плееров `sources` | `ruchime`, `players.py` — сделано (П-3): `PlayerRegistry` модуля |
| `video_providers/table.py::ReleaseTable.fill` (исключение пробы → `Cell(False)` без `transient`), `ruchime/provider.py` (`_quick_report`), `views.py` (ячейка без ответа) | исключение пробы даёт «нет», хотя Т-5 требует «не получено» | перепроектирована | исключение пробы → состояние «не получено» с причиной | `ruchime`, `views` — сделано (П-3): `players/asking.py::outcome_of` |
| `video_providers/__init__.py::_DEFAULT_REGISTRY`, `players.py::PLAYER_ORDER` | глобальные реестры сайтов и плееров | перепроектирована | реестры принадлежат модулю `sources`; регистрация по `ARCHITECTURE.md`, раздел 12 | `run.py`, `tests/conftest.py`, `tests/test_contracts.py` — сделано (П-3): реестр сайтов и реестр плееров — у экземпляра модуля |
| `RuchimeProvider._tables`, `_table_locks`, `SURVEY_TTL_S`, `ReleaseTable.expires_at` | кэш таблицы в памяти по slug без учёта выбора в адресе | удалена | таблица в БД, срок 1 час, замок страницы (Т-2, Т-4) | `test_sources_ruchime_players.py` (проверка `_tables["test"].expires_at`) — сделано (П-3) |
| `gateway/api_settings.py` (регулярное выражение ссылки VK, подстроки `vk.com`, `vkvideo.ru`, `@`), `web/src/lib/webSources.ts::SOURCE_ORDER`, `AddSeriesModal.vue` (метки плееров) | знание сайтов и плееров вне `sources` | перепроектирована | вид сайта по адресу — реестр сайтов `sources`; метка плеера — поле строки таблицы `player_label`; порядок плееров — настройка `voices.source_priority` | `scrape_titles` — сделано (П-8), `web` — сделано (П-9) |
| `tests/test_retry.py` (три сторожа, читающие исходники по жёсткому списку путей) | ломаются при переименовании файлов | перепроектирована | список путей приводится к новой файловой раскладке | `tests/test_retry.py` — сделано (П-3) |

### 9.2. Конструктор (`rules`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `rules/repository.py` (чтение таблицы `series`) | модуль читает таблицу другого модуля | удалена | профиль приходит параметром запроса (`profile_id`), у торрентов — из источника сериала | `rules.format_torrent_file`, `rules.format_filename` — сделано (П-2) |
| `rules.format_filename` — вызывающие | `renaming` (4 вызова), `downloads` (1), `slicing/module.py:379` (1) | перепроектирована | единый контракт «по готовым номерам»; поля запроса `series`, `media_item`, `episode_override` сохраняются, `slicing` вызывает без правки (Д-34) | `renaming`, `downloads` — сделано (П-2) |
| `rules.cache.invalidate` | топик без единого вызывающего | удалена | событие `rules.profile.changed` | — сделано (П-2) |

### 9.3. Потребители вида и профиля сериала

Поля `series.source_type` и `series.parser_profile_id` читают, кроме перечисленных в разделах 4–6, модули ниже. Все они переходят на `catalog.series.get`: `kind` (`torrent` либо `video`) и `sources` (список источников).

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `renaming/module.py` (108, 167, 174, 179) | вид и профиль сериала | перепроектирована | `kind`, профиль источника | — сделано (П-7): профиль торрентного источника — `core.torrent_profile_id`; имена видео по номерам строк, профиль не нужен |
| `library/module.py` (147), `metadata/module.py` (175) | ветка «видео или торрент» по `source_type` | перепроектирована | `kind` | — сделано (П-7) |
| `downloads/module.py` (144, 654) | `source_type` решает, есть ли запасной путь yt-dlp, для всей серии | перепроектирована | решение по плееру кандидата, не по сериалу: в серии с несколькими источниками виды смешаны | `ytdlp`, тесты `test_downloads_module.py` — сделано (П-7): источник кандидата — `media_items.source_id`; без него — единственный источник серии, иначе `web_video`; задачи без строки `media_items` снимаются при `scan.plan.updated` |
| `torrents/module.py` (788), `rules/formatter.py` (103) | профиль торрента, расширение `.mp4` по виду | перепроектирована | профиль из источника; расширение по кандидату | — сделано (П-7): `torrents/module.py`; `rules/formatter.py` — П-2 |
| `torrents/repository.py` (строки `task_type='torrent'` в `download_tasks`: запись и чтение прогресса, `LEFT JOIN` по `qb_hash`, чистка по каскаду) | торренты пишут в таблицу `downloads`, различая строки по `task_type`; две стороны одной таблицы | перепроектирована | таблица `torrent_progress` модуля torrents, одна строка на infohash (миграция 0031); `downloads` о торрентах не знает | `torrents.db.progress.list` и `torrents.progress.changed`: `task_key` → `qb_hash`, полей `video_url`, `save_path`, `attempts`, `task_type`, `total_size_mb` нет; сторона торрентов — сделано (П-7); `web/src/components/settings/SettingsAgents.vue` (строки 77, 80) — П-9 |
| `voices/module.py` (383) | пропуск не-web серий | перепроектирована | вход озвучек — раскладка | сделано (П-5) |
| `scan/module.py` (вид, адрес и профиль сериала, `vk_search_mode`, `vk_quality_priority`) | ветвление по `source_type`, адрес и профиль в самом сериале | перепроектирована | `kind`; адрес и профиль торрента — из источника сериала; веб- и VK-сериалы — синхронизация и референс | сделано (П-6) |
| `gateway/api_series.py` (`list_series`, `add_series`, `series_details`, `update_series`, `set_state`), `api_media.py` (`composition`, `source_filenames`) | ветвление по виду, сборка ответов из нескольких модулей | перепроектирована | тело запросов с источниками; сборка ответов — в модулях-владельцах | `web`, `schema.d.ts` — сделано (П-8): тела `SeriesCreate`/`SeriesUpdate`, эндпоинты источников и выхода, `GET /api/series` без ветвления по виду (счётчики складываются по владельцам), сверка файлов при открытии окна просит каждого владельца, `renaming.reprocess` без ветки по виду; `schema.d.ts` пересобран; `web` — сделано (П-9, ступень 1) |

## 10. Точки, тронутые кругом ревью (2026-11-02)

Независимое ревью нашло дефекты на границах модулей; исправления меняют следующие точки.

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `composition/project.py::_numbers_of`, `_with_part_one`, `DEFAULT_SEASON` | сезон 1 и «часть 1» назначала раскладка | перепроектирована | конструктор: `rules/numbering.py` (`season_of`, `settle_parts`), строка конструктора несёт плеер и озвучку; раскладка копирует ответ (З-3) | `rules.apply`, `composition.lines_for` |
| `scan/candidates.py::place` (строка с прежним ключом и другим адресом) | старая строка принимала новый адрес и сохраняла скачанный файл | перепроектирована | другое видео при скачанном прежнем — строка версии (`ids.version_row_unique_id`), прежняя вытесняется планом | `scan._refresh`, `downloads` |
| `downloads/module.py::_adopt_existing_files`, загрузка вытесняющего видео | усыновлял любой файл с ожидаемым именем | перепроектирована | файл, которым владеет другая строка, не усыновляется; файл вытесняемого видео убирается перед загрузкой нового | `scan.plan.updated` |
| `voice_dubs`, `voices/planning.py`, `voices/repository.py`, `voices.dub.changed` | ключ (сезон, серия, озвучка) | перепроектирована | ключ с частью (миграция 0032); события и снимок несут `part` | `downloads.episodes.get`, `web/src/stores/voices.ts` |
| `voices/repository.py::drop_unplanned` | снимала ждущие и упавшие озвучки | перепроектирована | серия с известным референсом: озвучка вне плана снимается в любом состоянии | `downloads.fetch.cancel` |
| `scan/module.py::on_output_switch`, `on_set_ignored` | меняли флаг и отметку, план не пересчитывался | перепроектирована | после выбора пользователя план серии пересчитывается (`_recount`) | `scan.plan.updated`, `downloads` |
| `scan/module.py::on_start`, `_run_series` (видео-синхронизация) | команда жила в памяти | перепроектирована | команда в `scan_tasks` до первого ожидания; возобновление по `system.started` | `run.py` |
| `library/module.py::on_start`, `renaming/module.py::on_start` | возобновление до старта соседей | перепроектирована | возобновление по `system.started` | `run.py` |
| `reference.allow` (`core/contracts`, `composition/levers.py`, `scan/reference.py`, `web/src/lib/output.ts`) | участие плеера источника в выборе референса | удалена | `variant.allow`: участие варианта (группа, источник, плеер) в референсе и в озвучках | `voices/planning.py`, окно «Выход» |
| `web/src/components/status/OutputLevers.vue`, `lib/output.ts::LEVER`, `defaultValue` | четыре блока рычагов, имена и умолчания в окне | перепроектирована | сетка по описаниям рычагов ядра (`LeverSpec`), умолчание из описания | `composition.get` |
| `sources/players/streams.py::resolve_stream` | пять имён плееров, Vibix без границы высоты | перепроектирована | `stream` в контракте плеера, перебор реестра | `sources.video.stream` |
| `web/src/styles/fields.css` (`.item-floating-label`, `.selected-value`) | подпись и значение обрезались многоточием | перепроектирована | подпись поднятого поля — строка над значением, переносится; значение списка переносится | все окна с полями |
| `scan/candidates.py::place`, `core/ids.py::upgrade_row_unique_id`, `scan/repository.py::save_outcome` | строка с задачей в очереди меняла адрес на месте; идентификатор версии не знал адреса | перепроектирована | Р-68 (1): очередь учитывается, адрес входит в идентификатор, запись адреса не касается качаемой строки | `scan._refresh`, тесты |
| `downloads/module.py::_run_download`, `_take_over`, `_work_path`, `_adopt_existing_files` | загрузка писала под именем видео, прежний файл удалялся до успеха | перепроектирована | Р-68 (2): рабочее имя задачи, замена целым файлом, усыновление не берёт файл чужой задачи | `_stop_task`, `on_cancel` |
| `voices/module.py` (`downloads.fetch.cancel`), `downloads/module.py::on_fetch_cancel` | владелец приказывал исполнителю остановиться | перепроектирована | Р-68 (3): `work.withdrawn`, `downloads.on_work_withdrawn` | тесты загрузок и озвучек |
| `voices/planning.py`, `voices/module.py::_follow_variants`, `voices/repository.py::retire` | запрет варианта не действовал на идущую работу | перепроектирована | Р-68 (4) | `_plan_series` |
| `scan/module.py::_hand_over`, `downloads.on_dispatch`, `voices.on_dispatch` | команды без ответа, намерение удалялось до записи работы | перепроектирована | Р-68 (5): запросы с ответом после записи | `_sync_video` |
| `composition/settings.py::retarget`, `voices/planning.py::adopt` | слияние групп давало два ключа и выбор строки по номеру | перепроектирована | Р-68 (6) | `composition.module`, `voices._plan_series` |
| `alembic/versions/0032_voices_part.py::downgrade` | уходящие строки определялись по части | перепроектирована | Р-68 (7) | — |
| `core/contracts/output_settings.py::LEVER_FORMS`, `modules/composition/levers.py::register`, `web/src/components/status/OutputLevers.vue` | окно рисовало четыре формы из девяти | перепроектирована | Р-68 (8) | регистрация рычагов |
| `tools/collectors/{live_three,live_sync,migrate_copy}.py`, `verdicts.py` | PASS без пар и без путей дорожек | перепроектирована | Р-68 (9) | — |
| `downloads/module.py::_drop_tasks_without_plan_row`, `_remove_unwanted_files`, `downloads/repository.py::unwanted_files`, `scan/module.py::_removed_by_configuration` | идущая загрузка отключённой серии доходила до конца, файлы серий, снятых настройкой, оставались | перепроектирована | Р-68 (10): на диске то, что настроено | `on_plan_updated` |
| `core/contracts/availability.py`, `scan._require_idle`, `downloads.on_cancel`, `composition._require_pickable`, `web/src/lib/output.ts::canDo` | допустимое по статусам решали тексты исключений у владельцев и окно само | перепроектирована | Р-68 (11): одна таблица в ядре | `OutputLevers.vue` |
| `downloads/ytdlp.py::download`, `_whole_or_discarded`, `core/ffprobe.py::video_gaps` | загрузка принималась по коду выхода и порогу 85 % длительности, дыры в видео не искались | перепроектирована | Р-68 (12): файл с дырой не сохраняется, загрузка повторяется | `_run_download`, `_fetch_one` |
