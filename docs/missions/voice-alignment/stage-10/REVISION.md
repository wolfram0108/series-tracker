# Этап 10 — реестр ревизии существующего кода

> Дата: 2026-11-01. Состояние: проект; пополняется при разборе каждого аспекта перед реализацией.
> Каждая точка кода, через которую проходит этап, получает статус: **подтверждена** (остаётся, с обоснованием),
> **перепроектирована** (переписывается под `ARCHITECTURE.md`), **удалена** (вместе со всеми вызовами в том же заходе).
> Точка без статуса в работу не берётся. Новое место — модуль и контракт из `ARCHITECTURE.md`.

## 1. Чтение сайтов (`modules/sources`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `sources.parse` для веб-источников (`module.py::on_parse`, ветка провайдера) | разбор страницы для формы, скана и озвучек; три формы ответа | удалена для веб-источников; торрентная ветка подтверждена | `sources.table.get`; один путь чтения (З-1) | `scan._included_episodes`, `voices._dispatch`, `gateway.parse_url`, `gateway.scrape_titles` |
| `sources.video.layout` (`on_video_layout`, `RuchimeProvider.layout`) | сезоны каталога Vibix отдельным чтением | удалена | каталог Vibix — строки таблицы ruchime | `gateway /api/web_video/layout`, `web/useWebSources` |
| `sources.video.coverage` (`on_video_coverage`, `RuchimeProvider.coverage`, событие `sources.coverage.progress`) | обход всех серий для окна добавления | удалена | полное чтение таблицы с событием `sources.table.progress` | `gateway /api/web_video/coverage`, `SSE coverage_progress`, `web/useWebSources` |
| `sources.video.scan` (`on_video_scan`, `scan_episodes` провайдеров, событие `sources.video.progress`) | опрос серий под скан; у ruchime младшие плееры спрашиваются, только если ещё можно найти разрешение выше | удалена | таблица полная (З-2); кандидаты — из раскладки | `scan._scan_web_video`, `gateway.scrape_titles`, `SSE video_scan_progress` |
| `sources.vk.scan` (`on_vk_scan`, `sources/vk.py`) | поиск видео канала, `video_to_fact` | перепроектирована | сайт `vk_channel` таблицы; запрос и режим поиска — поля источника | `scan._scan_vk`, `gateway.scrape_titles` |
| `sources.provider.info` | есть ли у провайдера каталог озвучек | удалена | у всех сайтов одна форма таблицы | `voices._dispatch` |
| `video_providers/base.py::BaseVideoProvider` (`parse_release`, `scan_episodes`, `layout`, `coverage`, `has_voice_catalog`, `has_layout`) | контракт провайдера с разными формами по сайтам | перепроектирована | контракт сайта: «прочитать страницу → строки таблицы» | все провайдеры, `registry.py` |
| `video_providers/table.py::Row.number`, `ReleaseTable` | строка таблицы с номером серии от парсера; таблица в памяти на 15 минут | перепроектирована | строка без номера (`ARCHITECTURE.md`, 4.1); таблица в БД со сроком 1 час | провайдеры |
| `video_providers/views.py` (`pick`, `facts`, `availability_report`, `coverage`, `played`, `causes`) | выбор лучшего потока, факты скана, отчёт о плеерах, охват диапазонами | удалена | выбор референса — `scan`; сводка источника — `composition` | провайдеры, `scan.save_availability` |
| `video_providers/ranges.py::episode_ranges` | диапазоны серий у парсеров | удалена из `sources` | диапазоны появляются только из ответа конструктора (З-3); форматирование для окна — `web/src/lib` | `dunhuatv._describe`, `ruchime._vibix_sources`, `views.coverage` |
| `video_providers/players.py::PLAYER_ORDER` | порядок плееров ruchime: VK, AllVideo, Vibix | перепроектирована | плееры страницы читаются все, включая Kodik; порядок выбора — приоритет источников `voices` | `ruchime` |
| `ruchime/provider.py` (`_needs_lower_source`, `_apply_vibix`, `_quick_report`, `_survey`, `_rows_for`, `SURVEY_TTL_S`) | частичный опрос под скан, отдельный обход для окна, кэш на 15 минут | перепроектирована | сайт `ruchime`: все строки списка, все плееры, каталог Vibix | — |
| `ruchime/site.py::episode_title` | заголовок «<название сериала> - N серия» из номера | удалена | текст строки — `title` списка серий сайта | `site.read_episode`, `ruchime._rows_for` |
| `ruchime/address.py` (`Choice`, `choice_of`, `address_of`: `?vibix=`, `?off=`) | выбор сезона Vibix и отключённых плееров в адресе сериала | перепроектирована | поля источника сериала (`catalog`) | `ruchime`, `vibix.link_of`, `web/webSources` |
| `dunhuatv.py` (`split_address`, `Choice`, `_LEGACY_PLAYER`, `_choose`, `_catalog(everything=…)`) | сериал берёт один плеер и одну озвучку, выбор в адресе | перепроектирована | сайт `dunhuatv`: все плееры и все озвучки; выбор референса — `scan` | `voices`, `web/AddSeriesModal` |
| `dunhuatv.py::_seasons_of` и отказ `season_clash` | сезон CVH отбрасывается; совпавшие номера двух сезонов — отказ всего каталога | удалена | группа плеера в строке; номера — конструктор | тесты `test_sources_dunhuatv` о `season_clash` |
| `dunhuatv.py::_episode_title` | заголовок «<название> - N серия» | удалена | текст строки сайта; для CVH — `line_of` | — |
| `players/cdnvideohub.py::read_playlist` (`season or 1`, ключ `(studio, voice_type, season, episode)`) | подставляет сезон 1; озвучка = студия + тип | перепроектирована | сезон — как отдал плеер; имя озвучки — открытый вопрос `../ROADMAP.md` | `dunhuatv` |
| `players/kodik.py::parse_episodes` | номер серии = `value` пункта | перепроектирована | строка = пункт: `data-id`, текст пункта, сезон из списка сезонов | `dunhuatv`, `KodikPlayer.fetch` |
| `players/kodik.py::parse_voices`, `players/vibix_protocol.py::parse_playlist` (номер из заголовка регуляркой) | имя озвучки Kodik читается; номер Vibix извлекается регуляркой | перепроектирована | номер Vibix не извлекается: текст «Серия N» уходит конструктору; озвучка потока `{…}` — в строку | `vibix.py`, `ruchime` |
| `players/*` контракт `find / fetch / read`, `policy.py`, `core/retry.py` | общий контракт плееров, одна политика повторов | подтверждена | соответствует З-5 и Р-61; меняется только состав строки | — |
| `sources.video.stream` и `resolve_stream` провайдеров | поток кандидата перед загрузкой | подтверждена | поток живёт недолго, разрешается перед загрузкой | `downloads` |
| `core/contracts/release.py` (`Release`, `ReleaseSource`) | ответ `parse_release`; источник с `first/last/ranges/episodes/links` | удалена | `core/contracts/release_table.py` | `voices.layout`, `gateway.schemas`, `tests/provider_contract.py` |
| `tests/provider_contract.py::check_release` | требует от провайдера без каталога озвучек пустых `sources`, а от провайдера с каталогом — отсортированных номеров и диапазонов | перепроектирована | проверка таблицы страницы | тесты провайдеров |

## 2. Сериал и источники (`modules/catalog`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `series.url`, `series.source_type`, `series.parser_profile_id`, `series.vk_search_mode` | один источник и один профиль на сериал | перепроектирована | `series_sources` для всех сериалов (Д-38); торрент-сериал получает один источник, торрентный путь берёт адрес из него и в остальном не меняется | `scan`, `torrents`, `voices`, `rules.format_*`, `gateway`, `web` |
| `series.season`, `ignored_seasons`, `ignored_episodes` | сезон по умолчанию и память отключённого | подтверждена | термины серии, принадлежат сериалу | — |
| `series.vk_quality_priority` (приоритет разрешений пользователя в планировщике) | на проде не задан ни у одного сериала с веб- или VK-источником | удалена | Д-36: действует Т-20 | `scan._quality_priority`, `planner._quality_key`, `gateway /vk-quality-priority`, `web/StatusVkComposition` |

## 3. Конструктор (`modules/rules`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `rules.apply` (список заголовков, ответ по порядку) | применение профиля | перепроектирована | строки с ключами, ответ по ключу | `composition` (единственный вызов) |
| `rules.test` | тот же разбор для окна теста | удалена | `rules.apply` уже отдаёт события правил; один разбор | `gateway.rules_test`, `web/ParserTest` |
| `rules.format_filename` (правила применяются к `source_title` ещё раз) | имя файла VK-элемента | перепроектирована | имя по номерам раскладки, без повторного применения | `downloads`, `renaming` |
| `rules.format_torrent_file` | имя файла торрента | подтверждена | торренты вне этапа | — |
| движок `engine.py`, блоки и действия | язык правил | подтверждена; добавление действия «назначить диапазон» — открытый вопрос | — | — |
| действие `assign_voiceover` (сейчас пишет только тег имени файла `voiceover_tag`) | тег в имени файла | перепроектирована | имя озвучки строки, главнее имени от плеера и сайта (Д-37); дальше проходит через группы озвучек | `scan._vk_candidates`, `rules/formatter.py` |
| сброс кэша вызовами CRUD | кэш скомпилированных профилей | подтверждена; добавляется событие `rules.profile.changed` | раскладка пересчитывается сама (Т-14) | — |

## 4. Раскладка и скан (`modules/scan`, новый `modules/composition`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `scan._included_episodes` | правила к быстрому списку, ответ сопоставляется с номером парсера | удалена | раскладка | `scan._scan_web_video` |
| `scan._scan_web_video`, `scan._scan_vk` | отдельные ветки скана по виду источника | перепроектирована | синхронизация: таблицы источников → раскладка → план | `scan._run_series`, `scan.on_composition` |
| `scan._process_video_candidates`, `_vk_candidates` (вызов `rules.apply`) | правила к заголовкам кандидатов | перепроектирована | кандидаты — строки раскладки одиночных серий | — |
| `scan._resolve_part_ones` и повтор той же логики в `scan._reconstruct` | «часть 1» для серии, у которой есть части — в двух местах | перепроектирована | одно место: `composition/project.py` | `scan` |
| `scan._reconstruct` (`rules.apply` по сохранённым заголовкам) | номера для окна композиции вычисляются заново | удалена | `composition.get` | `scan.on_composition`, `gateway /composition` |
| `scan.on_composition` (`refresh` запускает скан) | окно композиции и скан в одном запросе | перепроектирована | окно читает раскладку и план; синхронизация — отдельная команда | `web/StatusComposition`, `web/StatusVkComposition` |
| `scan.save_availability`, `scan.availability`, таблица `video_availability` | отчёт о плеерах после скана | удалена | состояния строк таблицы, сводка `composition` | `gateway api_series`, `web` |
| `scan/planner.py` шаги 3–5 (склейки закрывают дыры и вытесняют одиночки) | склейка может стать референсом | перепроектирована для веб- и VK-источников | Т-21, Д-32: склейка не референс; серия, покрытая только склейкой, — ошибка «референс не подходит» | `scan._rebuild_plan` |
| `modules/slicing` (нарезка склейки VK по главам на серии) | работает со склейкой, стоящей в плане референса; на проде нарезанных файлов нет | подтверждена без изменений, в новой архитектуре не участвует | Д-34: код не трогается; склейка в план не попадает, нарезка не вызывается; вызовы из `scan.on_composition` уходят вместе с ним | `scan.on_composition` (`slicing.files.drop_for_source`, `slicing.verify`) |
| `plan_with_supersession`, `superseded_by` (Р-37) | замена низкого разрешения высоким | подтверждена | Т-24 | — |
| `core/ids.media_unique_id` (адрес + дата + сериал) | ключ кандидата | перепроектирована | ключ строки источника; скачанное сохраняет `unique_id` сопоставлением по адресу | `scan.repository`, `downloads` |

## 5. Озвучки (`modules/voices`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `voices._dispatch` (`sources.provider.info`, `sources.parse` с `links`, `plan_layout(manual, selected_source)`) | озвучки только у сайта с каталогом озвучек; референс — выбранный источник из адреса | перепроектирована | вход — раскладка и план референсов | `scan` (команда `voices.dispatch`) |
| `voices._reference_episodes` (серия озвучки = номер на сайте у референса) | озвучки идут мимо конструктора | удалена | серия озвучки — из раскладки по номерам конструктора | — |
| `voices/layout.py::plan_layout` (режимы `manual` и `auto`, основной источник по числу серий) | раскладка озвучек одного сайта | перепроектирована | авторежим удалён; основной плеер озвучки — по приоритету источников | `voices.on_layout`, `gateway.parse_url` |
| `voices/layout.py::choose_reference` | выбор референса по разрешению и приоритету | удалена | одно место выбора — `scan` | тесты |
| `voices/layout.py::_ranges` | диапазоны для окна | удалена | З-3 | — |
| `voices.layout` (запрос) | раскладка для окна добавления | удалена | `composition.get` | `gateway.parse_url` |
| `voices/identity.py` (`_fold`, `_SEPARATORS`, `tokens`, `voice_groups`, `group_key`, `same_voice`) | озвучки склеиваются без групп: по написанию и по составу имени «A / B» | перепроектирована | только группы пользователя (З-6); сравнение с триггером без регистра и пробелов сохраняется | `voices.layout`, `_adopted_keys`, тесты `test_voices_groups_effect` |
| `voices._adopted_keys` | ключ озвучки переживает смену правил | перепроектирована | ключ озвучки — группа либо «неизвестная N» из `composition` | — |
| `voice_dubs` (`site_episode`, `chain`, `temp_path` на строке серии) | файл озвучки принадлежит одной серии | перепроектирована | `voice_files`: файл строки источника, общий для серий диапазона (Т-18) | `voices.repository`, журнал очистки |
| `voices/editor.py`, `voices/settings.py` (`voices.groups`, `voices.player_groups`, `voices.source_priority`) | группы и порядок источников | подтверждена | данные групп и приоритета — у `voices` | — |
| `DUB_MAX_HEIGHT = 720`, conform, выход рядом, следование за референсом, журнал очистки, восстановление | Р-40 … Р-59 | подтверждена | Т-19, Т-27 | вход заменяется на раскладку |

## 6. Шлюз (`modules/gateway`)

| Точка | Сейчас | Статус | Новое место / причина |
|---|---|---|---|
| `POST /api/parse_url` (веб-ветка и вызов `voices.layout`) | шлюз собирает ответ из двух модулей | перепроектирована | торренты — как есть; веб-источник — `sources.table.get` и сводка `composition` |
| `POST /api/web_video/coverage`, `POST /api/web_video/layout` | отдельные чтения | удалена | таблица страницы |
| `POST /api/parser-profiles/scrape-titles` (ветвление VK / веб, повторный скан, фильтр по запросу) | бизнес-логика в шлюзе | перепроектирована | строки для конструктора из таблицы источника (`line_of`) |
| `POST /api/parser-profiles/test` | `rules.test` | перепроектирована | `rules.apply` |
| `GET /api/series/{id}/composition` | `scan.on_composition` | перепроектирована | `composition.get` + план `scan` |
| `GET /api/series/{id}` (`scan.availability`) | отчёт о плеерах | перепроектирована | сводка источников `composition` |
| `SSE_MAP`: `video_scan_progress`, `coverage_progress` | ход старых чтений | удалена | `sources_table_progress`, `sources_table_ready`, `composition_changed`, `reference_changed` |

## 7. Интерфейс (`web/src`)

| Точка | Сейчас | Статус | Новое место / причина |
|---|---|---|---|
| `components/WebSourcesEditor.vue`, `composables/useWebSources.ts`, `lib/webSources.ts` (`SOURCE_ORDER = vk, allvideo, vibix`) | источники только ruchime | перепроектирована | один компонент источников (Т-31) |
| `AddSeriesModal.vue`: выпадающий список озвучки dunhuatv, `playerReportRows`, блок «Озвучки» (`webLayout`) | отдельный экран под каждый сайт | перепроектирована | компонент источников; блок озвучек — раздел «Выход» |
| `status/StatusProperties.vue`, блок «Источники» | редактор ruchime | перепроектирована | компонент источников с добавлением и удалением источника |
| `status/StatusComposition.vue`, `status/StatusVkComposition.vue` | номера из `scan._reconstruct` | перепроектирована | раскладка и план; раздел «Выход» |
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
| `downloads/ytdlp.py` (`is_allvideo_url`, `_resolve_allvideo_stream`, `_extract_best_stream_from_playerjs`, ветка в `download_video`) | собственный разбор плеера AllVideo с заголовком Referer ruchime — копия `sources/players/allvideo.py` | удалена | поток кандидата разрешает только `sources.video.stream` (З-5) | `ytdlp.download_video`, тесты `test_downloads_module.py` (`test_is_allvideo_url` и тест с `_resolve_allvideo_stream`) |
| `RuchimeProvider.vibix` и `PLAYER_ORDER[2]` | два экземпляра плеера Vibix; браузер настроен только у одного | перепроектирована | один экземпляр плеера в реестре плееров `sources` | `ruchime`, `players.py` |
| `video_providers/table.py::ReleaseTable.fill` (исключение пробы → `Cell(False)` без `transient`), `ruchime/provider.py` (`_quick_report`), `views.py` (ячейка без ответа) | исключение пробы даёт «нет», хотя Т-5 требует «не получено» | перепроектирована | исключение пробы → состояние «не получено» с причиной | `ruchime`, `views` |
| `video_providers/__init__.py::_DEFAULT_REGISTRY`, `players.py::PLAYER_ORDER` | глобальные реестры сайтов и плееров | перепроектирована | реестры принадлежат модулю `sources`; регистрация по `ARCHITECTURE.md`, раздел 12 | `run.py`, `tests/conftest.py`, `tests/test_contracts.py` |
| `RuchimeProvider._tables`, `_table_locks`, `SURVEY_TTL_S`, `ReleaseTable.expires_at` | кэш таблицы в памяти по slug без учёта выбора в адресе | удалена | таблица в БД, срок 1 час, замок страницы (Т-2, Т-4) | `test_sources_ruchime_players.py` (проверка `_tables["test"].expires_at`) |
| `gateway/api_settings.py` (регулярное выражение ссылки VK, подстроки `vk.com`, `vkvideo.ru`, `@`), `web/src/lib/webSources.ts::SOURCE_ORDER`, `AddSeriesModal.vue` (метки плееров) | знание сайтов и плееров вне `sources` | перепроектирована | вид сайта по адресу — реестр сайтов `sources`; метка плеера — поле строки таблицы `player_label`; порядок плееров — настройка `voices.source_priority` | `scrape_titles`, `web` |
| `tests/test_retry.py` (три сторожа, читающие исходники по жёсткому списку путей) | ломаются при переименовании файлов | перепроектирована | список путей приводится к новой файловой раскладке | `tests/test_retry.py` |

### 9.2. Конструктор (`rules`)

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `rules/repository.py` (чтение таблицы `series`) | модуль читает таблицу другого модуля | удалена | профиль приходит параметром запроса (`profile_id`), у торрентов — из источника сериала | `rules.format_torrent_file`, `rules.format_filename` |
| `rules.format_filename` — вызывающие | `renaming` (4 вызова), `downloads` (1), `slicing/module.py:379` (1) | перепроектирована | единый контракт «по готовым номерам»; поля запроса `series`, `media_item`, `episode_override` сохраняются, `slicing` вызывает без правки (Д-34) | `renaming`, `downloads` |
| `rules.cache.invalidate` | топик без единого вызывающего | удалена | событие `rules.profile.changed` | — |

### 9.3. Потребители вида и профиля сериала

Поля `series.source_type` и `series.parser_profile_id` читают, кроме перечисленных в разделах 4–6, модули ниже. Все они переходят на `catalog.series.get`: `kind` (`torrent` либо `video`) и `sources` (список источников).

| Точка | Сейчас | Статус | Новое место / причина | Вызовы, которые меняются |
|---|---|---|---|---|
| `renaming/module.py` (108, 167, 174, 179) | вид и профиль сериала | перепроектирована | `kind`, профиль источника | — |
| `library/module.py` (147), `metadata/module.py` (175) | ветка «видео или торрент» по `source_type` | перепроектирована | `kind` | — |
| `downloads/module.py` (144, 654) | `source_type` решает, есть ли запасной путь yt-dlp, для всей серии | перепроектирована | решение по плееру кандидата, не по сериалу: в серии с несколькими источниками виды смешаны | `ytdlp`, тесты `test_downloads_module.py` |
| `torrents/module.py` (788), `rules/formatter.py` (103) | профиль торрента, расширение `.mp4` по виду | перепроектирована | профиль из источника; расширение по кандидату | — |
| `voices/module.py` (383) | пропуск не-web серий | перепроектирована | вход озвучек — раскладка | — |
| `gateway/api_series.py` (`list_series`, `add_series`, `series_details`, `update_series`, `set_state`), `api_media.py` (`composition`, `source_filenames`) | ветвление по виду, сборка ответов из нескольких модулей | перепроектирована | тело запросов с источниками; сборка ответов — в модулях-владельцах | `web`, `schema.d.ts` |
