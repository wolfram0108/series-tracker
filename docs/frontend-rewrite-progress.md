# Прогресс переписывания фронта — точка продолжения (handoff)

> Живой снимок для бесшовного продолжения после сжатия контекста.
> План/ТЗ — [docs/frontend-rewrite.md](frontend-rewrite.md) (Р-Ф1..Р-Ф12,
> фазы, §13 под-вехи Ф4). Обновлено: 2026-06-19 (Ф4: Add ✅ + статус-окно
> собрано; СЛЕДУЮЩЕЕ — конфигуратор «Фильтры VK», см. §5).

---

## 1. Где мы в плане

| Фаза | Статус |
|---|---|
| **Ф0** — типизация бэка (response_model 62 + OpenAPI + golden) | ✅ |
| **Ф1** — каркас `web/` (Vite+Vue3+TS+PrimeVue+Pinia) под `/v2` | ✅ |
| **Ф2** — токен-пресет + парити-галерея + окно настроек | ✅ |
| **Ф3** — API-слой (client+useSSE+сторы+диспетчер) | ✅ |
| **Ф4** — перенос экранов на живые данные | 🔄 **в работе** (см. §4) |
| Ф5 — приёмка паритета | ⏳ |
| Ф6 — cutover `/v2`→`/` | ⏳ |

**`/v2` теперь — реальное приложение** (не галерея): `App.vue` = главный
экран на живых данных стенда. Галерея сохранена в `Gallery.vue` (полигон,
не монтируется).

**РЕЖИМ РАБОТЫ (важно):** Ф4 ведётся **автономно по ТЗ** — пользователя
звать **только на визуальную приёмку** готовых экранов; техническое/
архитектурное/порядок под-вех решать самому (память
`feedback-autonomous-frontend-rewrite`). На приёмку зову скриншотом.

---

## 2. Операционка

**Стенд:** root-сессия; приложение под systemd от `user`. Node 20. Адрес:
`http://192.168.1.148:5000` (старый фронт `/`, новый `/v2`).

```bash
cd /home/user/series-tracker/web && npm run build   # → dist (vue-tsc strict!)
chown -R user:user /home/user/series-tracker/web     # root создаёт файлы — вернуть владельца
npm run test                                         # Vitest (юнит сторов)
# /v2 отдаётся сразу (gateway монтирует dist; рестарт не нужен для статики)
# Скриншот: Playwright firefox, /v2 — wait_until="domcontentloaded" (+timeout),
#   т.к. SSE /api/stream вечный → networkidle НЕ наступает (это норма, SSE жив).
sudo systemctl restart series-tracker                # после правок Python
```

**Дисциплина:** русский; без AI-атрибуции; **не вписывать реальные данные
стенда (логины/пароли) в коммиты** (классификатор блокирует); коммит на шаг.

---

## 3. Архитектура `web/src` (Ф3 + Ф4)

```
api/client.ts ─ openapi-fetch на schema.d.ts (62 маршрута)
composables/
  useApi.ts ─ request(): loading + ошибки→Toast (для компонентов)
  useSSE.ts ─ singleton, 1 EventSource /api/stream, 11 событий, on()/connect()
  useRealtime.ts ─ диспетчер SSE→сторы (вызывается в main.ts)
  useConfirm.ts ─ Promise-подтверждение (singleton state)
  useDropAnchor.ts ─ computeDropStyle(): высота выпадашек по содержимому, потолок до края экрана
stores/ (Pinia, setup-стиль)
  series.ts ─ список + merge series_updated + savingIds; series.test.ts (6 Vitest)
  queues.ts ─ 4 очереди (agent/torrents/downloads/slicing)
  scanner.ts ─ ScannerStatus + load
  indicators.ts ─ computed (monitoring/downloader/slicing) — см. находку §6
  ui.ts ─ activeSeriesId + viewing-цикл (open→['viewing'], close→[])
components/
  AppHeader.vue ─ заголовок + индикаторы + кнопки
  SeriesCard.vue ─ карточка на реальной Series (emit scan/delete/toggle/open-status)
  ModalShell.vue ─ оболочка модалок (оверлей, header/body/footer слоты, Esc/клик-вне)
  ConfirmDialog.vue ─ окно подтверждения (useConfirm)
  SettingsModal.vue ─ окно настроек (xl, фикс-высота) + вкладки-сегмент st-tabs;
                      вкладка 'parser' (Фильтры VK) — ЗАГЛУШКА (tab-stub), её и строим
  settings/SettingsAuth|Trackers|Agents|Debug.vue ─ вкладки
  LogsModal.vue ─ просмотр логов (фильтры + таблица)
  AddSeriesModal.vue ─ окно добавления (полное: parse_url, TMDB ru→en, качество, VK)
  SavedPathDropdown.vue ─ выбор сохранённого пути ({paths:[]}, +catalogName, useDropAnchor)
  StatusModal.vue ─ окно «Статус» (ModalShell xl фикс + st-tabs; вкладки по source_type)
  status/StatusProperties.vue ─ Свойства (торрент+VK, автопарсинг качества, TMDB)
  status/StatusComposition.vue ─ Композиция торрент (карточки card-torrent по сезонам)
  status/StatusVkComposition.vue ─ Композиция VK (3 типа карточек + DnD приоритет качества)
  status/StatusSlicing.vue ─ Нарезка (главы ffprobe, фильтр, нарезка) — VK
  status/StatusHistory.vue ─ История (таблицы торрент/VK)
  St*.vue ─ поля constructor-group (из Ф2); StSelect — высота через useDropAnchor
main.ts ─ bootstrap + setupRealtime() + load series/scanner; #gallery → Gallery.vue
Gallery.vue ─ парити-галерея Ф2 (доступна по /v2#gallery)
styles/ ─ tokens/overrides/fields/tables/pills/card/cards/progress/modal/layout/
          add-series/status/slicing.css
```

---

## 4. Ф4: статус под-вех

| Под-веха (§13) | Статус | Эндпоинты |
|---|---|---|
| **Главный экран** (список+карточки, live) | ✅ принят | GET /api/series, scan/toggle/delete |
| **Подтверждение** + ModalShell | ✅ | DELETE /api/series/{id}?delete_from_qb |
| **Настройки: Авторизация** | ✅ принят | GET/POST /api/auth |
| **Настройки: Трекеры** | ✅ принят | GET /api/trackers, PUT /api/trackers/{id} |
| **Настройки: Агенты** (очереди-карточки) | ✅ принят | queuesStore (SSE) |
| **Настройки: Отладка** (сканер+saved_paths) | ✅ принят | /api/scanner/settings, scan_all, /api/settings/saved_paths |
| **Просмотр логов** | ✅ | GET /api/logs?group&level&limit |
| **Add-модалка** (полный порт) | ✅ принят | parse_url, tmdb/search+details, parser-profiles, saved_paths, POST /api/series |
| ↳ SavedPathDropdown + качество (anilibria/astar) + VK + TMDB | ✅ принят | catalogName, sortEpisodeKeys, vk_search_mode |
| ↳ TMDB-имя ru→en→original (азиатские без ru) | ✅ | metadata.search +en-US → name_en |
| **Статус-модалка** (Props/Composition/Slicing/History) | ✅ собрана | оболочка как Настройки (xl, фикс, st-tabs) |
| ↳ Свойства (торрент+VK, автопарсинг качества, TMDB) | ✅ принят | POST /api/series/{id}, parse_url, tmdb |
| ↳ Композиция торрент (карточки файлов) | ✅ принят | composition, reprocess |
| ↳ История (торрент+VK таблицы) | ✅ принят | torrents/history, media-items |
| ↳ Композиция VK (карточки+DnD приоритет) | 🔁 построена, ждёт VK-данных | composition?refresh, sliced-files, rename_preview, ignore, ignored-seasons, vk-quality-priority, deep-adoption, reprocess_vk_files |
| ↳ Нарезка (главы ffprobe, фильтр, нарезка) | 🔁 построена, ждёт VK-данных | media-items, chapters(/filtered/mark-garbage), slice(/-with-filter), delete-source |
| **Конфигуратор Фильтров VK** (DnD) | ⏳ крупная | см. §5 |
| Отладка-доп (БД-просмотр/очистка/флаги) | ⏳ | /api/settings/{force_replace,less_strict_scan,...} |

Окно настроек: вкладки-сегмент (остров облегает, адаптивное схлопывание
в иконки @media, стабильная ширина призраком жирного текста, высота 39px),
фикс-высота окна 86vh (легаси-паттерн — не прыгает при переключении вкладок).

---

## 5. СЛЕДУЮЩАЯ ВЕХА — Конфигуратор «Фильтры VK» (точка старта на свежий контекст)

«Фильтры VK» = вкладка `parser` окна Настроек. Сейчас это **заглушка**
(`SettingsModal.vue` строка ~63, `tab-stub`). Строим всю вкладку.

**ПЕРВЫМ делом (на свежую голову, по уроку «портируй реальный CSS»):**
1. Снять скриншот эталона: старый фронт `/`, Настройки → «Фильтры VK»,
   все три шага (Профили / Тестирование / Редактор правил Шаг 2).
2. Прочитать ИСХОДНИК целиком: `static/js/components/settingsParser.js`
   (763 стр) и `static/css/components/rule-editor.css` (321 стр).
3. Запустить Explore-агента за порт-картой (как делали для
   addSeriesModal/seriesCompositionManager) — компонент крупный.

**Три шага вкладки:**
- **Профили** — выбор/создание/удаление профиля парсера; компоненты
  StGroup/StSelect уже есть. API: GET/POST/PUT/DELETE /api/parser-profiles[/{id}].
- **Тестирование** — ввод строк, прогон правил, карточки результата
  (стиль `card-final.card-test-result` уже в cards.css; пример — в
  Gallery.vue). API: POST /api/parser-profiles/test, scrape-titles.
- **Редактор правил (Шаг 2) — САМОЕ СЛОЖНОЕ, DnD:**
  - правило = `conditions[]` (ЕСЛИ: `_blocks[]` + AND/OR) + `actions[]`
    (ТО: `action_type` + `_action_blocks[]`).
  - блок = `{id, type, value?}`; 8 типов: text(ред.)/number/whitespace/
    any_text/start_of_line/end_of_line + add/subtract(ред., только ТО).
  - DnD: **vuedraggable@4** (в deps, уже юзаем в StatusVkComposition) +
    палитра clone; **contenteditable** для значений; CSS — порт
    `rule-editor.css`. Логика блоков (перенести 1:1): cloneBlock,
    isBlockEditable, getBlockDisplayText (number в ТО → «Число #N»),
    updateBlockValue, getBlockClasses.
  - Риски: contenteditable+v-model (двусторонняя синхронизация курсора),
    клонирование из палитры (vuedraggable :clone), коллизии id.

**Как подключить:** в `SettingsModal.vue` заменить `tab-stub` ветки
`tab==='parser'` на новый компонент `settings/SettingsParser.vue`
(под-вкладки шага реализовать внутри). Эндпоинты parser-profiles уже в
schema.d.ts. Образец DnD на vuedraggable@4 — `StatusVkComposition.vue`
(приоритет качества).

---

## 6. Находки и Ф0

- **УРОК (Add-модалка):** сперва собрал окно «по смыслу» (свои стили) —
  пользователь забраковал. Дизайн неприкосновенен: снимать скриншот
  исходного экрана, портировать РЕАЛЬНЫЙ CSS (`static/css/components/*`),
  сверять. См. память `feedback-port-real-css-not-reinvent`.
- **Находки по ходу Add:** (а) `GET /api/settings/saved_paths` отдаёт
  `{paths:[]}`, а не массив — в Отладке список был пуст (исправлено);
  (б) `modal-xl` с `height:86vh` распирал окно добавления — фикс-высоту
  вынес в `modal-fixed` (только настройки/логи), Add — по контенту;
  (в) `div-table`/`table-site-torrents` вообще не были портированы —
  перенёс в `tables.css`; (г) азиатские тайтлы без ru: TMDB кладёт в
  `name` иероглифы — добрал `name_en` (en-US) и правило ru→en→original.
- **Виджеты:** галерея согласованных форм (Ф2) доступна по `/v2#gallery`
  (Gallery.vue, не удалена). Выпадающие списки (StSelect/SavedPathDropdown)
  — высота по содержимому, потолок до края экрана (useDropAnchor).
- **НАХОДКА (Ф3):** `_updateIndicatorState` (hold-таймер 1000ms, на него
  ссылался §11 ТЗ) в исходном app.js **мёртвый** (не вызывается). Индикаторы
  мгновенные → `indicatorsStore` = computed-производные от очередей/сканера.
- **ТЕХ-ДОЛГ (бэкенд, не фронт):** `torrent_files.original_path` пишется
  лениво из листинга qBit в `renaming` (не write-once при download-complete),
  пристинное имя релиза может теряться в краях. НЕ регрессия переноса
  (старый код идентичен). Память `techdebt-original-path-not-immutable`.
  Видно во вкладке Композиция у серий «Извне»/«Рик и Морти» (заведены из
  уже-переименованных файлов на стенде).
- **БАГ ПЕРЕНОСА (VK-композиция) — НАЙДЕН И ИСПРАВЛЕН (доказано):** записи
  media_items создаёт СКРЕЙП VK-канала (GET `composition?refresh=true`),
  а НЕ `viewing`/`fs.sync` (тот лишь усыновляет уже существующие pending→
  completed по диску). Доказано на стенде: удалил записи серии #6 →
  POST viewing оставил 0; GET composition?refresh=true → 85 (создал+
  усыновил, сразу completed). В оригинале `seriesCompositionManager`
  `autoUpdateEnabled` **дефолт=true** (строки 241/543; false только
  get_all) → loadComposition(true) → авто-скрейп на открытии. Я в порте
  по ошибке поставил дефолт=false → мой интерфейс не скрейпил на открытии.
  **Исправлено** (StatusVkComposition.initialize: дефолт true, false для
  get_all, иначе localStorage). Доказано end-to-end: 0 записей → открыл
  вкладку в /v2 → авто-скрейп → 85 записей + 85 карточек.
- **Ф0:** `schemas.py` (62 response_model, extra="allow"), golden-харнесс
  `tests/api_golden.py`. Тесты: 195 passed + 6 Vitest (seriesStore merge).

---

## 7. Следующий шаг

1. ~~Доделать Add-модалку~~ ✅ принята (порт целиком + TMDB ru→en).
2. ~~Статус-модалка~~ ✅ собрана целиком (Свойства/Композиция торрент+VK/
   Нарезка/История). Торрент-путь принят; VK-композиция и Нарезка ждут
   визуальной приёмки на реальных VK-данных (у серии #6 «Сказание о
   пастухе богов» пока нет медиа-элементов — нужен скан/загрузка).
3. **Конфигуратор Фильтров VK** — крупная веха DnD (§5). СЛЕДУЮЩАЯ.
4. **Отладка-доп** + модалка DatabaseViewer (БД-просмотр/очистка/флаги).
5. Затем Ф5 (приёмка паритета + e2e Playwright) → Ф6 (cutover).

**Незакрытые инварианты для проверки в Ф5:** частичный merge series_updated
(есть), viewing-stop при закрытии статус-модалки (есть: App.onCloseStatus →
uiStore.closeStatus), одно SSE-соединение (есть), 15-мин scan
(AbortController — добавить при необходимости). Плюс: визуальная приёмка
VK-композиции и Нарезки на реальных данных (сейчас пусто).

---

## 8. Карта коммитов (ветка refactoring/bus)

```
Ф3: 254b43e client+useApi → 1176f1f useSSE → d015812 seriesStore+Vitest
    → ea811d6 сторы+диспетчер
Ф4: 6ec2e47 главный экран → 3d96aa2 ModalShell+подтверждение → d3b64a7
    окно настроек+Агенты → 694ec77 фикс-высота → 305c794 Авторизация →
    804cd7a Трекеры → 4fb844c Отладка → 8ee89c5 Логи → 8f93836 Add-каркас
Add: cefe0a4 фикс saved_paths → 02c486d полный порт Add → 0d112ca точный
    CSS-порт → 161082b выпадашки+отступ → fc015cb #gallery → c7c1ea0 TMDB-
    карточки → 2285721 высота выпадашек → 57d0f36 metadata name_en →
    cbe074d имя ru→en
Статус: d4c9427 вкладки как Настройки → 0db8ecc seasonless-секция →
    b376867 Свойства+качество → 67761bf Композиция торрент → 7d344ec
    История → 399c9f4 Композиция VK → 5ddf44c Нарезка
(ранее: Ф0 c31bf3c.., Ф1 ffef404, Ф2 92158e8..9043c45 + окно настроек
 2f9fa90..9185a3d)
```

---

## 9. Операционка для продолжения (быстрый старт после сжатия)

```bash
cd /home/user/series-tracker/web && npm run build   # vue-tsc strict + vite
chown -R user:user /home/user/series-tracker/web     # root создаёт файлы → вернуть владельца
# /v2/ отдаётся сразу (рестарт не нужен для статики). Python-правки: sudo systemctl restart series-tracker
```
- Скриншот на приёмку: Playwright firefox, `wait_until="domcontentloaded"`
  (вечный SSE мешает networkidle). Статус-окно: клик `[title='Статус']`
  на карточке; вкладки — `.st-tab`; VK-серия (#6) — `.last`.
- Дисциплина: русский; без AI-атрибуции; коммит на каждый согласованный
  шаг; **не сочинять CSS — портировать реальный** (память
  `feedback-port-real-css-not-reinvent`); VK-вкладки ждут реальных данных.
